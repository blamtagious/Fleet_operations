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

CSS = """
@page { size: Letter; margin: 0.6in 42pt 0.7in; }
:root { --font: Lato, "Helvetica Neue", Helvetica, Arial, sans-serif; --ink:#111827; --muted:#4b5563;
        --faint:#6b7280; --rule:#9ca3af; --panel:#e5e7eb; --accent:#00063d; }
* { box-sizing: border-box; }
body { font-family: var(--font); font-size: 10pt; line-height: 1.38; color: var(--ink); margin: 0; padding: 0 12pt;
       -webkit-print-color-adjust: exact; print-color-adjust: exact; }
.label { font-size: 7.5pt; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); margin: 0 0 3pt; }
.num { font-variant-numeric: tabular-nums; text-align: right; white-space: nowrap; }
.header { display: flex; justify-content: space-between; align-items: flex-start; padding-bottom: 14pt; border-bottom: .5pt solid var(--rule); }
.header img { height: 52pt; width: auto; display: block; }
.title { text-align: right; }
.title h1 { margin: 0 0 7pt; font-size: 22pt; font-weight: 300; letter-spacing: -.01em; color: var(--accent); }
.meta { display: inline-grid; grid-template-columns: auto auto; gap: 2pt 18pt; text-align: left; margin: 0; }
.meta dt { font-size: 7.5pt; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); align-self: center; }
.meta dd { margin: 0; font-weight: 500; text-align: right; min-width: 70pt; }
.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 0 36pt; margin-top: 16pt; }
.cols p { margin: 0; }
.name { font-weight: 600; }
.sub { color: var(--muted); }
.blank { color: var(--rule); }
table.items { width: 100%; border-collapse: collapse; margin-top: 22pt; }
table.items th { font-size: 7.5pt; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; color: var(--muted);
                 text-align: left; padding: 0 0 6pt; }
table.items th.num { text-align: right; }
table.items td { padding: 6pt 0; vertical-align: top; }
table.items tr { break-inside: avoid; }
td.stock { width: 70pt; color: var(--muted); }
.item { font-weight: 600; }
.spec { color: var(--muted); font-size: 8.3pt; }
.mono { font-family: "Courier New", monospace; letter-spacing: .03em; }
tr.group td { padding: 14pt 0 2pt; font-size: 7.5pt; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); }
tr.empty td { color: var(--rule); font-size: 9pt; }
.totals { margin: 14pt 0 0 auto; width: 260pt; break-inside: avoid; }
.totals .row { display: flex; justify-content: space-between; padding: 2pt 0; }
.totals .row > span:first-child { color: var(--muted); }
.totals .grand { margin: 6pt -12pt 0; padding: 8pt 12pt; background: var(--panel); border-radius: 4pt; align-items: baseline; }
.totals .grand > span:first-child { font-size: 8pt; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; color: var(--ink); }
.totals .grand > span:last-child { font-size: 15pt; font-weight: 700; color: var(--accent); font-variant-numeric: tabular-nums; }
.notes { margin-top: 20pt; font-size: 9pt; color: var(--muted); max-width: 5.6in; white-space: pre-line; }
.foot { position: fixed; bottom: 0; left: 12pt; right: 12pt; display: flex; justify-content: space-between; font-size: 7.5pt; color: var(--faint); }
.terms { margin-top: 22pt; display: grid; grid-template-columns: 1fr 260pt; gap: 0 36pt; break-inside: avoid; }
.terms p { margin: 0; }
.due { font-size: 11pt; font-weight: 700; color: var(--accent); }
.thanks { margin-top: 26pt; text-align: center; font-size: 9pt; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); }
section.inv + section.inv { break-before: page; }
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


def _page(body: str, foot: str) -> str:
    return (f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>{e(foot)}</title>'
            f'<style>{CSS}</style></head><body>{body}<div class="foot"><span>{e(DEALER)} &middot; '
            f'{e(ADDRESS[0])} &middot; {e(ADDRESS[1])}</span><span>{e(foot)}</span></div></body></html>')


def cover_html(d: dict) -> str:
    vehs = d.get("vehicles") or []
    total = sum(_n(v.get("price")) for v in vehs)

    def veh_row(i, v):
        extra = ""
        if v.get("vin"):
            extra += f'<div class="spec mono">VIN {e(v["vin"])}</div>'
        if v.get("invoiceNo"):
            extra += f'<div class="spec">Invoice # {e(v["invoiceNo"])}</div>'
        return (f'<tr><td class="stock">{e(v.get("stock") or "")}</td>'
                f'<td><div class="item">{e(v.get("description") or f"Vehicle {i + 1}")}</div>{extra}</td>'
                f'<td class="num">{_money(v.get("price"))}</td></tr>')

    def lines(title, head, rows):
        rows = [r for r in rows if r.get("desc") or r.get("cost")]
        body = "".join(
            f'<tr><td colspan="2">{e(r.get("desc") or "")}</td><td class="num">{_money(r.get("cost"))}</td></tr>'
            for r in rows) or '<tr class="empty"><td colspan="3">None</td></tr>'
        return (f'<tr class="group"><td colspan="3">{title}</td></tr>{body}')

    body = f"""
<div class="header">
  <img src="{_logo()}" alt="">
  <div class="title"><h1>Billing Cover Sheet</h1>
    <dl class="meta">
      <dt>Date</dt><dd>{e(_mdy(d.get("date", ""))) or "&nbsp;"}</dd>
      <dt>Fleet P.O. #</dt><dd>{e(d.get("fleetPO") or "")}&nbsp;</dd>
      <dt>Salesperson</dt><dd>{e(d.get("salesperson") or "")}&nbsp;</dd>
      <dt>Units</dt><dd>{len(vehs)}</dd>
    </dl></div>
