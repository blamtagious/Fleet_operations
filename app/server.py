#!/usr/bin/env python3
"""Fleet quote builder -- local web app.

    python3 app/server.py [--port 8733] [--no-open]

Serves a drag-and-drop UI at http://127.0.0.1:8733 that reads Ford spec
proposals, prices them against the State of Tennessee bid sheet, and writes
finished quotes into the quotes/ and upfit/ directory trees.

Binds to loopback only; uploads are handled as base64 JSON, not multipart.
"""

from __future__ import annotations

import sys

# This app lives inside a shared Google Drive folder. Compiled .pyc caches would
# sync to everyone and churn constantly, so don't write them.
sys.dont_write_bytecode = True

import argparse
import base64
import json
import mimetypes
import os
import posixpath
import re
import subprocess
import tempfile
import threading
import urllib.parse
import webbrowser
from html import escape as html_escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib import (accessories, auth, build, config, finlookup, identity, pricing,  # noqa: E402
                 render, specparse, store)

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
ROOT = os.path.dirname(HERE)
# Uploads stage to local temp, never inside the Drive folder -- otherwise every
# dropped PDF would sync to the whole team before it was even used.
STAGING = os.path.join(tempfile.gettempdir(), "fleetquote-staging")

MAX_UPLOAD = 40 * 1024 * 1024

# Paths reachable without a session when logins are switched on.
PUBLIC_PATHS = {"/login", "/healthz"}
# Reachable while a temporary password is still in force.
PASSWORD_PATHS = {"/account", "/logout"}

ACCOUNT_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Your password | Fleet Quote Builder</title><style>
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px;
 background:#f2f4f7;font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;color:#10141a}
form{background:#fff;border:1px solid #d8dee6;border-radius:12px;padding:28px 30px;width:100%;max-width:380px;
 box-shadow:0 10px 40px rgba(16,26,38,.10)}
