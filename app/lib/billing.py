"""Billing paperwork: the fleet billing cover sheet and the customer invoices.

Same look as the sales quote (see render.py). Both are rendered from plain data
sent by the Billing page, so the on-screen preview and the PDF are one template.
"""

from __future__ import annotations

import base64
import datetime as dt
import os

from . import render
from .render import e

LOGO = os.path.join(os.path.dirname(render.LOGO), "header-logo.png")

# Same system as the sales quote (render.py): header bar, type, table and totals box.
CSS = render.CSS + """
@page inv { margin: 0 0 80px; }
section.inv { page: inv; }
section.inv + section.inv { break-before: page; }
.meta { flex-wrap: wrap; row-gap: var(--s2); }
.terms { margin-top: var(--s4); display: grid; grid-template-columns: 1fr 280px; gap: 0 var(--s6); break-inside: avoid; }
.terms p { margin: 0; }
.due { font-weight: 600; color: var(--primary); }
.thanks { margin-top: var(--s6); font-size: 13px; color: var(--text-2); }
.blank { color: var(--line-strong); }
"""

DEALER = "Ford of Murfreesboro"
ADDRESS = ("1550 NW Broad St.", "Murfreesboro, TN 37129")
PHONE = "(888) 505-4898"


def _n(v) -> float:
    try:
        return float(str(v or "").replace(",", "").replace("$", "").strip() or 0)
    except ValueError:
        return 0.0


def _money(v) -> str:
    if not str(v or "").strip():
        return ""
    n = _n(v)
    return ("-" if n < 0 else "") + "${:,.2f}".format(abs(n))


def _mdy(iso: str) -> str:
    try:
        return dt.date.fromisoformat(iso).strftime("%m/%d/%Y")
    except ValueError:
        return iso or ""


def _long(iso: str) -> str:
    try:
        d = dt.date.fromisoformat(iso)
        return f"{d.strftime('%B')} {d.day}, {d.year}"
    except ValueError:
        return ""


def _logo() -> str:
    try:
        with open(LOGO, "rb") as f:
            return "data:image/png;base64," + base64.b64encode(f.read()).decode()
    except OSError:
        return ""


def _v(x) -> str:
    x = (x or "").strip() if isinstance(x, str) else x
    return e(x) if x else '<span class="blank">&mdash;</span>'


def _text_date(iso: str) -> str:
    return render._fmt_date(iso) if iso else ""


def _page(body: str, foot: str) -> str:
    return (f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>{e(foot)}</title>'
            f'<style>{render.font_css()}\n{CSS}</style></head><body>{body}'
            f'<div class="foot"><span>{e(DEALER)} &middot; 1550 N.W. Broad St., Murfreesboro, TN 37129</span>'
            f'<span>{e(foot)}</span></div></body></html>')


def _bar(tag: str) -> str:
    return f'<div class="bar"><span class="name">Ford of Murfreesboro Fleet</span><span class="tag">{e(tag)}</span></div>'


def _meta(pairs) -> str:
    cells = "".join(
        f'<div><p class="label">{e(k)}</p>{v}</div>' for k, v in pairs
    )
    return f'<div class="meta">{cells}</div>'


def _val(text, code=False) -> str:
    text = (text or "").strip()
    if not text:
        return '<span class="blank">&mdash;</span>'
    return f'<span class="code" style="color:var(--text)">{e(text)}</span>' if code else f'<span class="v">{e(text)}</span>'


def cover_html(d: dict) -> str:
    vehs = d.get("vehicles") or []
    total = sum(_n(v.get("price")) for v in vehs)

    def veh_row(i, v):
        extra = ""
        if v.get("vin"):
            extra += f'<div class="spec"><span class="code">VIN {e(v["vin"])}</span></div>'
        if v.get("invoiceNo"):
            extra += f'<div class="spec">Invoice # <span class="code">{e(v["invoiceNo"])}</span></div>'
        return (f'<tr><td class="line">{e(v.get("stock") or "")}</td>'
                f'<td><div class="item">{e(v.get("description") or f"Vehicle {i + 1}")}</div>{extra}</td>'
                f'<td class="num">{_money(v.get("price"))}</td></tr>')

    def lines(title, rows):
        rows = [r for r in rows if r.get("desc") or r.get("cost")]
        if not rows:
            return ""
        body = "".join(
            f'<tr><td class="line"></td><td>{e(r.get("desc") or "")}</td><td class="num">{_money(r.get("cost"))}</td></tr>'
            for r in rows)
        return f'<tr class="group"><td colspan="3">{title}</td></tr>{body}'

    veh_body = "".join(veh_row(i, v) for i, v in enumerate(vehs)) or \
        '<tr><td colspan="3" style="color:var(--text-2)">No vehicles added</td></tr>'
    extras = lines("We owe(s)", d.get("weOwes") or []) + lines("Adjustments / programs", d.get("adjs") or [])
    notes = (d.get("notes") or "").strip()

    body = f"""
{_bar("Billing cover sheet")}
<div class="head">
  <div><h1>Billing cover sheet</h1>
    {_meta([("Date", _val(_text_date(d.get("date", "")))), ("Fleet P.O. #", _val(d.get("fleetPO"), True)),
            ("Salesperson", _val(d.get("salesperson"))), ("Units", f'<span class="v">{len(vehs)}</span>')])}
  </div>
  <img src="{_logo()}" alt="">
</div>
<div class="parties">
  <div><p class="label">Bill to</p>
    <p class="name">{_v(d.get("company"))}</p>
    <p class="sub">{_v(d.get("address"))}</p><p class="sub">{_v(d.get("csz"))}</p>
    <p class="sub">FIN code {_val(d.get("fin"), True)}</p></div>
  <div><p class="label">Contact</p>
    <p class="name">{_v(d.get("contactName"))}</p>
    <p class="sub">{_v(d.get("contactPhone"))}</p><p class="sub">{_v(d.get("contactEmail"))}</p></div>
</div>
<div class="tablewrap">
<table class="items">
  <thead><tr><th style="width:96px">Stock #</th><th>Description</th><th class="num">Selling price</th></tr></thead>
  <tbody>{veh_body}{extras}</tbody>
</table>
</div>
<div class="closing">
<div class="totals">
  <div class="row grand"><span>Total</span><span>{_money(total) if total else ""}</span></div>
</div>
{f'<div class="notes" style="white-space:pre-line"><p class="label">Notes</p>{e(notes)}</div>' if notes else ""}
</div>
"""
    return _page(body, "Billing cover sheet")


