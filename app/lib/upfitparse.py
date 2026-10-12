"""Read an upfitter's quote PDF (Knapheide, Reading, Lehr, Stringfellow ...) and
pull out what the upfit library form needs: who it is from, their quote number,
dates, price, priced line items, and the descriptive text that belongs under
the line on the customer quote.

Every upfitter lays their quote out differently, so this is a best-effort
reader. It fills the form in and the person checks it; nothing here is saved
until they do.
"""

from __future__ import annotations

import datetime as dt
import re

import pdfplumber

KNOWN_UPFITTERS = [
    "Knapheide", "Reading", "Lehr", "Stringfellow", "Auto Truck Group", "Duramag",
    "Warner", "Harbor", "Dakota Bodies", "Brandon", "Switch-N-Go", "Omaha Standard",
    "Royal Truck Body", "Supreme", "Morgan", "Heil", "Kann", "Scelzi", "Tommy Gate",
]

MONEY = re.compile(r"\$?\s*(-?\d{1,3}(?:,\d{3})+(?:\.\d{2})?|-?\d+\.\d{2})")
BULLET = re.compile(r"^\s*(?:[•●▪■·‣*\-–]|\(?\d{1,2}[.)])\s+(.+)$")
TOTAL_LINE = re.compile(
    r"\b(grand\s+total|quote\s+total|total\s+price|net\s+price|total\s+investment|total|amount\s+due)\b", re.I)
SKIP_LINE = re.compile(
    r"\b(sub\s*-?total|tax|terms|conditions|valid|freight\s+not|page\s+\d|thank\s+you|signature|"
    r"accepted|payment|deposit|warranty\s+info|www\.|@|phone|fax|ph:|tel:|bill\s+to|ship\s+to|"
    r"sold\s+to|prepared\s+(?:by|for)|customer|salesperson|quote\s*(?:#|no|number)|date)\b", re.I)
DATE = r"(\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2}|[A-Z][a-z]{2,8}\.? \d{1,2},? \d{4})"


def _money(s: str):
    try:
        return round(float(s.replace(",", "").replace("$", "").strip()), 2)
    except ValueError:
        return None


def _norm_date(s: str) -> str:
    s = (s or "").strip().replace(",", "")
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%B %d %Y", "%b %d %Y", "%b. %d %Y"):
        try:
            return dt.datetime.strptime(s, fmt).strftime("%m/%d/%Y")
        except ValueError:
            continue
    return ""


def _text(path: str) -> str:
    out = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            out.append(page.extract_text() or "")
    return "\n".join(out)


