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


# ---- FOM Fleet design system (customer documents: light theme on white) ----
FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "fonts")
_FONTS = (
    ("Barlow Semi Condensed", 600, "barlow-semi-condensed-latin-600-normal.woff2"),
    ("Barlow Semi Condensed", 700, "barlow-semi-condensed-latin-700-normal.woff2"),
    ("IBM Plex Sans", 400, "ibm-plex-sans-latin-400-normal.woff2"),
    ("IBM Plex Sans", 500, "ibm-plex-sans-latin-500-normal.woff2"),
    ("IBM Plex Sans", 600, "ibm-plex-sans-latin-600-normal.woff2"),
    ("IBM Plex Mono", 400, "ibm-plex-mono-latin-400-normal.woff2"),
    ("IBM Plex Mono", 500, "ibm-plex-mono-latin-500-normal.woff2"),
)
_font_css_cache: str | None = None


def font_css() -> str:
    """@font-face rules with the files embedded, so they load in any context."""
    global _font_css_cache
    if _font_css_cache is None:
        out = []
        for fam, weight, fn in _FONTS:
            try:
                with open(os.path.join(FONT_DIR, fn), "rb") as f:
                    data = base64.b64encode(f.read()).decode()
            except OSError:
                continue
            out.append(f'@font-face {{ font-family: "{fam}"; font-weight: {weight}; font-style: normal; '
                       f'src: url(data:font/woff2;base64,{data}) format("woff2"); }}')
        _font_css_cache = "\n".join(out)
    return _font_css_cache