def invoice_html(d: dict, veh: dict, number: int = 0) -> str:
    dd = d.get("date") or ""
    try:
        due = (dt.date.fromisoformat(dd) + dt.timedelta(days=30)).isoformat() if dd else ""
    except ValueError:
        due = ""
    price = _money(veh.get("price"))
    return f"""<section class="inv">
{_bar("Invoice")}
<div class="head">
  <div><h1>Invoice</h1>
    {_meta([("Invoice #", _val(veh.get("invoiceNo"), True)), ("Invoice date", _val(_text_date(dd))),
            ("PO number", _val(d.get("fleetPO"), True)), ("Date due", _val(_text_date(due))),
            ("Sales rep", _val(d.get("salesperson"))), ("Stock #", _val(veh.get("stock"), True))])}
  </div>
  <img src="{_logo()}" alt="">
</div>
<div class="parties">
  <div><p class="label">Bill to</p>
    <p class="name">{_v(d.get("company"))}</p>
    <p class="sub">{e(d.get("address") or "")}</p><p class="sub">{e(d.get("csz") or "")}</p>
    {f'<p class="sub">FIN code <span class="code">{e(d["fin"])}</span></p>' if d.get("fin") else ""}</div>
  <div><p class="label">From</p>
    <p class="name">{e(DEALER)}</p>
    <p class="sub">1550 N.W. Broad St.</p><p class="sub">Murfreesboro, TN 37129</p>
    <p class="sub">Fleet Department &middot; {e(PHONE)}</p></div>
</div>
<div class="tablewrap">
<table class="items">
  <thead><tr><th>Qty</th><th>Description</th><th class="num">Unit price</th><th class="num">Amount</th></tr></thead>
  <tbody><tr><td class="line">1</td>
    <td><div class="item">{e(veh.get("description") or "")}</div>
      <div class="spec"><span class="code">VIN {e(veh.get("vin") or "")}</span></div>
      <div class="spec">Stock # <span class="code">{e(veh.get("stock") or "")}</span></div></td>
    <td class="num">{price}</td><td class="num">{price}</td></tr></tbody>
</table>
</div>
<div class="totals">
  <div class="row"><span>Subtotal</span><span class="num">{price}</span></div>
  <div class="row"><span>Tax</span><span class="num">&nbsp;</span></div>
  <div class="row"><span>Freight</span><span class="num">&nbsp;</span></div>
  <div class="row grand"><span>Amount due</span><span>{price}</span></div>
</div>
<div class="terms">
  <div><p class="label">Payment terms</p>
    <p>Payment is due within 30 days of the invoice date.</p>
    {f'<p>Please remit by <span class="due">{e(_text_date(due))}</span>.</p>' if due else ""}</div>
  <div><p class="label">Make checks payable to</p>
    <p>{e(DEALER)}</p><p class="sub">Attn: Accounts Receivable</p>
    <p class="sub">1550 N.W. Broad St., Murfreesboro, TN 37129</p>
    <p class="label" style="margin-top:8px">Questions</p><p class="sub">Fleet Department &middot; {e(PHONE)}</p></div>
</div>
<p class="thanks">Thank you for your business.</p>
</section>"""


def invoices_html(d: dict) -> str:
    vehs = [v for v in (d.get("vehicles") or []) if v.get("vin") or v.get("stock") or v.get("description")]
    body = "".join(invoice_html(d, v, i) for i, v in enumerate(vehs))
    return _page(body, "Customer invoice")
