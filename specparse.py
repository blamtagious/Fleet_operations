"""Parse a Ford fleet spec proposal PDF into a structured configuration.

The Ford "As Configured Vehicle" proposal uses a consistent typographic grammar
that we key off of rather than guessing at whitespace:

    size 12 / Helvetica-Bold / x0~27   -> section heading  ("Powertrain")
    size 10 / Helvetica     / x0~36    -> option code      ("99T")
    size 10 / Helvetica     / x0~145   -> option description
    size 10 / Helvetica     / x0>500   -> MSRP for that option
    size  8 / Helvetica-Oblique        -> explanatory notes (ignored)

A price sits a few points ABOVE the baseline of the row it belongs to, so rows
are assembled by nearest-neighbour matching rather than exact top equality.
"""

from __future__ import annotations

import collections
import re
from dataclasses import dataclass, field, asdict

import pdfplumber

# Column boundaries in PDF points (page is 595pt wide).
CODE_X = (30, 100)
WERS_X = (100, 140)
DESC_X = 140
PRICE_X = 480

ITEM_SIZE = 10.0
HEAD_SIZE = 12.0
SIZE_TOL = 0.6

# Rows Ford emits that are structural, not options.
SKIP_HEADINGS = {"code wers description msrp"}

# The five-line block Ford repeats at the top of every page.
HEADER_PREFIXES = (
    "Prepared for:",
    "Prepared by:",
    "Price Level:",
    "Ford of Murfreesboro",
)
HEADER_BAND = 130  # points from the top of the page


def _is_page_header(text: str, top: float) -> bool:
    if top > HEADER_BAND:
        return False
    return text.startswith(HEADER_PREFIXES) or bool(
        re.match(r"^\d{2}/\d{2}/\d{4}\b", text)
    ) or bool(re.match(r"^\d{4}\s+\S", text))


MONEY = re.compile(r"^\$[\d,]+\.\d{2}$")
NON_PRICE = {"N/C", "STD", "Included", "N/A"}


@dataclass
class Option:
    code: str = ""
    description: str = ""
    section: str = ""
    msrp: float | None = None       # None when the line is N/C / STD / Included
    price_label: str = ""           # verbatim right-hand column text
    is_priced: bool = False         # True when it carries real dollars


@dataclass
class SpecProposal:
    prepared_for: str = ""
    customer_title: str = ""
    customer_org: str = ""
    prepared_by: str = ""
    spec_date: str = ""
    year: str = ""
    vehicle_title: str = ""
    model_code: str = ""
    order_code: str = ""
    price_level: str = ""
    engine: str = ""
    exterior_color: str = ""
    interior_color: str = ""
    options: list = field(default_factory=list)
    subtotal: float | None = None
    destination: float | None = None
    total: float | None = None
    base_price: float | None = None
    summary: dict = field(default_factory=dict)

    def to_dict(self):
        d = asdict(self)
        d["options"] = [asdict(o) if not isinstance(o, dict) else o for o in self.options]
        return d


def _money(text: str):
    t = text.strip().replace("$", "").replace(",", "")
    try:
        return float(t)
    except ValueError:
        return None


def _customer_line(rows) -> tuple:
    """The organisation (and job title, when given) under "Prepared for:".

    Ford writes this two ways:

        Prepared for: Brenna Bak
        Senior Project Administrator, Southeast Tennessee Development District

        Prepared for: Larry Gaines
        Oakland Police Dept

    So it is found by position -- the next left-hand header row -- rather than
    by looking for a comma, which the second form does not have.
    """
    ordered = []
    for top, ws in sorted(rows.items()):
        if top > HEADER_BAND:
            break
        first = ws[0]
        if first["x0"] > 200:          # the dealer address sits on the right
            continue
        ordered.append((top, first, " ".join(w["text"] for w in ws).strip()))

    for i, (_top, _first, text) in enumerate(ordered):
        if not text.startswith("Prepared for:"):
            continue
        for _t, f, nxt in ordered[i + 1:]:
            if not _is(f["size"], 10.0):
                continue
            if nxt.startswith(("Prepared by:", "Price Level:")):
                break
            if re.match(r"^\d{2}/\d{2}/\d{4}", nxt):
                break
            if "," in nxt:
                title, org = (p.strip() for p in nxt.split(",", 1))
                return title, org
            return "", nxt
        break
    return "", ""


def _rows(page):
    """Group words into visual rows keyed by rounded `top`."""
    words = page.extract_words(extra_attrs=["size", "fontname"])
    rows = collections.defaultdict(list)
    for w in words:
        rows[round(w["top"], 0)].append(w)
    return {k: sorted(v, key=lambda w: w["x0"]) for k, v in sorted(rows.items())}


def _is(size, target):
    return abs(size - target) < SIZE_TOL