CSS = """
/* First page runs the header bar edge to edge; later pages get a top margin. */
@page { size: Letter; margin: 24px 0 80px; }
@page :first { margin-top: 0; }
:root {
  --canvas: #fafafa; --surface: #ffffff; --subtle: #f2f4f7;
  --line: #e4e7eb; --line-strong: #cbd2d9;
  --ink: #1f2933; --primary: #3e5c76; --accent: #c85a32;
  --text: #1f2933; --text-2: #52606d;
  --display: "Barlow Semi Condensed", "Arial Narrow", sans-serif;
  --sans: "IBM Plex Sans", -apple-system, BlinkMacSystemFont, sans-serif;
  --mono: "IBM Plex Mono", Consolas, monospace;
  --s1: 4px; --s2: 8px; --s4: 16px; --s6: 24px;
}
* { box-sizing: border-box; }
html, body { margin: 0; }
body { font-family: var(--sans); font-size: 15px; line-height: 22px; color: var(--text); background: var(--surface);
       padding: 0 0.75in; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
.label { font-size: 12px; line-height: 16px; font-weight: 600; letter-spacing: .05em; text-transform: uppercase;
         color: var(--text-2); margin: 0 0 var(--s1); }
.code { font-family: var(--mono); font-size: 13px; line-height: 20px; color: var(--text-2); }
.num { font-variant-numeric: tabular-nums; text-align: right; white-space: nowrap; }

/* header bar: brand-ink, 3px copper rule */
.bar { margin: 0 -0.75in; background: var(--ink); border-bottom: 3px solid var(--accent);
       padding: var(--s4) 0.75in; display: flex; align-items: center; gap: var(--s4); }
.bar .name { font-family: var(--display); font-weight: 700; font-size: 20px; line-height: 24px; color: #fff; }
.bar .tag { font-family: var(--mono); font-size: 13px; line-height: 20px; color: #fff; background: var(--primary);
            border-radius: 4px; padding: var(--s1) var(--s2); }

.head { display: flex; justify-content: space-between; align-items: flex-start; margin-top: var(--s6); }
.head h1 { margin: 0; font-family: var(--display); font-weight: 700; font-size: 36px; line-height: 40px; }
.meta { display: flex; gap: var(--s6); margin-top: var(--s4); }
.meta .v { font-weight: 500; }
.head img { height: 56px; width: auto; display: block; }

.parties { display: grid; grid-template-columns: 1fr 280px; gap: 0 var(--s6); margin-top: var(--s6); }
.parties p { margin: 0; }
.parties .name { font-weight: 600; }
.parties .sub { color: var(--text-2); }

.tablewrap { margin-top: var(--s6); border: 1px solid var(--line); border-radius: 8px; }
table.items { width: 100%; border-collapse: collapse; }
table.items thead th { background: var(--subtle); border-top: 1px solid var(--line-strong); border-bottom: 1px solid var(--line-strong);
  padding: var(--s2) var(--s4); font-size: 12px; line-height: 16px; font-weight: 600; letter-spacing: .05em;
  text-transform: uppercase; color: var(--text-2); text-align: left; }
table.items thead th:first-child { border-top-left-radius: 8px; border-left: 0; }
table.items thead th:last-child { border-top-right-radius: 8px; }
table.items th.num { text-align: right; }
table.items td { padding: var(--s2) var(--s4); vertical-align: top; border-bottom: 1px solid var(--line); font-size: 13px; line-height: 20px; }
table.items tr:last-child td { border-bottom: 0; }
table.items tr { break-inside: avoid; }
td.line { width: 48px; font-family: var(--mono); font-size: 12px; color: var(--text-2); }
tr.group td { background: var(--surface); padding-top: var(--s4); padding-bottom: var(--s1); font-size: 12px; line-height: 16px;
              font-weight: 600; letter-spacing: .05em; text-transform: uppercase; color: var(--text-2); border-bottom: 0; }
.item { font-weight: 600; }
.spec { color: var(--text-2); font-size: 12px; line-height: 16px; }
.spec .code, .detail .code, td .code { font-size: 12px; line-height: 16px; }
.detail { white-space: pre-line; color: var(--text-2); font-size: 12px; line-height: 16px; margin-top: var(--s1); max-width: 4.6in; }

.closing { break-inside: avoid; }
.totals { width: 280px; margin: var(--s4) 0 0 auto; background: var(--subtle); border: 1px solid var(--line-strong);
          border-radius: 8px; padding: var(--s4); break-inside: avoid; }
.totals .row { display: flex; justify-content: space-between; gap: var(--s4); line-height: 24px; }
.totals .row > span:first-child { color: var(--text-2); }
.totals .grand { margin-top: var(--s2); padding-top: var(--s2); border-top: 1px solid var(--line-strong); align-items: baseline; }
.totals .grand > span:first-child { font-size: 12px; font-weight: 600; letter-spacing: .05em; text-transform: uppercase; color: var(--text); }
.totals .grand > span:last-child { font-family: var(--display); font-weight: 700; font-size: 22px; line-height: 28px;
                                   color: var(--primary); font-variant-numeric: tabular-nums; }
.notes { margin-top: var(--s4); font-size: 13px; line-height: 20px; color: var(--text-2); max-width: 5.4in; }
.accept { margin-top: var(--s4); display: grid; grid-template-columns: 1fr 280px; gap: 0 var(--s6); break-inside: avoid; }
.field { border-bottom: 1px solid var(--line-strong); height: 32px; }
.accept .label { margin-top: var(--s2); }
.foot { position: fixed; bottom: 0; left: 0.75in; right: 0.75in; display: flex; justify-content: space-between;
        font-size: 13px; line-height: 20px; color: var(--text-2); }

/* Density tiers: render_pdf starts at t0 and steps down until the quote fits one page. */
.t1 .bar { padding-top: var(--s2); padding-bottom: var(--s2); }
.t1 .head { margin-top: var(--s4); }
.t1 .head h1 { font-size: 28px; line-height: 32px; }
.t1 .head img { height: 48px; }
.t1 .meta { margin-top: var(--s2); }
.t1 .parties, .t1 .tablewrap { margin-top: var(--s4); }
.t1 table.items td { padding-top: var(--s1); padding-bottom: var(--s1); line-height: 18px; }
.t1 tr.group td { padding-top: var(--s2); }
.t1 .totals { margin-top: var(--s2); padding: var(--s2) var(--s4); }
.t1 .notes, .t1 .accept { margin-top: var(--s2); }
.t1 .field { height: 24px; }

.t2 .head h1 { font-size: 24px; line-height: 28px; }
.t2 .head { margin-top: var(--s2); }
.t2 .head img { height: 40px; }
.t2 .parties p { line-height: 18px; }
.t2 table.items td { padding-top: 2px; padding-bottom: 2px; font-size: 12px; line-height: 16px; }
.t2 .spec, .t2 .detail { font-size: 11px; line-height: 14px; }
.t2 .totals .row { line-height: 20px; }
.t2 .notes { font-size: 12px; line-height: 16px; }

/* t3: plain options sit two to a row */
.t3 .head h1 { font-size: 24px; line-height: 28px; }
.t3 .head { margin-top: var(--s2); }
.t3 .head img { height: 40px; }
.t3 .parties p { line-height: 18px; }
.t3 table.items td { padding-top: 2px; padding-bottom: 2px; font-size: 12px; line-height: 16px; }
.t3 .spec, .t3 .detail { font-size: 11px; line-height: 14px; }
.t3 .totals .row { line-height: 20px; }
.t3 .notes { font-size: 12px; line-height: 16px; }
.t2 .totals, .t3 .totals { width: 320px; }
.t3 td.half { width: 50%; }
.t3 td.half + td.num { border-left: 0; }
.t3 td.gap { padding: 0; width: 16px; }
"""