</div>
<div class="cols">
  <div><p class="label">Bill to</p>
    <p class="name">{_v(d.get("company"))}</p>
    <p class="sub">{_v(d.get("address"))}</p><p class="sub">{_v(d.get("csz"))}</p>
    <p class="sub">FIN {_v(d.get("fin"))}</p></div>
  <div><p class="label">Contact</p>
    <p class="name">{_v(d.get("contactName"))}</p>
    <p class="sub">{_v(d.get("contactPhone"))}</p><p class="sub">{_v(d.get("contactEmail"))}</p></div>
</div>
<table class="items">
  <thead><tr><th>Stock #</th><th>Description</th><th class="num">Selling price</th></tr></thead>
  <tbody>
    {"".join(veh_row(i, v) for i, v in enumerate(vehs)) or '<tr class="empty"><td colspan="3">No vehicles added</td></tr>'}
  </tbody>
</table>
<div class="totals">
  <div class="row grand"><span>Total selling price</span><span>{_money(total) if total else ""}</span></div>
</div>
<table class="items" style="margin-top:6pt">
  <tbody>
    {lines("We owe(s)", "Cost", d.get("weOwes") or [])}
    {lines("Adjustments / programs", "Amount", d.get("adjs") or [])}
  </tbody>
</table>
{f'<div class="notes"><p class="label">Notes</p>{e(d.get("notes"))}</div>' if (d.get("notes") or "").strip() else ""}
"""
    return _page(body, "Billing cover sheet")


def invoice_html(d: dict, veh: dict, number: int = 0) -> str:
    dd = d.get("date") or ""
    try:
        due = (dt.date.fromisoformat(dd) + dt.timedelta(days=30)).isoformat() if dd else ""
    except ValueError:
        due = ""
    price = _money(veh.get("price"))
    inv_no = (veh.get("invoiceNo") or "").strip()
    return f"""<section class="inv">
<div class="header">
  <img src="{_logo()}" alt="">
  <div class="title"><h1>Invoice</h1>
    <dl class="meta">
      <dt>Invoice #</dt><dd>{e(inv_no)}&nbsp;</dd>
      <dt>Invoice date</dt><dd>{e(_mdy(dd))}&nbsp;</dd>
      <dt>PO number</dt><dd>{e(d.get("fleetPO") or "")}&nbsp;</dd>
      <dt>Date due</dt><dd>{e(_mdy(due))}&nbsp;</dd>
      <dt>Sales rep</dt><dd>{e(d.get("salesperson") or "")}&nbsp;</dd>
      <dt>Stock #</dt><dd>{e(veh.get("stock") or "")}&nbsp;</dd>
    </dl></div>
</div>
<div class="cols">
  <div><p class="label">Bill to</p>
    <p class="name">{_v(d.get("company"))}</p>
    <p class="sub">{e(d.get("address") or "")}</p><p class="sub">{e(d.get("csz") or "")}</p>
    {f'<p class="sub">FIN {e(d["fin"])}</p>' if d.get("fin") else ""}</div>
  <div><p class="label">From</p>
    <p class="name">{e(DEALER)}</p>
    <p class="sub">{e(ADDRESS[0])}</p><p class="sub">{e(ADDRESS[1])}</p>
    <p class="sub">Fleet Department &middot; {e(PHONE)}</p></div>
</div>
<table class="items">
  <thead><tr><th style="width:40pt">Qty</th><th>Description</th><th class="num" style="width:90pt">Unit price</th>
    <th class="num" style="width:90pt">Amount</th></tr></thead>
  <tbody><tr><td class="stock">1</td>
    <td><div class="item">{e(veh.get("description") or "")}</div>
      <div class="spec mono">VIN {e(veh.get("vin") or "")}</div>
      <div class="spec">Stock # {e(veh.get("stock") or "")}</div></td>
    <td class="num">{price}</td><td class="num">{price}</td></tr></tbody>
</table>
<div class="totals">
  <div class="row"><span>Subtotal</span><span class="num">{price}</span></div>
  <div class="row"><span>Tax</span><span class="num">&nbsp;</span></div>
  <div class="row"><span>Freight</span><span class="num">&nbsp;</span></div>
  <div class="row grand"><span>Pay this amount</span><span>{price}</span></div>
</div>
<div class="terms">
  <div><p class="label">Payment terms</p>
    <p>Payment is due within 30 days of the invoice date.</p>
    {f'<p>Please remit by <span class="due">{e(_long(due))}</span>.</p>' if due else ""}</div>
  <div><p class="label">Make checks payable to</p>
    <p>{e(DEALER)}</p><p class="sub">Attn: Accounts Receivable</p>
    <p class="sub">{e(ADDRESS[0])}, {e(ADDRESS[1])}</p>
    <p class="label" style="margin-top:10pt">Questions</p><p class="sub">Fleet Department &middot; {e(PHONE)}</p></div>
</div>
<p class="thanks">Thank you for your business</p>
</section>"""


def invoices_html(d: dict) -> str:
    vehs = [v for v in (d.get("vehicles") or []) if v.get("vin") or v.get("stock") or v.get("description")]
    body = "".join(invoice_html(d, v, i) for i, v in enumerate(vehs))
    return _page(body, "Customer invoice")
