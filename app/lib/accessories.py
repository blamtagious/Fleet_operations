"""Import the parts department's Ford Accessories workbook into the upfit library.

The workbook has three sheets (BED COVERS, RUNNING BOARDS, FLOOR MATS). Every
priced row becomes one library record under the upfitter "Ford Accessories",
so sellers add it to a quote exactly like any other upfit. Records carry
`applies_to.models / cab / bed / years` so the quote page only suggests the ones
that fit the truck on the proposal.

Re-importing is safe: records are keyed by part number, so a new copy of the
sheet updates prices in place and removes parts that were dropped from it.
"""

from __future__ import annotations

import re
from collections import defaultdict

import openpyxl

from . import store

UPFITTER = "Ford Accessories"
YEAR = "catalog"
SOURCE_TAG = "parts-dept-import"


def _money(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _name(s: str) -> str:
    s = re.sub(r"\s+", " ", str(s or "").strip())
    s = re.sub(r"(\w)\(", r"\1 (", s)
    return re.sub(r"\((\w)", lambda m: "(" + m.group(1).upper(), s)


def _cab(text: str) -> str:
    t = (text or "").lower()
    if "supercab or crew" in t or "super cab or crew" in t:
        return ""
    if "supercrew" in t or "crew cab" in t:
        return "Crew Cab"
    if "supercab" in t or "super cab" in t:
        return "SuperCab"
    if "reg cab" in t or "regular cab" in t:
        return "Regular Cab"
    return ""


def _years(text) -> list:
    """'21-C' -> [2021, 2099] (current production); FY/LY columns come in as ints."""
    m = re.match(r"\s*(\d{2,4})\s*-\s*(\w+)", str(text or ""))
    if not m:
        return []
    fy = int(m.group(1))
    fy += 2000 if fy < 100 else 0
    ly = m.group(2)
    return [fy, 2099 if ly.upper() == "C" else (int(ly) + (2000 if int(ly) < 100 else 0))]


def _bed(text) -> str:
    m = re.search(r"(\d+(?:\.\d+)?)", str(text or ""))
    return f"{float(m.group(1)):g}" if m else ""


def _record(category, name, model, pn, price, cost, *, years=None, bed="", cab="",
            note="", cur_pn=""):
    fam = store.model_family(model)
    model = re.sub(r"\s*\(P[\d/P]+\)", "", model)
    where = " ".join(x for x in (model.strip(), f"{bed}' bed" if bed else "") if x)
    desc = f"{name} - {where} (P/N {pn})" if where else f"{name} (P/N {pn})"
    notes = [f"Dealer cost {cost:,.2f}." if cost is not None else "", note]
    if cur_pn and cur_pn.upper() != "N/A" and cur_pn != pn:
        notes.append(f"Previous P/N {cur_pn}.")
    return {
        "upfitter": UPFITTER,
        "reference": pn,
        "title": f"{name} - {where}" if where else name,
        "body_type": category,
        "year": YEAR,
        "price": price,
        "line_items": [{"description": desc, "price": price}],
        "applies_to": {
            "models": [fam] if fam else [],
            "cab": cab,
            "bed": bed,
            "years": years or [],
        },
        "notes": " ".join(n for n in notes if n),
        "tags": [SOURCE_TAG, category],
    }


def parse(path: str) -> tuple[list, list]:
    """Return (records, warnings) from the workbook."""
    wb = openpyxl.load_workbook(path, data_only=True)
    recs, warns = [], []
    costs = defaultdict(set)  # (sheet, name, model, cost) -> {installed prices}

    for ws in wb:
        sheet = ws.title.strip().upper()
        for row in ws.iter_rows(values_only=True):
            row = list(row) + [None] * 10
            if sheet == "BED COVERS":
                ctype, app, bed, fy, ly, cur, new, cost, price = row[:9]
                if str(ctype or "").strip().lower() == "cover type":
                    continue
                pn = str(new if new not in (None, "", "N/A") else cur or "").strip()
                cat = "Bed Cover"
                name = f"{_name(ctype)} Tonneau Cover"
                model, bed_s = str(app or ""), _bed(bed)
                years = [int(fy), int(ly)] if isinstance(fy, (int, float)) and isinstance(ly, (int, float)) else []
                extra = dict(years=years, bed=bed_s, cur_pn=str(cur or ""))
            elif sheet == "RUNNING BOARDS":
                name, model, yrs, pn, cost, price = row[:6]
                if str(name or "").strip().lower() == "running boards":
                    continue
                pn, cat, name, model = str(pn or "").strip(), "Running Boards", _name(name), str(model or "")
                name = name.replace("Steb", "Step")
                extra = dict(years=_years(yrs), cab=_cab(model))
            elif sheet == "FLOOR MATS":
                name, model, yrs, pn, cost, price, note = row[:7]
                if str(name or "").strip().lower() == "floor liners":
                    continue
                pn, cat, name, model = str(pn or "").strip(), "Floor Liners", _name(name), str(model or "")
                extra = dict(years=_years(yrs), cab=_cab(model), note=str(note or "").strip())
            else:
                continue
            price, cost = _money(price), _money(cost)
            if not pn or price is None:
                continue
            recs.append(_record(cat, name, model, pn, price, cost, **extra))
            if cost is not None:
                costs[(sheet, name, model, cost)].add(price)

    for (sheet, name, model, cost), prices in costs.items():
        if len(prices) > 1:
            warns.append(
                f"{name} / {model}: same dealer cost {cost:,.2f} but installed prices "
                f"{', '.join(f'{p:,.2f}' for p in sorted(prices))}. Check the sheet."
            )
    return recs, warns


def import_workbook(path: str) -> dict:
    recs, warns = parse(path)
    saved, seen = [], set()
    for r in recs:
        base, n = r["reference"], 1
        while (store.slug(UPFITTER), YEAR, store.slug(r["reference"])) in seen:
            n += 1
            r["reference"] = f"{base}-{n}"
        seen.add((store.slug(UPFITTER), YEAR, store.slug(r["reference"])))
        saved.append(store.save_upfit(r)["id"])
    stale = 0
    for rec in store.list_upfits():
        if rec.get("upfitter") == UPFITTER and SOURCE_TAG in (rec.get("tags") or []) \
                and rec["id"] not in saved:
            stale += store.delete_upfit(rec["id"])
    return {"imported": len(saved), "removed": stale, "warnings": warns}