def _fmt_date(s: str) -> str:
    """'10/14/2026' -> 'Oct 14, 2026' (the design system's date style in text)."""
    import datetime as _dt
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            d = _dt.datetime.strptime((s or "").strip(), fmt)
            return f"{d.strftime('%b')} {d.day}, {d.year}"
        except ValueError:
            continue
    return s or ""


def render_html(q: dict, tier: int = 0) -> str:
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
            '<tr><td class="line"></td><td>{desc}{code}{extra}</td><td class="num">{price}</td></tr>'
        ).format(desc=e(desc), code=code, price=money(price), extra=extra)

    def _code(i):
        return f' <span class="code">{e(i.get("code",""))}</span>' if i.get("code") else ""

    chosen = [i for i in opts if i.get("include", True)]
    rows = []
    plain = []   # (description, code html, price) lines with no long description
    if q.get("options_lump") and chosen:
        # One line for everything Ford builds in; upfits stay itemized below it.
        total = sum(float(i.get("price") or 0) for i in chosen)
        rows.append(_acc_row("Optional Factory Equipment", "", total))
    else:
        plain += [(i.get("description", ""), _code(i), i.get("price")) for i in chosen]
    detailed = []
    for i in upfits:
        if not i.get("include", True):
            continue
        if (i.get("details") or "").strip():
            detailed.append(_acc_row(i.get("description", ""), _code(i), i.get("price"), i.get("details", "")))
        else:
            plain.append((i.get("description", ""), _code(i), i.get("price")))
    if tier >= 3 and len(plain) > 1:
        # Two plain lines per row; descriptions and prices keep their own columns.
        for k in range(0, len(plain), 2):
            a = plain[k]
            b = plain[k + 1] if k + 1 < len(plain) else None
            cells = (f'<td class="half">{e(a[0])}{a[1]}</td><td class="num">{money(a[2])}</td>')
            cells += (f'<td class="gap"></td><td class="half">{e(b[0])}{b[1]}</td><td class="num">{money(b[2])}</td>'
                      if b else '<td colspan="3"></td>')
            rows.append(f'<tr class="pair"><td class="line"></td>{cells}</tr>')
    else:
        rows += [_acc_row(d_, c_, p_) for d_, c_, p_ in plain]
    paired = tier >= 3 and len(plain) > 1
    if paired:
        wide = lambda r: r.replace('<td class="line"></td><td>', '<td class="line"></td><td colspan="4">', 1)
        rows = [wide(r) if 'class="pair"' not in r else r for r in rows]
        detailed = [wide(r) for r in detailed]
        veh_rows = veh_rows.replace('<td><div class="item">', '<td colspan="4"><div class="item">')
    rows += detailed
    colspan = 6 if paired else 3
    desc_th = '<th colspan="4">Description</th>' if paired else "<th>Description</th>"
    acc_block = (
        f'<tr class="group"><td colspan="{colspan}">Options &amp; accessories</td></tr>\n' + "\n".join(rows)
        if rows else ""
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
        row("Options &amp; accessories", money(t.get("options"))),
    ]
    if t.get("upfitting"):
        total_rows.append(row("Upfitting", money(t.get("upfitting"))))
    if t.get("destination"):
        total_rows.append(row("Destination", money(t.get("destination"))))
    for adj in q.get("adjustments") or []:
        total_rows.append(row(e(adj.get("label")), money(adj.get("amount"))))
    if int(v.get("quantity") or 1) > 1:
        total_rows.append(row("Unit total", money(t.get("unit_total"))))
        total_rows.append(row("Quantity", int(v.get("quantity"))))
    total_rows.append(
        f'<div class="row grand"><span>Total</span><span>{money(t.get("grand_total"))}</span></div>'
    )

    contact = e(c.get("name", ""))
    if c.get("title"):
        contact += f", {e(c.get('title'))}"
    cust_lines = [
        f'<p class="name">{e(c.get("org", ""))}</p>',
        f'<p class="sub">{contact}</p>' if contact else "",
    ]
    cust_block = "\n      ".join(x for x in cust_lines if x)

    foot = "Ford of Murfreesboro &middot; 1550 N.W. Broad St., Murfreesboro, TN 37129"
    qid = e(q.get("quote_id", ""))

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>{qid} — Sales Quotation</title>
<style>{font_css()}
{CSS}</style></head><body class="t{tier}">

