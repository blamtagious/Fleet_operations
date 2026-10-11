"""State of Tennessee statewide-contract bid pricing lookup.

Reads TT_Murfreesboro Ford Pricing.xlsx. Each worksheet is a contract year and
has the shape:

    <section header>            (col A only, e.g. "F-350 CHASSIS CAB")
        <model no> <desc> <MSRP> <State Bid>   (cols B..E)

A model number such as W3H-640A usually appears more than once -- once per
engine -- so matching also weighs the engine described on the spec proposal.
"""

from __future__ import annotations

import os
import re
import unicodedata

import openpyxl

from . import config

HERE = config.APP_DIR
ROOT = config.ROOT
PRICING_XLSX = config.PRICING_XLSX

_cache: dict | None = None


def _num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("$", "").replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def _clean(s):
    if s is None:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    return re.sub(r"\s+", " ", s).strip()


def load(force=False) -> dict:
    """Return {year: [row, ...]} for every worksheet in the bid workbook."""
    global _cache
    if _cache is not None and not force:
        return _cache

    wb = openpyxl.load_workbook(PRICING_XLSX, data_only=True)
    years: dict[str, list] = {}

    for ws in wb.worksheets:
        year = ws.title.strip()
        rows = []
        section = ""
        for r in ws.iter_rows(values_only=True):
            cells = list(r) + [None] * (5 - len(r))
            a, b, c, d, e = (_clean(x) for x in cells[:5])

            # Section header: text in col A, nothing in col B.
            if a and not b:
                # Ignore the sheet's own title block.
                if re.search(
                    r"state of tennessee|dealer name|pricing update|bid sheet|event|^model$",
                    a, re.I,
                ):
                    continue
                section = a
                continue

            if not b:
                continue
            if b.lower() == "model number":
                continue

            msrp, bid = _num(cells[3]), _num(cells[4])
            if msrp is None and bid is None:
                continue

            rows.append(
                {
                    "year": year,
                    "section": section or a,
                    "model_number": b,
                    "description": c,
                    "msrp": msrp,
                    "bid": bid,
                    "discount": (round(1 - bid / msrp, 6) if msrp and bid else None),
                }
            )
        if rows:
            years[year] = rows

    _cache = years
    return _cache


def years() -> list[str]:
    return sorted(load().keys())


# --- engine fingerprinting -------------------------------------------------

def _engine_tokens(text: str) -> set[str]:
    t = (text or "").lower()
    tok = set()
    if "diesel" in t or "power stroke" in t:
        tok.add("diesel")
    for disp in re.findall(r"(\d\.\d)\s*l", t):
        tok.add(disp)
    if "ecoboost" in t:
        tok.add("ecoboost")
    if "hybrid" in t:
        tok.add("hybrid")
    if "electric" in t or t.strip().startswith("ev"):
        tok.add("electric")
    return tok


def _score(row: dict, model_code: str, order_code: str, engine: str) -> float:
    """Higher is better. Negative means the row is not a candidate at all."""
    mn = row["model_number"].upper().replace(" ", "")
    want_model = (model_code or "").upper()
    want_order = (order_code or "").upper()

    if want_model and not mn.startswith(want_model):
        return -1

    score = 0.0
    score += 10 if want_model and mn.startswith(want_model) else 0
    if want_order:
        score += 10 if want_order in mn else -4

    want = _engine_tokens(engine)
    have = _engine_tokens(row["description"])
    if want:
        if "diesel" in want and "diesel" in have:
            score += 8
        elif "diesel" in want and "diesel" not in have:
            score -= 8
        elif "diesel" not in want and "diesel" in have:
            score -= 8
        score += 3 * len((want & have) - {"diesel"})
    return score


def match(model_code: str, order_code: str = "", engine: str = "", year: str = ""):
    """Best bid-sheet row for a parsed spec, plus the other plausible rows."""
    data = load()
    sheets = [year] if year in data else years()

    scored = []
    for y in sheets:
        for row in data[y]:
            s = _score(row, model_code, order_code, engine)
            if s > 0:
                scored.append((s, row))

    if not scored:
        return None, []

    # Prefer the requested contract year, then score.
    scored.sort(key=lambda t: (t[1]["year"] == year, t[0]), reverse=True)
    best = scored[0][1]
    return best, [r for _, r in scored]


def search(term: str, year: str = "", limit: int = 40):
    """Free-text search across model number, description and section."""
    data = load()
    term = (term or "").strip().lower()
    out = []
    for y in ([year] if year in data else years()):
        for row in data[y]:
            hay = f"{row['model_number']} {row['description']} {row['section']}".lower()
            if all(t in hay for t in term.split()):
                out.append(row)
                if len(out) >= limit:
                    return out
    return out


if __name__ == "__main__":
    import json, sys

    if len(sys.argv) > 1 and sys.argv[1] == "search":
        print(json.dumps(search(" ".join(sys.argv[2:])), indent=2))
    else:
        best, cands = match("W3H", "640A", "6.7L 4V OHV Power Stroke V8 Turbo Diesel B20", "2026")
        print("BEST:", json.dumps(best, indent=2))
        print("\nCANDIDATES:")
        for c in cands[:6]:
            print(" ", c["year"], c["model_number"], "|", c["description"][:46],
                  "| msrp", c["msrp"], "| bid", c["bid"], "| disc",
                  f"{c['discount']:.4f}" if c["discount"] else "-")