def parse(path: str, known: list | None = None) -> dict:
    text = _text(path)
    lines = [re.sub(r"[ \t]+", " ", l).strip() for l in text.splitlines()]
    lines = [l for l in lines if l]
    res = {
        "upfitter": "", "reference": "", "title": "", "body_type": "", "price": None,
        "quote_date": "", "valid_until": "", "line_items": [], "details": "",
        "applies_to": {}, "notes": "", "confidence": [],
    }
    if not lines:
        res["notes"] = "No text could be read from this PDF (it may be a scan)."
        return res

    # Who it is from
    for name in list(known or []) + KNOWN_UPFITTERS:
        if name and re.search(r"\b" + re.escape(name) + r"\b", text, re.I):
            res["upfitter"] = name
            break

    # Their quote number
    m = re.search(r"\b(?:quote|proposal|estimate)\s*(?:no\.?|number|num|#)\s*[:#]?\s*([A-Z0-9][A-Z0-9\-/]{3,})", text, re.I)
    if m:
        res["reference"] = m.group(1).strip("-/")

    # Dates
    m = re.search(r"\b(?:quote\s+)?date\s*[:#]?\s*" + DATE, text, re.I)
    if m:
        res["quote_date"] = _norm_date(m.group(1))
    m = re.search(r"\bvalid\s+(?:until|through|thru|to)\s*[:]?\s*" + DATE, text, re.I)
    if m:
        res["valid_until"] = _norm_date(m.group(1))
    else:
        m = re.search(r"\b(?:valid|good|firm)\s+for\s+(\d{1,3})\s+days", text, re.I)
        if m and res["quote_date"]:
            base = dt.datetime.strptime(res["quote_date"], "%m/%d/%Y")
            res["valid_until"] = (base + dt.timedelta(days=int(m.group(1)))).strftime("%m/%d/%Y")

    # Priced lines, total, and the descriptive text under them
    priced, total, details, last_priced = [], None, [], False
    for l in lines:
        amounts = [a for a in (_money(x) for x in MONEY.findall(l)) if a is not None]
        if amounts and TOTAL_LINE.search(l) and not re.search(r"sub\s*-?total", l, re.I):
            total = max(total or 0, amounts[-1])
            last_priced = False
            continue
        if amounts and not SKIP_LINE.search(l):
            desc = MONEY.sub("", l)
            desc = re.sub(r"\b\d+(?:\.\d+)?\s*(?:ea|each|qty)?\s*(?:x|@)\s*$", "", desc, flags=re.I)
            desc = re.sub(r"^\s*\d{1,3}\s+(?=[A-Za-z])", "", desc)
            desc = re.sub(r"\s+\d{1,3}(?:\.0)?\s*$", "", desc).strip(" -:|–")   # trailing quantity
            if len(re.findall(r"[A-Za-z]", desc)) >= 4 and amounts[-1] > 0:
                priced.append({"description": desc, "price": amounts[-1]})
                last_priced = True
                continue
        b = BULLET.match(l)
        if b and not SKIP_LINE.search(l):
            details.append("• " + b.group(1).strip())
            last_priced = False
        elif last_priced and not SKIP_LINE.search(l) and not amounts and len(l) > 12:
            details.append(l)           # wrapped description under a priced line

    res["line_items"] = priced[:30]
    if total is None and priced:
        total = round(sum(i["price"] for i in priced), 2)
        res["confidence"].append("Price is the sum of the lines; no total was printed.")
    elif total is not None and priced:
        s = round(sum(i["price"] for i in priced), 2)
        if abs(s - total) > 0.01:
            # Lines don't add to the printed total: trust the total, drop the breakdown.
            res["confidence"].append(
                f"Lines add to ${s:,.2f} but the quote total is ${total:,.2f}; check the breakdown.")
    res["price"] = total
    res["details"] = "\n".join(dict.fromkeys(details))[:3000]

    # A title: the biggest priced line, else the first line that names a body.
    if priced:
        best = max(priced, key=lambda i: i["price"])
        res["title"] = best["description"][:120]
    low = text.lower()
    for kind in ("service body", "dump body", "flatbed", "stake body", "utility body",
                 "crane", "van body", "box truck", "contractor body", "platform body", "rack body"):
        if kind in low:
            res["body_type"] = kind
            break

    # Which chassis it fits, when the quote says
    a = {}
    wb = re.search(r"\b(\d{3})(?:\.\d)?\s*[\"”]?\s*(?:WB|wheel\s*base)", text, re.I)
    if wb:
        a["wheelbase"] = [wb.group(1)]
    if re.search(r"\bDRW\b|dual\s+rear", text, re.I):
        a["drw"] = True
    elif re.search(r"\bSRW\b|single\s+rear", text, re.I):
        a["drw"] = False
    cab = re.search(r"\b(crew\s*cab|super\s*cab|regular\s*cab|reg\.?\s*cab)\b", text, re.I)
    if cab:
        c = cab.group(1).lower().replace(" ", "")
        a["cab"] = "Crew Cab" if "crew" in c else "SuperCab" if "super" in c else "Regular Cab"
    codes = sorted({m for m in re.findall(r"\b([A-Z]\d[A-Z])\b", text) if m[0] in "WXFD"})
    if codes:
        a["model_codes"] = codes[:4]
    res["applies_to"] = a
    return res
