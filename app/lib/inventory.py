"""Inventory and order dashboard: one row per vehicle, built from the weekly exports.

Sources (uploaded on the Inventory page, recognised by their column headings):

    orders     Ford Pro Order Status. Delivered rows accumulate by VIN; the
               non-delivered rows replace the previous open-order pull.
    inventory  Ford dealer inventory (STOCKINVENTORY / INTRANSIT / INPLANT).
    tess       Accounting's stock list. Cross-check only.
    sales      Ford "MTD Sold VIN Detail". Accumulates by VIN across months.
    repsheet   The reps' old spreadsheet. Loaded once to seed status, customer,
               upfitter and notes; after that those fields are edited in the app.

Everything lives under DATA_DIR/inventory and is never committed: it holds
customer names. Invoice cost is deliberately not read from any source.

A unit counts as on the ground only when Ford's inventory lists it as unsold
stock or it is on Tess's list. Ford Pro "Delivered" alone proves nothing about
where the vehicle is now.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import re
import threading

from . import config

DIR = os.path.join(config.DATA_DIR, "inventory")
SNAP_DIR = os.path.join(DIR, "snapshots")
_LOCK = threading.RLock()
_CACHE: dict = {}

STATUSES = ["Available", "Quoted", "Sold", "Billed Out", "Released to Retail"]
DEFAULT_UPFITTERS = [
    "Truckers Lighthouse - Nashville", "ProLogic (Lehr)", "Stringfellow - Nashville",
    "ML Utilities", "Dealers Truck", "Dana Safety Supply",
]
STAGES = ["Ordered", "Scheduled", "In Production", "In Transit", "On Ground", "At Upfitter",
          "Ready for Delivery", "Billed Out", "Sold", "Unconfirmed"]
EDIT_FIELDS = ("status", "customer", "unconfirmed", "location", "upfit_complete", "notes")
SOURCE_LABELS = {
    "orders": "Ford Pro order status", "inventory": "Ford inventory", "tess": "Tess's list",
    "sales": "Ford sales report", "repsheet": "Rep sheet (one-time)",
}
_SEED_STATUS = {"stock": "Available", "retail (chuck)": "Released to Retail", "sold": "Sold",
                "quoted": "Quoted", "billed out": "Billed Out"}
_LOC_FIX = {"stringrellow": "Stringfellow - Nashville", "stringfellow": "Stringfellow - Nashville"}
_NOT_A_CUSTOMER = {"STATE OF TENNESSEE", "FORD OF MURFREESBORO", ""}


# ---------------------------------------------------------------- storage

def _path(name: str) -> str:
    return os.path.join(DIR, name)


def _load(name: str, default):
    try:
        with open(_path(name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(name: str, obj) -> None:
    os.makedirs(DIR, exist_ok=True)
    tmp = _path(name) + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, separators=(",", ":"))
    os.replace(tmp, _path(name))


# ---------------------------------------------------------------- parsing

def _s(v):
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s in ("#N/A", "None") else s


def _date(v) -> str:
    """ISO date string, or ''."""
    if v in (None, ""):
        return ""
    if isinstance(v, dt.datetime):
        return v.date().isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()
    s = str(v).strip()[:10]
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return ""


def _int(v):
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return None


def _year(v):
    n = _int(v)
    if n is None:
        return None
    return n + 2000 if n < 100 else n


def _strip_code(v: str) -> str:
    return re.sub(r"\s*\([^)]*\)\s*$", "", v or "").strip()


_COLOR_FIX = {"Oxford Wht": "Oxford White", "White": "Oxford White", "Carbonized Gry": "Carbonized Gray",
              "Agate Blk": "Agate Black", "Agate Blk Met": "Agate Black"}


def _color(v: str) -> str:
    if not v:
        return ""
    v = _strip_code(v).upper()
    v = re.sub(r"(?<![A-Z])(SOLID|C/C|WB|-W/B|METALLIC|TRI-?COAT|3C)(?![A-Z])", "", v).split("/")[0]
    v = re.sub(r"\s+", " ", v).strip(" -").title()
    return _COLOR_FIX.get(v, v)


def vehicle_line(*parts) -> str:
    """One consistent vehicle name from whatever a source calls it."""
    t = " ".join(str(p or "") for p in parts).upper()
    if "POL RESP" in t or "RESPONDER" in t:
        return "F-150 Police Responder"
    if "POLICE" in t or "PI UTILITY" in t or re.search(r"\bK8A\b", t):
        return "Police Interceptor Utility"
    m = re.search(r"\bF-?\s?([2-6])50\b", t)
    if m:
        return f"F-{m.group(1)}50"
    for key, name in (("MACH", "Mustang Mach-E"), ("BRONCO SP", "Bronco Sport"), ("BRONCO", "Bronco"),
                      ("MAVERICK", "Maverick"), ("RANGER", "Ranger"), ("EXPED", "Expedition"),
                      ("TRANSIT", "Transit"), ("E450", "E-Series"), ("ECONOLINE", "E-Series"),
                      ("F-150", "F-150"), ("F150", "F-150"), ("EXPL", "Explorer"),
                      ("SUPERDUTY", "Super Duty"), ("F-SERIES SD", "Super Duty")):
        if key in t:
            return name
    return ""


def _tables(blob: bytes, filename: str):
    """Yield (sheet name, rows) for a workbook or CSV. Formula cells give their last saved value."""
    if blob[:2] == b"PK":
        import openpyxl

        wb = openpyxl.load_workbook(io.BytesIO(blob), data_only=True, read_only=True)
        for ws in wb.worksheets:
            yield ws.title, [list(r) for r in ws.iter_rows(values_only=True)]
    else:
        text = blob.decode("utf-8-sig", errors="replace")
        yield os.path.basename(filename), [r for r in csv.reader(io.StringIO(text))]


_SIGNATURES = (
    ("orders", {"order number", "primary status", "vin"}),
    ("tess", {"serial#", "stock no"}),
    ("sales", {"vin", "sale date", "sale type"}),
    ("repsheet", {"vin", "customer", "location", "status"}),
    ("inventory", {"vin", "status", "pep"}),
)


def _detect(rows):
    """(kind, header index, {heading: column}) for the first heading row that matches a known export."""
    for i, row in enumerate(rows[:12]):
        heads = {}
        for j, c in enumerate(row):
            h = re.sub(r"\s+", " ", _s(c).lower())
            if h and h not in heads:
                heads[h] = j
        for kind, need in _SIGNATURES:
            if need <= set(heads):
                return kind, i, heads
    return None, 0, {}


def _records(rows, start, heads):
    for row in rows[start + 1:]:
        yield {h: (row[j] if j < len(row) else None) for h, j in heads.items()}


def _is_vin(v: str) -> bool:
    return bool(re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", v or ""))


def _parse_orders(recs):
    out = []
    for r in recs:
        order = _s(r.get("order number"))
        if not order:
            continue
        vin = _s(r.get("vin")).upper()
        body_code, body = _s(r.get("body code")), _s(r.get("body code description"))
        out.append({
            "order": order, "year": _year(r.get("model year")), "vin": vin if _is_vin(vin) else "",
            "vline": vehicle_line(r.get("vehicle line"), body_code, body, r.get("version")),
            "body_code": body_code, "body": body,
            "fp_primary": _s(r.get("primary status")), "fp_secondary": _s(r.get("secondary status")),
            "fp_updated": _date(r.get("status last updated")), "eta_week": _date(r.get("estimated arrival week")),
            "end_user": _s(r.get("end user fin name")).title(), "cust_ref": _s(r.get("customer name")),
            "po": _s(r.get("purchase order number")), "order_received": _date(r.get("order received")),
            "est_build": _date(r.get("estimated build date")), "produced": _date(r.get("produced date")),
            "shipped": _date(r.get("shipped date")), "delivered": _date(r.get("delivered date")),
            "engine": _strip_code(_s(r.get("engine"))), "pep": _s(r.get("pep/tpo code")),
            "wheelbase": (re.match(r"\d+", _s(r.get("wheelbase"))) or [""])[0],
            "color": _color(_s(r.get("paint"))),
        })
    return out


def _parse_inventory(recs):
    out = []
    for r in recs:
        vin = _s(r.get("vin")).upper()
        if not _is_vin(vin):
            continue
        out.append({
            "vin": vin, "fmc_status": _s(r.get("status")).upper(), "dol": _int(r.get("dol")),
            "year": _year(r.get("model year")), "vline": vehicle_line(r.get("model"), r.get("body")),
            "body": _s(r.get("body")), "pep": _s(r.get("pep")), "engine": _s(r.get("engine")),
            "wheelbase": _s(r.get("wheelbase")), "color": _color(_s(r.get("exterior color"))),
        })
    return out


def _parse_tess(recs):
    out = []
    for r in recs:
        vin = _s(r.get("serial#")).upper()
        if not _is_vin(vin):
            continue
        out.append({
            "vin": vin, "order": _s(r.get("order")), "year": _year(r.get("yr")), "tess_days": _int(r.get("day")),
            "vline": vehicle_line(r.get("carline"), r.get("model description")),
            "body": _s(r.get("model description")), "color": _color(_s(r.get("ext color desc"))),
        })
    return out


def _parse_sales(recs):
    out = []
    for r in recs:
        vin = _s(r.get("vin")).upper()
        if not _is_vin(vin):
            continue
        out.append({"vin": vin, "sale_date": _date(r.get("sale date")), "sale_type": _s(r.get("sale type")),
                    "year": _year(r.get("model year")), "vline": vehicle_line(r.get("vehicle"))})
    return out


def _parse_repsheet(recs):
    out = []
    for r in recs:
        vin = _s(r.get("vin")).upper()
        if not _is_vin(vin):
            continue
        loc = _s(r.get("location"))
        cust = _s(r.get("customer"))
        out.append({
            "vin": vin, "order": _s(r.get("order")), "year": _year(r.get("year")),
            "body_code": _s(r.get("body")), "vline": vehicle_line(r.get("model"), r.get("body")),
            "rep_model": _s(r.get("model")), "color": _color(_s(r.get("color"))),
            "status": _SEED_STATUS.get(_s(r.get("status")).lower(), ""),
            "customer": re.sub(r"\s*[?*]+\s*$", "", cust), "unconfirmed": bool(re.search(r"[?*]", cust)),
            "location": _LOC_FIX.get(loc.lower(), loc), "notes": _s(r.get("notes")),
        })
    return out


_PARSERS = {"orders": _parse_orders, "inventory": _parse_inventory, "tess": _parse_tess,
            "sales": _parse_sales, "repsheet": _parse_repsheet}


# ---------------------------------------------------------------- ingest

def ingest(blob: bytes, filename: str, who: str) -> dict:
    """Read every recognised sheet in an uploaded file, store it, and rebuild. Returns what was found."""
    found = []
    parsed: dict = {}
    for sheet, rows in _tables(blob, filename):
        kind, start, heads = _detect(rows)
        if not kind:
            continue
        rows_out = _PARSERS[kind](_records(rows, start, heads))
        if rows_out:
            parsed.setdefault(kind, []).extend(rows_out)
            found.append({"kind": kind, "label": SOURCE_LABELS[kind], "sheet": sheet, "rows": len(rows_out)})
    if not parsed:
        raise ValueError(
            "Nothing recognised in that file. Expected a Ford Pro order status export, "
            "the Ford inventory list, Tess's list or the Ford sold-VIN report."
        )
    now = dt.datetime.now().isoformat(timespec="seconds")
    with _LOCK:
        meta = _load("sources.json", {})
        for kind, rows in parsed.items():
            if kind == "orders":
                delivered = {r["vin"]: r for r in _load("orders_delivered.json", [])}
                fresh_open = []
                for r in rows:
                    if r["fp_primary"] == "Delivered":
                        if r["vin"]:
                            delivered[r["vin"]] = r
                    else:
                        fresh_open.append(r)
                _save("orders_delivered.json", list(delivered.values()))
                if fresh_open:
                    _save("orders_open.json", fresh_open)
            elif kind == "sales":
                sold = {r["vin"]: r for r in _load("sales.json", [])}
                sold.update({r["vin"]: r for r in rows})
                _save("sales.json", list(sold.values()))
            elif kind == "repsheet":
                _seed_from_repsheet(rows, who, now)
            else:
                _save(f"{kind}.json", rows)
            meta[kind] = {"at": now, "by": who, "file": os.path.basename(filename), "rows": len(rows)}
            if kind == "orders":  # the pull is only as fresh as its newest status change
                meta[kind]["asof"] = max((r["fp_updated"] for r in rows if r["fp_updated"]), default="")
        _save("sources.json", meta)
        units = rebuild()
        os.makedirs(SNAP_DIR, exist_ok=True)
        with open(os.path.join(SNAP_DIR, now[:10] + ".json"), "w", encoding="utf-8") as f:
            json.dump({"at": now, "units": units}, f, separators=(",", ":"))
    return {"found": found, "units": len(units)}


def _seed_from_repsheet(rows, who, now):
    """Rep-sheet values fill in only units nobody has edited in the app yet."""
    edits = _load("edits.json", {})
    for r in rows:
        if r["vin"] in edits:
            continue
        e = {k: r[k] for k in ("status", "customer", "unconfirmed", "location", "notes") if r[k]}
        if e:
            e.update(by="Rep sheet", at=now)
            edits[r["vin"]] = e
    _save("edits.json", edits)
    _save("repsheet.json", rows)


# ---------------------------------------------------------------- build

def _merge(unit: dict, row: dict, fields) -> None:
    for k in fields:
        if row.get(k) not in (None, "") and unit.get(k) in (None, ""):
            unit[k] = row[k]


_DESC = ("order", "year", "vline", "body_code", "body", "color", "engine", "wheelbase", "pep")
_ORDER_FIELDS = _DESC + ("fp_primary", "fp_secondary", "fp_updated", "eta_week", "end_user", "cust_ref", "po",
                         "order_received", "est_build", "produced", "shipped", "delivered")


def rebuild() -> list:
    """Recompute every unit from the stored sources and edits."""
    with _LOCK:
        delivered = _load("orders_delivered.json", [])
        open_rows = _load("orders_open.json", [])
        inv = _load("inventory.json", [])
        tess = _load("tess.json", [])
        sales = _load("sales.json", [])
        rep = _load("repsheet.json", [])
        edits = _load("edits.json", {})

        units: dict = {}

        def unit(key):
            return units.setdefault(key, {"key": key, "src": []})

        # Ford reuses order numbers: a cancelled row is noise when a live row shares order + year.
        live = {(r["order"], r["year"]) for r in open_rows + delivered if r["fp_primary"] != "Cancelled"}
        cancelled, no_vin = [], []
        by_vin: dict = {}
        for r in open_rows:
            if r["fp_primary"] == "Cancelled":
                if (r["order"], r["year"]) not in live:
                    cancelled.append(r)
            elif r["vin"]:
                by_vin[r["vin"]] = r
            else:
                no_vin.append(r)
        for r in delivered:  # delivered is the later word on any VIN
            by_vin[r["vin"]] = r
        for vin, r in by_vin.items():
            u = unit(vin)
            u["vin"] = vin
            _merge(u, r, _ORDER_FIELDS)
            u["src"].append("orders")
        for r in inv:
            u = unit(r["vin"])
            u["vin"] = r["vin"]
            u["fmc_status"], u["dol"] = r["fmc_status"], r["dol"]
            _merge(u, r, _DESC)
            u["src"].append("inventory")
        for r in rep:
            u = unit(r["vin"])
            u["vin"] = r["vin"]
            _merge(u, r, _DESC + ("rep_model",))
            u["src"].append("repsheet")
        for r in tess:
            u = unit(r["vin"])
            u["vin"] = r["vin"]
            u["tess_days"] = r["tess_days"]
            _merge(u, r, _DESC)
            u["src"].append("tess")
        for r in sales:
            u = unit(r["vin"])
            u["vin"] = r["vin"]
            u["sale_date"], u["sale_type"] = r["sale_date"], r["sale_type"]
            _merge(u, r, _DESC)
            u["src"].append("sales")
        seen: dict = {}
        for r in no_vin + cancelled:
            base = f"{r['order']}-{r['year']}"
            seen[base] = seen.get(base, 0) + 1
            u = unit(base if seen[base] == 1 else f"{base}-{seen[base]}")
            _merge(u, r, _ORDER_FIELDS)
            u["src"].append("orders")

        # An order that has since been given a VIN keeps what was entered against the order number.
        moved = False
        for key, u in units.items():
            if u.get("vin") and key not in edits and u.get("order"):
                old = f"{u['order']}-{u.get('year')}"
                if old in edits and old not in units:
                    edits[key] = edits.pop(old)
                    moved = True
        if moved:
            _save("edits.json", edits)

        out = []
        for key, u in units.items():
            e = edits.get(key, {})
            for f in EDIT_FIELDS:
                u[f] = e.get(f, False if f in ("unconfirmed", "upfit_complete") else "")
            u["edited_by"], u["edited_at"] = e.get("by", ""), e.get("at", "")
            u["customer_from_ford"] = False
            if not u["customer"] and u["status"] not in ("Available", "Released to Retail"):
                # Ford's end user when it names the agency, else the name the rep typed on the order.
                guess = u.get("end_user") or ""
                if guess.upper() in _NOT_A_CUSTOMER:
                    guess = (u.get("cust_ref") or "").title()
                if guess.upper() not in _NOT_A_CUSTOMER and guess.upper() != "STOCK":
                    u["customer"], u["customer_from_ford"] = guess, True
            u["stock"] = u["vin"][-7:] if u.get("vin") else ""
            u["days"] = u.get("dol") if u.get("dol") is not None else u.get("tess_days")
            u["on_tess"] = "tess" in u["src"]
            u["in_ford_inv"] = "inventory" in u["src"]
            u["stage"] = _stage(u)
            u["flags"] = _flags(u)
            out.append(u)
        order = {s: i for i, s in enumerate(STAGES + ["Cancelled"])}
        out.sort(key=lambda u: (order.get(u["stage"], 99), u.get("vline") or "", str(u.get("order") or "")))
        _CACHE["units"] = out
        return out


def _stage(u: dict) -> str:
    if u.get("fp_primary") == "Cancelled" and not u.get("vin"):
        return "Cancelled"
    if u.get("sale_date"):
        return "Sold"
    if u["status"] == "Billed Out":
        return "Billed Out"
    if u.get("fmc_status") == "STOCKINVENTORY" or u["on_tess"]:
        if u["upfit_complete"]:
            return "Ready for Delivery"
        return "At Upfitter" if u["location"] else "On Ground"
    fp = u.get("fp_primary", "")
    if u.get("fmc_status") == "STOCKINTRANSIT" or fp == "In Transit":
        return "In Transit"
    if u.get("fmc_status") == "STOCKINPLANT" or fp in ("In Production", "Built"):
        return "In Production"
    if fp == "Scheduled":
        return "Scheduled"
    if fp == "Unscheduled":
        return "Ordered"
    return "Unconfirmed"


def _flags(u: dict) -> list:
    """Reasons this unit belongs on the Needs Attention tab."""
    f = []
    stage, status = u["stage"], u["status"]
    here = stage in ("On Ground", "At Upfitter", "Ready for Delivery")
    if here and not status:
        f.append("On the ground with no status")
    if status in ("Sold", "Quoted", "Billed Out") and not u["customer"] and stage != "Sold":
        f.append(f"{status} with no customer")
    if u["unconfirmed"] and stage not in ("Sold", "Cancelled"):
        f.append("Customer unconfirmed")
    if u.get("sale_date") and u["on_tess"]:
        f.append("Reported sold to Ford, still on Tess's list")
    if u.get("sale_date") and u["in_ford_inv"] and u.get("fmc_status") == "STOCKINVENTORY":
        f.append("Reported sold to Ford, still in Ford inventory")
    if here and (u.get("days") or 0) >= 365:
        f.append("On the ground 365+ days")
    if stage == "Unconfirmed":
        f.append("Not in Ford inventory or on Tess's list")
    if here and u["on_tess"] and not u["in_ford_inv"]:
        f.append("On Tess's list, not in Ford inventory")
    if here and u.get("fmc_status") == "STOCKINVENTORY" and not u["on_tess"]:
        f.append("In Ford inventory, not on Tess's list")
    return f


# ---------------------------------------------------------------- read / edit

def units() -> list:
    with _LOCK:
        return _CACHE["units"] if "units" in _CACHE else rebuild()


def upfitters() -> list:
    saved = _load("upfitters.json", None)
    names = list(saved if saved is not None else DEFAULT_UPFITTERS)
    for u in units():
        if u["location"] and u["location"] not in names:
            names.append(u["location"])
    return names


def add_upfitter(name: str) -> list:
    name = re.sub(r"\s+", " ", name or "").strip()[:80]
    if not name:
        raise ValueError("Give the upfitter a name.")
    with _LOCK:
        names = upfitters()
        if name.lower() not in [n.lower() for n in names]:
            names.append(name)
        _save("upfitters.json", names)
    return names


def payload() -> dict:
    us = units()
    customers = sorted({u["customer"] for u in us if u["customer"]}, key=str.lower)
    return {"units": us, "sources": _load("sources.json", {}), "source_labels": SOURCE_LABELS,
            "statuses": STATUSES, "stages": STAGES, "upfitters": upfitters(), "customers": customers}


def apply_edit(keys, fields: dict, who: str) -> int:
    """Set the same fields on one or many units. Returns how many units changed."""
    clean = {}
    for k in EDIT_FIELDS:
        if k not in fields:
            continue
        v = fields[k]
        if k in ("unconfirmed", "upfit_complete"):
            clean[k] = bool(v)
        else:
            clean[k] = re.sub(r"[ \t]+", " ", str(v or "")).strip()[:500]
    if clean.get("status") and clean["status"] not in STATUSES:
        raise ValueError("Unknown status.")
    if not clean:
        return 0
    now = dt.datetime.now().isoformat(timespec="seconds")
    with _LOCK:
        known = {u["key"]: u for u in units()}
        edits = _load("edits.json", {})
        changed, log = 0, []
        for key in keys or []:
            cur = known.get(key)
            if not cur:
                continue
            diff = {k: v for k, v in clean.items() if cur.get(k) != v}
            if not diff:
                continue
            e = edits.setdefault(key, {})
            # A customer that was only Ford's suggestion becomes the unit's own once anything is saved.
            if cur.get("customer_from_ford") and "customer" not in diff and "customer" not in e:
                e["customer"] = cur["customer"]
            if diff.get("location") == "" and "upfit_complete" not in diff:
                diff["upfit_complete"] = False
            e.update(diff)
            e.update(by=who, at=now)
            for k, v in diff.items():
                log.append({"at": now, "by": who, "key": key, "field": k, "from": cur.get(k), "to": v})
            changed += 1
        if changed:
            _save("edits.json", edits)
            os.makedirs(DIR, exist_ok=True)
            with open(_path("history.jsonl"), "a", encoding="utf-8") as f:
                for row in log:
                    f.write(json.dumps(row) + "\n")
            rebuild()
    return changed


def history(key: str) -> list:
    out = []
    try:
        with open(_path("history.jsonl"), encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("key") == key:
                    out.append(row)
    except OSError:
        pass
    return out[::-1]


# ---------------------------------------------------------------- export

_EXPORT = (("Stock #", "stock"), ("VIN", "vin"), ("Order", "order"), ("Year", "year"), ("Vehicle", "vline"),
           ("Body", "body"), ("Color", "color"), ("Engine", "engine"), ("Stage", "stage"), ("Status", "status"),
           ("Customer", "customer"), ("Customer Unconfirmed", "unconfirmed"), ("Upfitter", "location"),
           ("Upfit Complete", "upfit_complete"), ("Notes", "notes"), ("Days in Stock", "days"),
           ("Ford Status", "fp_secondary"), ("Order Received", "order_received"), ("Est. Build", "est_build"),
           ("Sale Date", "sale_date"), ("Needs Attention", "flags"), ("Last Edited By", "edited_by"),
           ("Last Edited", "edited_at"))


def export_xlsx(keys=None) -> bytes:
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    rows = units()
    if keys:
        want = set(keys)
        rows = [u for u in rows if u["key"] in want]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Vehicles"
    ws.append([h for h, _ in _EXPORT])
    for u in rows:
        line = []
        for _, k in _EXPORT:
            v = u.get(k)
            if isinstance(v, bool):
                v = "Yes" if v else ""
            elif isinstance(v, list):
                v = "; ".join(v)
            line.append(v)
        ws.append(line)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F2933")
    for i, (h, k) in enumerate(_EXPORT, 1):
        width = max([len(h)] + [len(str(u.get(k) or "")) for u in rows[:300]])
        ws.column_dimensions[get_column_letter(i)].width = min(max(width + 2, 9), 44)
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = ws.dimensions
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