<div class="bar"><span class="name">Ford of Murfreesboro Fleet</span><span class="tag">Sales Quotation</span></div>

<div class="head">
  <div>
    <h1>Sales Quotation</h1>
    <div class="meta">
      <div><p class="label">Quote ID</p><span class="code" style="color:var(--text)">{qid}</span></div>
      <div><p class="label">Date</p><span class="v">{e(_fmt_date(q.get('quote_date','')))}</span></div>
    </div>
  </div>
  <img src="{_logo_data_uri()}" alt="Ford of Murfreesboro / Ford Pro">
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

<div class="tablewrap">
<table class="items">
  <thead><tr><th>#</th>{desc_th}<th class="num">Price</th></tr></thead>
  <tbody>
    <tr class="group"><td colspan="{colspan}">Vehicle</td></tr>
    {veh_rows}
    {acc_block}
  </tbody>
</table>
</div>

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

<div class="foot"><span>{foot}</span><span class="code">{qid}</span></div>

</body></html>"""


def _pages(path: str) -> int:
    try:
        from pypdf import PdfReader
        return len(PdfReader(path).pages)
    except Exception:
        return 1


def render_pdf(q: dict, out_path: str) -> str:
    """Write quote.pdf next to quote.json using headless Chrome.

    A quote is meant to be one page. Start at the roomy layout and step down
    through tighter ones (t1..t3) until it fits; a very long quote that still
    spills over keeps the tightest layout and runs to a second page.
    """
    for tier in range(4):
        html_to_pdf(render_html(q, tier), out_path)
        if _pages(out_path) <= 1:
            break
    return out_path


def html_to_pdf(html_text: str, out_path: str) -> str:
    """Print an HTML document to a PDF file with headless Chrome."""
    chrome = chrome_path()
    if not chrome:
        raise RuntimeError(
            "PDF export needs Google Chrome (or Microsoft Edge) installed. "
            "Install Chrome, or set the FLEETQUOTE_CHROME environment variable "
            "to the full path of chrome.exe."
        )

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
