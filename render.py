"""Render a quote record to SWC-template HTML, and to PDF via headless Chrome."""

from __future__ import annotations

import base64
import html
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGO = os.path.join(HERE, "static", "img", "header-logo.png")

def _chrome_candidates() -> list:
    """Chrome/Edge locations, in preference order, for this OS."""
    if sys.platform == "darwin":
        return [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
    if os.name == "nt":
        pf = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        pf86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        local = os.environ.get("LOCALAPPDATA", "")
        out = []
        for base in (pf, pf86, local):
            if not base:
                continue
            out += [
                os.path.join(base, r"Google\Chrome\Application\chrome.exe"),
                os.path.join(base, r"Microsoft\Edge\Application\msedge.exe"),
            ]
        return out
    return [
        "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
        "/usr/bin/microsoft-edge", "/snap/bin/chromium",
    ]


def chrome_path() -> str | None:
    """First installed Chrome/Edge, or whatever FLEETQUOTE_CHROME points at."""
    override = os.environ.get("FLEETQUOTE_CHROME", "").strip()
    if override and os.path.isfile(override):
        return override
    for p in _chrome_candidates():
        if os.path.isfile(p):
            return p
    for name in ("google-chrome", "chromium", "chrome", "msedge"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _logo_data_uri() -> str:
    try:
        with open(LOGO, "rb") as f:
            return "data:image/png;base64," + base64.b64encode(f.read()).decode()
    except OSError:
        return ""


def money(v) -> str:
    if v is None or v == "":
        return ""
    try:
        return "${:,.2f}".format(float(v))
    except (TypeError, ValueError):
        return str(v)


def e(s) -> str:
    return html.escape("" if s is None else str(s))


def _rows(items, cols, empty_rows=0):
    out = []
    for it in items:
        out.append(
            "<tr>"
            + "".join(f'<td class="{c[1]}">{c[2](it)}</td>' for c in cols)
            + "</tr>"
        )
    for _ in range(empty_rows):
        out.append("<tr>" + "".join(f'<td class="{c[1]}">&nbsp;</td>' for c in cols) + "</tr>")
    return "\n".join(out)


CSS = """
@page { size: Letter; margin: 0.5in 0.6in; }
* { box-sizing: border-box; }
body { font-family: Arial, Helvetica, sans-serif; font-size: 9.5pt; color: #000;
       margin: 0; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
.header { display: flex; align-items: flex-start; justify-content: space-between; }
.header img { width: 260px; height: auto; }
.title { font-size: 26pt; letter-spacing: .5px; margin: 6pt 0 0 0; text-align: right; }
.meta { margin-top: 6pt; display: flex; justify-content: space-between; align-items: flex-start; }
.dealer { line-height: 1.35; }
.boxes { width: 300px; }
table.kv { border-collapse: collapse; width: 100%; margin-bottom: 10pt; }
table.kv td { border: 1px solid #000; padding: 2pt 5pt; height: 16pt; }
table.kv td.k { width: 92px; font-size: 9pt; }
.quotedby { border: 1px solid #000; padding: 4pt 5pt; line-height: 1.35; }
.quotedby .lbl { text-decoration: underline; display: block; margin-bottom: 2pt;
                 border-bottom: 1px solid #000; margin: -4pt -5pt 4pt -5pt; padding: 2pt 5pt; }
h2.sec { background: #000; color: #fff; font-size: 10pt; font-weight: bold;
         margin: 16pt 0 0 0; padding: 3pt 6pt; }
table.grid { border-collapse: collapse; width: 100%; }
table.grid th, table.grid td { border: 1px solid #000; padding: 3pt 5pt; vertical-align: top; }
table.grid th { font-size: 9pt; font-weight: normal; text-align: center; }
td.line, th.line { width: 52px; text-align: center; }
td.price, th.price { width: 118px; text-align: right; }
td.desc .code { color: #444; font-size: 8pt; }
tr.tot td { border: none; padding: 3pt 5pt; }
tr.tot td.lbl { text-align: right; font-weight: bold; }
tr.tot td.val { text-align: right; width: 118px; border-top: 1px solid #000; font-weight: bold; }
tr.sub td.val { font-weight: normal; }
.totals { margin-top: 8pt; width: 100%; border-collapse: collapse; }
.notes { margin-top: 18pt; font-size: 8pt; line-height: 1.45; }
.sign { margin-top: 30pt; display: flex; gap: 40px; }
.sign div { flex: 1; border-top: 1px solid #000; padding-top: 3pt; font-size: 8.5pt; }
.small { font-size: 8pt; color: #333; }
"""


def render_html(q: dict) -> str:
    c = q.get("customer", {}) or {}
    s = q.get("seller", {}) or {}
    v = q.get("vehicle", {}) or {}
    d = q.get("dealer", {}) or {}
    t = q.get("totals", {}) or {}

    veh_items = v.get("lines") or []
    opts = q.get("options") or []
    upfits = q.get("upfits") or []

    veh_rows = _rows(
        veh_items,
        [
            ("Line", "line", lambda i: e(i.get("line", ""))),
            ("Description", "desc", lambda i: (
                f'<strong>{e(i.get("description",""))}</strong>'
                + (f'<div class="code">{e(i.get("detail",""))}</div>' if i.get("detail") else "")
            )),
            ("Price", "price", lambda i: money(i.get("price"))),
        ],
        empty_rows=max(0, 1 - len(veh_items)),
    )

    acc = [i for i in (list(opts) + list(upfits)) if i.get("include", True)]
    acc_rows = _rows(
        acc,
        [
            ("Description", "desc", lambda i: (
                e(i.get("description", ""))
                + (f' <span class="code">({e(i.get("code",""))})</span>' if i.get("code") else "")
            )),
            ("Price", "price", lambda i: money(i.get("price"))),
        ],
        empty_rows=max(0, 1 - len(acc)),
    )

    # Skip blank contact lines rather than printing empty rows on the quote.
    seller_lines = []
    who = e(s.get("name", ""))
    if s.get("title"):
        who = f"{who} - {e(s['title'])}" if who else e(s["title"])
    for line in (who, e(s.get("email", "")), e(s.get("phone", ""))):
        if line.strip():
            seller_lines.append(line)
    seller_block = "<br>\n      ".join(seller_lines) or "&nbsp;"

    qty = int(v.get("quantity") or 1)
    qty_block = ""
    if qty > 1:
        qty_block = (
            f'<tr class="tot"><td class="lbl">Unit Total</td>'
            f'<td class="val">{money(t.get("unit_total"))}</td></tr>'
            f'<tr class="tot"><td class="lbl">Quantity</td>'
            f'<td class="val">{qty}</td></tr>'
        )

    extra_rows = ""
    for row in q.get("adjustments") or []:
        extra_rows += (
            f'<tr class="tot sub"><td class="lbl">{e(row.get("label"))}</td>'
            f'<td class="val">{money(row.get("amount"))}</td></tr>'
        )

    fin = c.get("fin", "")
    fin_row = f'<tr><td class="k">FIN Code</td><td>{e(fin)}</td></tr>' if fin else ""

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>{e(q.get('quote_id',''))} — Sales Quote</title>
<style>{CSS}</style></head><body>

<div class="header">
  <img src="{_logo_data_uri()}" alt="Ford of Murfreesboro / Ford Pro">
  <div style="flex:1"><h1 class="title">SALES QUOTE</h1></div>
</div>

<div class="meta">
  <div class="dealer">
    <strong>{e(d.get('name','Ford of Murfreesboro'))}</strong><br>
    {e(d.get('address1',''))}<br>
    {e(d.get('address2',''))}
  </div>
  <div class="boxes">
    <table class="kv">
      <tr><td class="k">Quote Date</td><td>{e(q.get('quote_date',''))}</td></tr>
      <tr><td class="k">Quote ID</td><td>{e(q.get('quote_id',''))}</td></tr>
    </table>
    <div class="quotedby">
      <span class="lbl">Quoted By:</span>
      {seller_block}
    </div>
  </div>
</div>

<h2 class="sec">Customer</h2>
<table class="grid">
  <tr><td class="k" style="width:92px">Organization</td><td>{e(c.get('org',''))}</td></tr>
  <tr><td class="k">Contact</td><td>{e(c.get('name',''))}{', ' + e(c.get('title','')) if c.get('title') else ''}</td></tr>
  {fin_row}
</table>

<h2 class="sec">Vehicle Information</h2>
<table class="grid">
  <tr><th class="line">Line</th><th>Description</th><th class="price">Price</th></tr>
  {veh_rows}
</table>

<h2 class="sec">Options &amp; Accessories</h2>
<table class="grid">
  <tr><th>Description</th><th class="price">Price</th></tr>
  {acc_rows}
</table>

<table class="totals">
  <tr class="tot sub"><td class="lbl">Vehicle</td><td class="val">{money(t.get('vehicle'))}</td></tr>
  <tr class="tot sub"><td class="lbl">Options &amp; Accessories</td><td class="val">{money(t.get('options'))}</td></tr>
  {f'<tr class="tot sub"><td class="lbl">Upfitting</td><td class="val">{money(t.get("upfitting"))}</td></tr>' if t.get('upfitting') else ''}
  {f'<tr class="tot sub"><td class="lbl">Destination</td><td class="val">{money(t.get("destination"))}</td></tr>' if t.get('destination') else ''}
  {extra_rows}
  {qty_block}
  <tr class="tot"><td class="lbl">TOTAL</td><td class="val">{money(t.get('grand_total'))}</td></tr>
</table>

<div class="notes">{e(q.get('notes',''))}</div>

<div class="sign">
  <div>Customer Signature</div>
  <div>Acceptance Date</div>
</div>

</body></html>"""


def render_pdf(q: dict, out_path: str) -> str:
    """Write quote.pdf next to quote.json using headless Chrome."""
    chrome = chrome_path()
    if not chrome:
        raise RuntimeError(
            "PDF export needs Google Chrome (or Microsoft Edge) installed. "
            "Install Chrome, or set the FLEETQUOTE_CHROME environment variable "
            "to the full path of chrome.exe."
        )

    html_text = render_html(q)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "quote.html")
        with open(src, "w", encoding="utf-8") as f:
            f.write(html_text)
        if os.path.exists(out_path):
            os.remove(out_path)

        cmd = [
            chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
            # Containers give /dev/shm only 64MB, which crashes the renderer.
            "--disable-dev-shm-usage",
            "--no-first-run", "--no-default-browser-check",
            "--disable-extensions", "--no-pdf-header-footer",
            f"--user-data-dir={os.path.join(tmp, 'ud')}",
            f"--print-to-pdf={out_path}", "--virtual-time-budget=4000",
            "file://" + src,
        ]
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        # Chrome sometimes writes the PDF but lingers, so poll for the artifact
        # and stop waiting once it is complete.
        deadline = time.time() + 60
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            if os.path.isfile(out_path) and os.path.getsize(out_path) > 1024:
                time.sleep(0.4)
                if os.path.getsize(out_path) > 1024:
                    break
            time.sleep(0.2)

        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

        if not os.path.isfile(out_path) or os.path.getsize(out_path) < 1024:
            err = ""
            try:
                err = (proc.stderr.read() or "")[-800:]
            except Exception:
                pass
            raise RuntimeError(f"Chrome failed to produce a PDF. {err}")
    return out_path