def parse(path: str) -> SpecProposal:
    spec = SpecProposal()
    pending_prices: dict[float, tuple[str, float | None]] = {}
    section = ""
    last_option: Option | None = None

    with pdfplumber.open(path) as pdf:
        header_done = False
        for pageno, page in enumerate(pdf.pages):
            rows = _rows(page)
            page_kind = ""
            for _t, _ws in rows.items():
                if _is(_ws[0]["size"], 18.0):
                    page_kind = " ".join(w["text"] for w in _ws)
                    break
            is_config_page = page_kind.startswith("As Configured Vehicle")

            # ---- page header block (identical on every page; read it once) ----
            if not header_done:
                for top, ws in rows.items():
                    text = " ".join(w["text"] for w in ws).strip()
                    if text.startswith("Prepared for:"):
                        spec.prepared_for = text.split(":", 1)[1].strip()
                    elif text.startswith("Prepared by:"):
                        spec.prepared_by = text.split(":", 1)[1].strip()
                    elif text.startswith("Price Level:"):
                        spec.price_level = text.split(":", 1)[1].strip()
                    elif re.match(r"^\d{2}/\d{2}/\d{4}$", ws[0]["text"]):
                        spec.spec_date = ws[0]["text"]
                    elif _is(ws[0]["size"], 13.0) and "(" in text:
                        spec.vehicle_title = text
                        m = re.match(r"^(\d{4})\s+(.*)$", text)
                        if m:
                            spec.year = m.group(1)
                        m = re.search(r"\(([A-Z0-9]+)\)\s*$", text)
                        if m:
                            spec.model_code = m.group(1)
                if not spec.customer_org:
                    spec.customer_title, spec.customer_org = _customer_line(rows)

                if spec.vehicle_title:
                    header_done = True

            # ---- body rows ----
            for top, ws in (rows.items() if is_config_page else ()):
                first = ws[0]
                text = " ".join(w["text"] for w in ws).strip()
                size = first["size"]

                if _is_page_header(text, top):
                    continue

                # Right-column price floating just above its item row.
                price_words = [w for w in ws if w["x0"] >= PRICE_X and _is(w["size"], ITEM_SIZE)]
                if price_words and len(ws) == len(price_words):
                    label = " ".join(w["text"] for w in price_words)
                    pending_prices[top] = (label, _money(label))
                    continue

                # Totals block (left-aligned label + right-aligned money).
                up = text.upper()
                if up.startswith("SUBTOTAL"):
                    spec.subtotal = _money(text.split()[-1])
                    continue
                if text.startswith("Destination Charge"):
                    spec.destination = _money(text.split()[-1])
                    continue
                if up.startswith("TOTAL") and spec.total is None:
                    spec.total = _money(text.split()[-1])
                    continue

                # Section heading.
                if _is(size, HEAD_SIZE) and "Bold" in first["fontname"] and first["x0"] < 60:
                    if text.lower() not in SKIP_HEADINGS:
                        section = text
                        last_option = None
                    continue

                # Option row: code in the left band, description in the middle band.
                if _is(size, ITEM_SIZE) and CODE_X[0] <= first["x0"] < CODE_X[1]:
                    code_ws = [w for w in ws if w["x0"] < DESC_X]
                    desc_ws = [w for w in ws if w["x0"] >= DESC_X]
                    code = "".join(w["text"] for w in code_ws)
                    desc = " ".join(w["text"] for w in desc_ws)
                    if not desc:
                        # Wrapped code continuation, e.g. "WARAN" / "T".
                        if last_option is not None and not desc_ws:
                            last_option.code += code
                        continue
                    opt = Option(code=code, description=desc, section=section)
                    spec.options.append(opt)
                    last_option = opt
                    # Attach the nearest price emitted just above this row.
                    best = None
                    for ptop in list(pending_prices):
                        if -14 <= (top - ptop) <= 8:
                            if best is None or abs(top - ptop) < abs(top - best):
                                best = ptop
                    if best is not None:
                        label, val = pending_prices.pop(best)
                        opt.price_label = label
                        opt.msrp = val
                        opt.is_priced = val is not None
                    continue

                # Description-only continuation of the previous option.
                if (
                    _is(size, ITEM_SIZE)
                    and first["x0"] >= DESC_X
                    and last_option is not None
                    and not price_words
                ):
                    last_option.description += " " + text

            # Pricing Summary page.
            for top, ws in rows.items():
                text = " ".join(w["text"] for w in ws).strip()
                m = re.match(
                    r"^(Base Vehicle Price|Options|Colors|Upfitting|Fleet Discount|"
                    r"Fuel Charge|Destination Charge|Total)\s+\$?([\d,]+\.\d{2})$",
                    text,
                )
                if m:
                    spec.summary[m.group(1)] = _money(m.group(2))

    # Derived fields.
    for o in spec.options:
        if o.section == "Base Vehicle" and spec.base_price is None:
            spec.base_price = o.msrp
        if o.section == "Packages" and re.match(r"^\d{3}A$", o.code) and not spec.order_code:
            spec.order_code = o.code
        if o.description.startswith("Engine:"):
            spec.engine = o.description.split(":", 1)[1].strip()
        if o.section == "Exterior Color":
            spec.exterior_color = o.description
        if o.section == "Interior Color":
            spec.interior_color = o.description

    if spec.base_price is None:
        spec.base_price = spec.summary.get("Base Vehicle Price")

    return spec


if __name__ == "__main__":
    import json, sys

    s = parse(sys.argv[1])
    print(json.dumps(s.to_dict(), indent=2))