h1{font-size:18px;margin:0 0 4px}
p.sub{margin:0 0 20px;color:#5c6673;font-size:13px}
label{display:block;font-size:11.5px;text-transform:uppercase;letter-spacing:.5px;color:#5c6673;margin:0 0 4px}
input{width:100%;padding:9px 11px;border:1px solid #d8dee6;border-radius:7px;font:inherit;margin-bottom:14px}
input:focus{outline:2px solid #e8f1fb;border-color:#066fd1}
button{width:100%;padding:10px;border:0;border-radius:7px;background:#00274d;color:#fff;
 font:inherit;font-weight:600;cursor:pointer}
button:hover{filter:brightness(1.15)}
.err{background:#fdecec;border:1px solid #f3c9cb;color:#a4232b;border-radius:7px;
 padding:9px 11px;font-size:13px;margin-bottom:16px}
.ok{background:#e6f4ec;border:1px solid #bfe3cd;color:#0a7d3f;border-radius:7px;
 padding:9px 11px;font-size:13px;margin-bottom:16px}
.note{background:#fdf3e2;border:1px solid #f0dcb8;color:#9a5b00;border-radius:7px;
 padding:9px 11px;font-size:13px;margin-bottom:16px}
a.back{display:block;text-align:center;margin-top:14px;font-size:13px;color:#0a5fb4}
</style></head><body><form method="POST" action="/account">
<h1>Choose your password</h1>
<p class="sub">__WHO__</p>
__MSG__
<label for="c">Current password</label>
<input id="c" name="current" type="password" autocomplete="current-password" autofocus>
<label for="n">New password</label>
<input id="n" name="new" type="password" autocomplete="new-password">
<label for="n2">New password again</label>
<input id="n2" name="again" type="password" autocomplete="new-password">
<button type="submit">Save password</button>
__BACK__
</form></body></html>"""

LOGIN_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in | Fleet Quote Builder</title><style>
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px;
 background:#f2f4f7;font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;color:#10141a}
form{background:#fff;border:1px solid #d8dee6;border-radius:12px;padding:28px 30px;width:100%;max-width:360px;
 box-shadow:0 10px 40px rgba(16,26,38,.10)}
h1{font-size:18px;margin:0 0 4px}
p.sub{margin:0 0 20px;color:#5c6673;font-size:13px}
label{display:block;font-size:11.5px;text-transform:uppercase;letter-spacing:.5px;color:#5c6673;margin:0 0 4px}
input{width:100%;padding:9px 11px;border:1px solid #d8dee6;border-radius:7px;font:inherit;margin-bottom:14px}
input:focus{outline:2px solid #e8f1fb;border-color:#066fd1}
button{width:100%;padding:10px;border:0;border-radius:7px;background:#00274d;color:#fff;
 font:inherit;font-weight:600;cursor:pointer}
button:hover{filter:brightness(1.15)}
.err{background:#fdecec;border:1px solid #f3c9cb;color:#a4232b;border-radius:7px;
 padding:9px 11px;font-size:13px;margin-bottom:16px}
</style></head><body><form method="POST" action="/login">
<h1>Fleet Quote Builder</h1>
<p class="sub">Ford of Murfreesboro &middot; Fleet</p>
__ERR__
<label for="u">Username</label><input id="u" name="username" autocomplete="username" autofocus>
<label for="p">Password</label><input id="p" name="password" type="password" autocomplete="current-password">
<button type="submit">Sign in</button>
</form></body></html>"""


UPLOAD_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Reference files | Fleet Quote Builder</title><style>
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px;
 background:#f2f4f7;font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;color:#10141a}
.card{background:#fff;border-radius:14px;padding:28px;width:100%;max-width:520px;box-shadow:0 8px 30px rgba(16,26,38,.1)}
h1{font-size:20px;margin:0 0 6px}p{margin:0 0 18px;color:#4a5663}
.row{border:1px dashed #b8c2cc;border-radius:10px;padding:14px;margin-bottom:12px}
.row b{display:block}.st{font-size:13px;margin:2px 0 8px}.ok{color:#0a6f39}.no{color:#a45c06}
button{background:#00274d;color:#fff;border:0;border-radius:8px;padding:9px 16px;font:inherit;cursor:pointer}
a{color:#0a5fb4}.msg{margin-top:10px;font-size:14px}
</style></head><body><div class="card">
<h1>Reference files</h1>
<p>The two spreadsheets the quote builder prices from. Choose a file to replace what is on the server.</p>
__ROWS__
<div class="row"><b>Ford Accessories catalog (parts department sheet)</b>
<div class="st __ACCCLS__">__ACCST__</div>
<input type="file" id="f_acc" accept=".xlsx"> <button onclick="importAcc()">Import</button>
<div class="st" style="margin-top:8px">Adds every priced bed cover, running board and floor liner to the upfit library, so they show up as suggestions on matching trucks. Uploading a newer sheet updates prices and removes dropped parts.</div></div>
<div class="msg" id="msg"></div>
<p style="margin-top:16px"><a href="/">Back to the quote builder</a></p>
</div><script>
async function up(kind){
  const f=document.getElementById('f_'+kind).files[0], m=document.getElementById('msg');
  if(!f){m.textContent='Choose a file first.';return;}
  m.textContent='Uploading '+f.name+'...';
  const r=await fetch('/api/ref-upload?kind='+kind,{method:'POST',body:f});
  const j=await r.json().catch(()=>({ok:false,error:'Unexpected reply'}));
  m.textContent=j.ok?'Saved. Reload the quote builder.':'Failed: '+j.error;
  if(j.ok) setTimeout(()=>location.reload(),900);
}
async function importAcc(){
  const f=document.getElementById('f_acc').files[0], m=document.getElementById('msg');
  if(!f){m.textContent='Choose the accessories workbook first.';return;}
  m.textContent='Importing...';
  const r=await fetch('/api/accessories-import',{method:'POST',body:f});
  const j=await r.json().catch(()=>({ok:false,error:'Unexpected reply'}));
  m.innerHTML=j.ok?('Imported '+j.imported+' parts'+(j.removed?', removed '+j.removed+' old':'')+'.'+
    (j.warnings.length?'<br><b>Check the sheet:</b><br>'+j.warnings.join('<br>'):'')):'Failed: '+j.error;
  if(j.ok&&!j.warnings.length) setTimeout(()=>location.reload(),1200);
}
</script></body></html>"""

REF_FILES = {
    "pricing": ("State bid pricing (TT Murfreesboro Ford Pricing.xlsx)", lambda: config.PRICING_XLSX),
    "fin": ("FIN codes (FIN Codes.xlsx)", lambda: config.FIN_XLSX),
}


class Handler(BaseHTTPRequestHandler):
    server_version = "FleetQuote/1.0"

    # ---------------- plumbing ----------------

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s\n" % (fmt % args))

    def _send(self, code, body=b"", ctype="application/octet-stream", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, default=str), "application/json; charset=utf-8")

    def _err(self, msg, code=400):
        self._json({"ok": False, "error": str(msg)}, code)

    # ---------------- sessions ----------------

    def _cookie(self, name):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return ""

    def _user(self):
        """The signed-in user, or None. Always None when auth is switched off."""
        if not config.AUTH_REQUIRED:
            return None
        try:
            return auth.read_session(self._cookie("fq_session"))
        except RuntimeError:
            return None

    def _gate(self, path) -> bool:
        """True when the request may proceed."""
        if not config.AUTH_REQUIRED or path in PUBLIC_PATHS:
            return True
        row = self._user()
        if row:
            # A temporary password gets you exactly one place: the page that
            # replaces it.
            if row.get("must_change") and path not in PASSWORD_PATHS \
                    and not path.startswith("/static/"):
                if path.startswith("/api/"):
                    self._json(
                        {"ok": False, "error": "Set your own password first."}, 403
                    )
                else:
                    self._redirect("/account")
                return False
            return True
        if path.startswith("/api/"):
            self._json({"ok": False, "error": "Session expired. Reload to sign in."}, 401)
        else:
            self._redirect("/login")
        return False

    def _redirect(self, to, cookie=None):
        self.send_response(303)
        self.send_header("Location", to)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _login_page(self, error=""):
        page = LOGIN_PAGE.replace(
            "__ERR__", f'<div class="err">{html_escape(error)}</div>' if error else ""
        )
        self._send(200, page, "text/html; charset=utf-8")

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        if n > MAX_UPLOAD:
            raise ValueError("Upload too large.")
        return json.loads(self.rfile.read(n) or b"{}")

    # ---------------- routing ----------------

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            return self._send(200, "ok", "text/plain")
        if path == "/login":
            if self._user():
                return self._redirect("/")
            return self._login_page()
        if path == "/logout":
            return self._redirect("/login", "fq_session=; Path=/; Max-Age=0; SameSite=Lax")
        if path == "/account":
            if not self._user():
                return self._redirect("/login")
            return self._account_page()
        if not self._gate(path):
            return
        query = {}
        if "?" in self.path:
            from urllib.parse import parse_qs, unquote

            query = {k: unquote(v[0]) for k, v in parse_qs(self.path.split("?", 1)[1]).items()}
        try:
            if path == "/" or path == "/index.html":
                return self._static("index.html")
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            if path == "/upload":
                return self._upload_page()
            if path == "/api/bootstrap":
                return self._json(
                    {
                        "ok": True,
                        "sellers": build.sellers(),
                        "settings": build.settings(),
                        "years": pricing.years(),
                        "quotes": store.list_quotes(50),
                        "upfits": store.list_upfits(with_usage=True),
                        "identity": self._me(),
                        "next_id": store.next_quote_id(
                            None, self._me().get("initials", "")
                        ),
                    }
                )
            if path == "/api/fin":
                return self._json({"ok": True, "results": finlookup.search(query.get("q", ""))})
            if path == "/api/pricing":
                return self._json(
                    {
                        "ok": True,
                        "results": pricing.search(query.get("q", ""), query.get("year", "")),
                    }
                )
            if path == "/api/identity":
                return self._json({"ok": True, "identity": self._me()})
            if path == "/api/quotes":
                return self._json({"ok": True, "quotes": store.list_quotes()})
            if path == "/api/quote":
                q = store.load_quote(query.get("id", ""))
                return self._json({"ok": bool(q), "quote": q})
            if path == "/api/upfits":
                return self._json(
                    {"ok": True, "upfits": store.list_upfits(with_usage=True)}
                )
            if path == "/api/file":
                return self._serve_file(query.get("path", ""))
            if path == "/api/backup":
                return self._backup()
            return self._err("Not found", 404)
        except Exception as exc:  # surfaced in the UI
            return self._err(exc, 500)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/login":
            return self._do_login()
        if path == "/account":
            return self._do_account()
        if not self._gate(path):
            return
        if path in ("/api/ref-upload", "/api/accessories-import"):
            try:
                return self._ref_upload() if path == "/api/ref-upload" else self._accessories_import()
            except Exception as exc:  # a legible message beats a dropped connection
                return self._err(f"{type(exc).__name__}: {exc}", 500)
        try:
            data = self._body()
            if path == "/api/parse-spec":
                return self._parse_spec(data)
            if path == "/api/identity":
                if config.AUTH_REQUIRED:
                    return self._err(
                        "Your initials come from your login. Ask Jason to change them.", 403
                    )
                me = identity.save(data.get("initials", ""), data.get("seller_id", ""))
                return self._json({"ok": True, "identity": me})
            if path == "/api/recalc":
                return self._json({"ok": True, "quote": build.recalc(data.get("quote", {}))})
            if path == "/api/save":
                q = build.recalc(data.get("quote", {}))
                saved = store.save_quote(q, data.get("spec_files") or [])
                return self._json({"ok": True, "quote": saved})
            if path == "/api/pdf":
                return self._pdf(data)
            if path == "/api/upfit-save":
                return self._upfit_save(data)
            if path == "/api/upfit-delete":
                ok = store.delete_upfit(data.get("id", ""))
                return self._json(
                    {"ok": ok, "upfits": store.list_upfits(with_usage=True),
                     "error": "" if ok else "That upfit no longer exists."}
                )
            if path == "/api/upfit-file-delete":
                rec = store.delete_upfit_file(data.get("id", ""), data.get("file", ""))
                return self._json(
                    {"ok": bool(rec), "upfit": rec,
                     "upfits": store.list_upfits(with_usage=True)}
                )
            if path == "/api/upfit-match":
                return self._json(
                    {"ok": True, "upfits": store.applicable_upfits(data.get("spec", {}))}
                )
            if path == "/api/reveal":
                return self._reveal(data.get("path", ""))
            return self._err("Not found", 404)
        except Exception as exc:
            import traceback

            traceback.print_exc()
            return self._err(exc, 500)

    # ---------------- handlers ----------------

    def _me(self) -> dict:
        """Who is quoting. Hosted: the signed-in account. Local: the machine."""
        row = self._user()
        if row:
            return {
                "initials": (row.get("initials") or "").upper(),
                "seller_id": row.get("seller_id", ""),
                "name": row.get("name", ""),
                "username": row.get("username", ""),
                "configured": bool(row.get("initials")),
                "suggested_initials": "",
                "hosted": True,
            }
        me = identity.load()
        me["hosted"] = False
        return me

    def _account_page(self, error="", ok=""):
        row = self._user() or {}
        must = row.get("must_change")
        msg = ""
        if error:
            msg = f'<div class="err">{html_escape(error)}</div>'
        elif ok:
            msg = f'<div class="ok">{html_escape(ok)}</div>'
        elif must:
            msg = ('<div class="note">You are signed in with a temporary '
                   'password. Choose your own to carry on.</div>')
        page = (ACCOUNT_PAGE
                .replace("__WHO__", html_escape(
                    f"Signed in as {row.get('name') or row.get('username','')}"))
                .replace("__MSG__", msg)
                .replace("__BACK__", "" if must
                         else '<a class="back" href="/">Back to the quote builder</a>'))
        return self._send(200, page, "text/html; charset=utf-8")

    def _do_account(self):
        row = self._user()
        if not row:
            return self._redirect("/login")
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(min(n, 8192)).decode("utf-8", "replace")
        form = {}
        for pair in raw.split("&"):
            k, _, v = pair.partition("=")
            form[k] = urllib.parse.unquote_plus(v)

        if not auth.authenticate(row["username"], form.get("current", "")):
            return self._account_page(error="That current password is not right.")
        new = form.get("new", "")
        if new != form.get("again", ""):
            return self._account_page(error="The two new passwords do not match.")
        if auth.verify_password(new, row.get("password_hash", "")):
            return self._account_page(
                error="That is the same password you are already using."
            )
        try:
            auth.set_password(row["username"], new)
        except ValueError as exc:
            return self._account_page(error=str(exc))
        return self._account_page(ok="Password changed. You can carry on.")

    def _do_login(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(min(n, 8192)).decode("utf-8", "replace")
        form = {}
        for pair in raw.split("&"):
            k, _, v = pair.partition("=")
            form[k] = urllib.parse.unquote_plus(v)
        row = auth.authenticate(form.get("username", ""), form.get("password", ""))
        if not row:
            return self._login_page("That username and password did not match.")
        token = auth.make_session(row["username"])
        secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
        return self._redirect(
            "/",
            f"fq_session={token}; Path=/; Max-Age={auth.SESSION_DAYS * 86400}; "
            f"HttpOnly; SameSite=Lax{secure}",
        )

    def _upload_page(self):
        rows = []
        for kind, (label, getter) in REF_FILES.items():
            path = getter()
            if os.path.isfile(path):
                st = f'<div class="st ok">On the server ({os.path.getsize(path) // 1024} KB)</div>'
            else:
                st = '<div class="st no">Missing</div>'
            rows.append(
                f'<div class="row"><b>{html_escape(label)}</b>{st}'
                f'<input type="file" id="f_{kind}" accept=".xlsx"> '
                f'<button onclick="up(\'{kind}\')">Upload</button></div>'
            )
        n = sum(1 for u in store.list_upfits() if u.get("upfitter") == accessories.UPFITTER)
        page = (UPLOAD_PAGE.replace("__ROWS__", "\n".join(rows))
                .replace("__ACCCLS__", "ok" if n else "no")
                .replace("__ACCST__", f"{n} parts in the library" if n else "Not imported yet"))
        return self._send(200, page, "text/html; charset=utf-8")

    def _accessories_import(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_UPLOAD:
            return self._err("Empty or too large a file.")
        blob = self.rfile.read(n)
        if blob[:2] != b"PK":
            return self._err("That is not an .xlsx workbook.")
        os.makedirs(STAGING, exist_ok=True)
        tmp = os.path.join(STAGING, "accessories.xlsx")
        with open(tmp, "wb") as f:
            f.write(blob)
        try:
            res = accessories.import_workbook(tmp)
        finally:
            os.remove(tmp)
        if not res["imported"]:
            return self._err("No priced rows found. Expected the BED COVERS, RUNNING BOARDS and FLOOR MATS sheets.")
        return self._json({"ok": True, **res})

    def _ref_upload(self):
        kind = (urllib.parse.parse_qs(self.path.partition("?")[2]).get("kind") or [""])[0]
        if kind not in REF_FILES:
            return self._err("Unknown file type.")
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_UPLOAD:
            return self._err("Empty or too large a file.")
        blob = self.rfile.read(n)
        if blob[:2] != b"PK":
            return self._err("That is not an .xlsx workbook.")
        dest = REF_FILES[kind][1]()
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = dest + ".part"
        with open(tmp, "wb") as f:
            f.write(blob)
        os.replace(tmp, dest)
        (pricing if kind == "pricing" else finlookup).load(force=True)
        return self._json({"ok": True})

    def _static(self, rel):
        rel = posixpath.normpath(rel).lstrip("./")
        full = os.path.join(STATIC, rel)
        if not os.path.abspath(full).startswith(os.path.abspath(STATIC)) or not os.path.isfile(full):
            return self._err("Not found", 404)
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as f:
            return self._send(200, f.read(), ctype)

    def _stage(self, filename, b64):
        os.makedirs(STAGING, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(filename or "upload.pdf"))
        dest = os.path.join(STAGING, safe)
        with open(dest, "wb") as f:
            f.write(base64.b64decode(b64 or ""))
        return dest

    def _parse_spec(self, data):
        files = data.get("files") or []
        if not files:
            return self._err("No file received.")
        out = []
        for item in files:
            path = item.get("path") or self._stage(item.get("name"), item.get("data"))
            if not os.path.isfile(path):
                return self._err(f"Missing file: {path}")
            spec = specparse.parse(path).to_dict()
            me = self._me()
            quote = build.draft_from_spec(spec, data.get("year", ""))
            quote["quote_id"] = store.next_quote_id(
                quote.get("contract_year"), me.get("initials", "")
            )
            if me.get("seller_id"):
                seller = next(
                    (x for x in build.sellers() if x.get("id") == me["seller_id"]), None
                )
                if seller:
                    quote["seller"] = seller
            out.append(
                {
                    "source": path,
                    "source_name": os.path.basename(path),
                    "spec": spec,
                    "quote": quote,
                    "applicable_upfits": store.applicable_upfits(spec),
                }
            )
        return self._json({"ok": True, "results": out})

    def _pdf(self, data):
        q = build.recalc(data.get("quote", {}))
        saved = store.save_quote(q, data.get("spec_files") or [])
        out = os.path.join(saved["_dir"], "quote.pdf")
        render.render_pdf(saved, out)
        attached = 0
        if saved.get("attach_spec", True):
            spec_dir = os.path.join(saved["_dir"], "spec")
            extra = sorted(
                os.path.join(spec_dir, f) for f in (os.listdir(spec_dir) if os.path.isdir(spec_dir) else [])
                if f.lower().endswith(".pdf")
            )
            attached = render.append_pdfs(out, extra)
        return self._json({"ok": True, "quote": saved, "pdf": out, "attached_pages": attached})

    def _upfit_save(self, data):
        rec = data.get("upfit") or {}
        sources = []
        for item in data.get("files") or []:
            sources.append(item.get("path") or self._stage(item.get("name"), item.get("data")))
        saved = store.save_upfit(rec, sources, data.get("original_id", ""))
        return self._json(
            {"ok": True, "upfit": saved, "upfits": store.list_upfits(with_usage=True)}
        )

    def _backup(self):
        """Everything in quotes/ and upfit/ as one zip, for the Drive copy."""
        import io
        import zipfile

        skip = {".DS_Store", "Thumbs.db", "desktop.ini"}
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for label, base in (("quotes", store.QUOTES_DIR), ("upfit", store.UPFIT_DIR)):
                for dirpath, _d, files in os.walk(base):
                    for fn in files:
                        if fn in skip or fn.endswith(".tmp"):
                            continue
                        full = os.path.join(dirpath, fn)
                        z.write(full, os.path.join(label, os.path.relpath(full, base)))
        data = buf.getvalue()
        stamp = __import__("datetime").date.today().isoformat()
        return self._send(
            200, data, "application/zip",
            {"Content-Disposition": f'attachment; filename="fleetquote-{stamp}.zip"'},
        )

    @staticmethod
    def _under_allowed_root(full: str) -> bool:
        """Quotes live on the data volume, which is not under the app dir
        once this is hosted, so both roots have to be allowed."""
        roots = {os.path.abspath(config.DATA_DIR), os.path.abspath(ROOT)}
        return any(
            full == r or full.startswith(r + os.sep) for r in roots
        )

    def _serve_file(self, path):
        full = os.path.abspath(path)
        if not self._under_allowed_root(full) or not os.path.isfile(full):
            return self._err("Not found", 404)
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as f:
            return self._send(
                200, f.read(), ctype,
                {"Content-Disposition": f'inline; filename="{os.path.basename(full)}"'},
            )

    def _reveal(self, path):
        if config.AUTH_REQUIRED:
            return self._err(
                "Opening a folder only works on the copy running on your own computer.", 400
            )
        full = os.path.abspath(path)
        if not self._under_allowed_root(full) or not os.path.exists(full):
            return self._err("Not found", 404)
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", "-R", full])
            elif os.name == "nt":
                if os.path.isdir(full):
                    os.startfile(full)  # noqa: S606
                else:
                    subprocess.Popen(["explorer", "/select,", os.path.normpath(full)])
            else:
                subprocess.Popen(["xdg-open", full if os.path.isdir(full) else os.path.dirname(full)])
        except Exception as exc:
            return self._err(f"Could not open the folder: {exc}")
        return self._json({"ok": True})


def _ours(port: int) -> bool:
    """Is an instance of THIS app already answering on that port?"""
    import urllib.request

    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/bootstrap", timeout=1.5
        ) as r:
            return b'"ok"' in r.read(400)
    except Exception:
        return False


def _bind(port: int, tries: int = 12, host: str = "127.0.0.1"):
    """Bind the first free port at or after `port`.

    Returns (server, port, already_running_port). If the app is already up on
    a port we simply point the browser at it instead of failing.
    """
    for offset in range(tries):
        p = port + offset
        try:
            return ThreadingHTTPServer((host, p), Handler), p, None
        except OSError as exc:
            if exc.errno not in (48, 98, 10048):  # address already in use
                raise
            if _ours(p):
                return None, p, p
            continue
    raise SystemExit(
        f"  Could not find a free port between {port} and {port + tries - 1}.\n"
        "  Restart the computer, or start it with:  python3 app/server.py --port 9000"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT") or 8733))
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()

    # Listen on loopback unless a deployment explicitly says otherwise. A
    # container has to accept traffic on its published interface, so the
    # platform sets FLEETQUOTE_BIND; nothing binds beyond this machine by
    # default, and logins are required whenever it does.
    bind = os.environ.get("FLEETQUOTE_BIND", "").strip() or "127.0.0.1"
    if bind != "127.0.0.1" and not config.AUTH_REQUIRED:
        raise SystemExit(
            "  Refusing to listen beyond this machine without logins.\n"
            "  Set FLEETQUOTE_AUTH=1 and FLEETQUOTE_SECRET, then try again."
        )

    os.makedirs(store.QUOTES_DIR, exist_ok=True)
    os.makedirs(store.UPFIT_DIR, exist_ok=True)

    srv, port, already = _bind(args.port, host=bind)
    url = f"http://127.0.0.1:{port}/"

    if already is not None:
        print(f"\n  The quote builder is already running  ->  {url}")
        print("  Opening it in your browser. Nothing else to do.\n")
        if not args.no_open:
            webbrowser.open(url)
        return

    if bind != "127.0.0.1":
        print(f"\n  Fleet Quote Builder listening on {bind}:{port}")
        print(f"  Data    : {store.QUOTES_DIR}")
        print("  Logins  : required\n")
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
        return

    me = identity.load()
    print(f"\n  Fleet Quote Builder  ->  {url}")
    print(f"  Quotes  : {store.QUOTES_DIR}")
    print(f"  Upfits  : {store.UPFIT_DIR}")
    print(f"  You     : {me['initials'] or 'not set yet (the page will ask)'}")
    if port != args.port:
        print(f"  Note    : port {args.port} was busy, so this one is on {port}.")
    if not render.chrome_path():
        print("\n  !! Google Chrome was not found, so PDF export will not work.")
        print("     Install Chrome, then start this again.")
    print("\n  Leave this window open while you work. Close it to stop.\n")

    if not args.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped.")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # a legible message beats a traceback
        import traceback

        print("\n  The quote builder could not start.\n")
        print(f"  {type(exc).__name__}: {exc}\n")
        print("  Details for Jason:")
        traceback.print_exc()
        raise SystemExit(1)
