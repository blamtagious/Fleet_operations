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
@page { size: Letter; margin: 0.6in 42pt 0.8in; }  /* 42pt + 12pt body padding = 0.75in */
:root { --font: Lato, "Helvetica Neue", Helvetica, Arial, sans-serif; --ink:#111827; --muted:#4b5563;
        --faint:#6b7280; --rule:#9ca3af; --panel:#e5e7eb; --accent:#00063d; }
* { box-sizing: border-box; }
body { font-family: var(--font); font-size: 10pt;
       line-height: 1.38; color: var(--ink); margin: 0; padding: 0 12pt;
       -webkit-print-color-adjust: exact; print-color-adjust: exact; }
.label { font-size: 7.5pt; font-weight: 600; letter-spacing: .08em; text-transform: uppercase;
         color: var(--muted); margin: 0 0 3pt; }
.num { font-variant-numeric: tabular-nums; text-align: right; white-space: nowrap; }

.header { display: flex; justify-content: space-between; align-items: flex-start;
          padding-bottom: 14pt; border-bottom: 0.5pt solid var(--rule); }
.header img { height: 56pt; width: auto; display: block; }
.title { text-align: right; }
.title h1 { margin: 0 0 7pt; font-size: 22pt; font-weight: 300; letter-spacing: -.01em;
            color: var(--accent); }
.meta { display: inline-grid; grid-template-columns: auto auto; gap: 2pt 18pt; text-align: left; margin: 0; }
.meta dt { font-size: 7.5pt; font-weight: 600; letter-spacing: .08em; text-transform: uppercase;
           color: var(--muted); align-self: center; }
.meta dd { margin: 0; font-weight: 500; text-align: right; }

.parties { display: grid; grid-template-columns: 1fr 260pt; gap: 0 36pt; margin-top: 16pt; }
.parties p { margin: 0; }
.parties .name { font-weight: 600; }
.parties .sub { color: var(--muted); }

table.items { width: 100%; border-collapse: collapse; margin-top: 20pt; }
table.items th { font-size: 7.5pt; font-weight: 600; letter-spacing: .08em; text-transform: uppercase;
                 color: var(--muted); text-align: left; padding: 0 0 6pt; }
table.items th.num { text-align: right; }
table.items td { padding: 4.5pt 0; vertical-align: top; }
table.items tr { break-inside: avoid; }
td.line { width: 34pt; color: var(--faint); }
tr.opt td { padding: 2.5pt 0; font-size: 9pt; }
tr.group td { padding: 11pt 0 1pt; font-size: 7.5pt; font-weight: 600; letter-spacing: .08em;
              text-transform: uppercase; color: var(--muted); }
.detail { white-space: pre-line; color: var(--muted); font-size: 8.3pt; line-height: 1.4;
          margin: 2pt 0 5pt; max-width: 4.9in; }
.item { font-weight: 600; }
.spec { color: var(--muted); font-size: 8.5pt; }
.code { color: var(--faint); font-weight: 400; }

.closing { break-inside: avoid; }
.totals { margin: 14pt 0 0 auto; width: 260pt; break-inside: avoid; }
.totals .row { display: flex; justify-content: space-between; padding: 2pt 0; }
.totals .row > span:first-child { color: var(--muted); }
.totals .grand { margin: 6pt -12pt 0; padding: 8pt 12pt; background: var(--panel); border-radius: 4pt;
                 align-items: baseline; }
.totals .grand > span:first-child { font-size: 8pt; font-weight: 600; letter-spacing: .08em;
                                    text-transform: uppercase; color: var(--ink); }
.totals .grand > span:last-child { font-size: 15pt; font-weight: 700; color: var(--accent);
                                   font-variant-numeric: tabular-nums; }

.notes { margin-top: 18pt; font-size: 8pt; line-height: 1.5; color: var(--muted); max-width: 5.6in; }
.accept { margin-top: 28pt; display: grid; grid-template-columns: 1fr 260pt; gap: 0 36pt;
          break-inside: avoid; }
.field { border-bottom: 0.5pt solid var(--rule); height: 26pt; }
.accept .label { margin-top: 5pt; }
.foot { position: fixed; bottom: 0; left: 12pt; right: 12pt; display: flex;
        justify-content: space-between; font-size: 7.5pt; color: var(--faint); }
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

    veh_rows = "\n".join(
        '<tr><td class="line">{line}</td><td><div class="item">{desc}</div>{detail}</td>'
        '<td class="num">{price}</td></tr>'.format(
            line=e(i.get("line", "")),
            desc=e(i.get("description", "")),
            detail=f'<div class="spec">{e(i.get("detail",""))}</div>' if i.get("detail") else "",
            price=money(i.get("price")),
        )
        for i in veh_items
    )

    def _acc_row(desc, code, price, details=""):
        extra = f'<div class="detail">{e(details.strip())}</div>' if (details or "").strip() else ""
        return (
            '<tr class="opt"><td class="line"></td><td>{desc}{code}{extra}</td><td class="num">{price}</td></tr>'
        ).format(desc=e(desc), code=code, price=money(price), extra=extra)

    def _code(i):
        return f' <span class="code">({e(i.get("code",""))})</span>' if i.get("code") else ""

    chosen = [i for i in opts if i.get("include", True)]
    rows = []
    if q.get("options_lump") and chosen:
        # One line for everything Ford builds in; upfits stay itemized below it.
        total = sum(float(i.get("price") or 0) for i in chosen)
        rows.append(_acc_row("Optional Factory Equipment", "", total))
    else:
        rows += [_acc_row(i.get("description", ""), _code(i), i.get("price")) for i in chosen]
    rows += [_acc_row(i.get("description", ""), _code(i), i.get("price"), i.get("details", ""))
             for i in upfits if i.get("include", True)]
    acc = rows
    acc_rows = "\n".join(rows)
    acc_block = (
        '<tr class="group"><td colspan="3">Options &amp; Accessories</td></tr>\n' + acc_rows
        if acc else ""
    )

    # Skip blank contact lines rather than printing empty rows on the quote.
    seller_name = e(s.get("name", ""))
    seller_lines = [
        f'<p class="name">{seller_name}</p>' if seller_name else "",
        f'<p class="sub">{e(s["title"])}</p>' if s.get("title") else "",
        f'<p class="sub">{e(s["email"])}</p>' if s.get("email") else "",
        f'<p class="sub">{e(s["phone"])}</p>' if s.get("phone") else "",
    ]
    seller_block = "\n      ".join(x for x in seller_lines if x)

    def row(label, value, cls=""):
        return f'<div class="row {cls}"><span>{label}</span><span class="num">{value}</span></div>'

    total_rows = [
        row("Vehicle", money(t.get("vehicle"))),
        row("Options &amp; Accessories", money(t.get("options"))),
    ]
    if t.get("upfitting"):
        total_rows.append(row("Upfitting", money(t.get("upfitting"))))
    if t.get("destination"):
        total_rows.append(row("Destination", money(t.get("destination"))))
    for adj in q.get("adjustments") or []:
        total_rows.append(row(e(adj.get("label")), money(adj.get("amount"))))
    if int(v.get("quantity") or 1) > 1:
        total_rows.append(row("Unit Total", money(t.get("unit_total"))))
        total_rows.append(row("Quantity", int(v.get("quantity"))))
    total_rows.append(
        f'<div class="row grand"><span>Total</span><span>{money(t.get("grand_total"))}</span></div>'
    )

    contact = e(c.get("name", ""))
    if c.get("title"):
        contact += f", {e(c.get('title'))}"
    fin = c.get("fin", "")
    cust_lines = [
        f'<p class="name">{e(c.get("org", ""))}</p>',
        f'<p class="sub">{contact}</p>' if contact else "",
        f'<p class="sub">FIN Code {e(fin)}</p>' if fin else "",
    ]
    cust_block = "\n      ".join(x for x in cust_lines if x)

    foot = " · ".join(
        x for x in (e(d.get("name", "Ford of Murfreesboro")), e(d.get("address1", "")),
                    e(d.get("address2", ""))) if x
    )

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>{e(q.get('quote_id',''))} — Sales Quote</title>
<style>{CSS}</style></head><body>

<div class="header">
  <img src="{_logo_data_uri()}" alt="Ford of Murfreesboro / Ford Pro">
  <div class="title">
    <h1>Sales Quote</h1>
    <dl class="meta">
      <dt>Quote ID</dt><dd>{e(q.get('quote_id',''))}</dd>
      <dt>Date</dt><dd>{e(q.get('quote_date',''))}</dd>
    </dl>
  </div>
</div>

<div class="parties">
  <div>
    <p class="label">Prepared for</p>
    {cust_block}
  </div>
  <div>
    <p class="label">Prepared by</p>
    {seller_block}
  </div>
</div>

<table class="items">
  <thead><tr><th>#</th><th>Description</th><th class="num">Price</th></tr></thead>
  <tbody>
    <tr class="group"><td colspan="3">Vehicle</td></tr>
    {veh_rows}
    {acc_block}
  </tbody>
</table>

<div class="closing">
<div class="totals">
  {"".join(total_rows)}
</div>

<div class="notes">{e(q.get('notes',''))}</div>

<div class="accept">
  <div><div class="field"></div><p class="label">Customer signature</p></div>
  <div><div class="field"></div><p class="label">Acceptance date</p></div>
</div>
</div>

<div class="foot"><span>{foot}</span><span>{e(q.get("quote_id",""))}</span></div>

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


def append_pdfs(out_path: str, extra: list) -> int:
    """Append every page of the `extra` PDFs to out_path. Returns pages added.

    Unreadable or encrypted files are skipped, so a bad attachment never
    stops the quote itself from being produced.
    """
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for page in PdfReader(out_path).pages:
        writer.add_page(page)
    added = 0
    for path in extra:
        try:
            reader = PdfReader(path)
            if reader.is_encrypted and not reader.decrypt(""):
                continue
            for page in reader.pages:
                writer.add_page(page)
                added += 1
        except Exception:
            continue
    if added:
        tmp = out_path + ".part"
        with open(tmp, "wb") as f:
            writer.write(f)
        os.replace(tmp, out_path)
    return added
