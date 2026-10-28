"""A record of every bill: one row per vehicle, keyed by the Ford invoice number.

Each bill the Billing page sends is written to billing/log.csv under the data
folder (always), and appended to a Google Sheet when one is configured:

    FLEETQUOTE_SHEET_ID    the spreadsheet id (the long token in its URL)
    FLEETQUOTE_GOOGLE_KEY  a service-account key: the JSON itself, or a path to it
    FLEETQUOTE_SHEET_TAB   tab to append to            (default: the first tab)

The sheet must be shared with the service account's email as an Editor. Rows the
sheet could not take (offline, not shared yet) wait in billing/pending.json and go
out with the next bill, so nothing is lost and nothing is sent twice.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import threading

from . import config

COLUMNS = [
    "Logged at", "Invoice # (deal)", "Customer", "Address", "City / State / ZIP",
    "FIN code", "Contact", "Phone", "Email", "Fleet P.O. #", "Salesperson",
    "Stock #", "VIN", "Vehicle", "Selling price", "We owes",
    "Adjustments / programs", "Package file", "Logged by",
]

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SHEETS_API = os.environ.get("FLEETQUOTE_SHEETS_API", "https://sheets.googleapis.com/v4/spreadsheets")

_lock = threading.Lock()
_tab_cache: dict[str, str] = {}


def _dir() -> str:
    d = os.path.join(config.DATA_DIR, "billing")
    os.makedirs(d, exist_ok=True)
    return d


def _log_path() -> str:
    return os.path.join(_dir(), "log.csv")


def _pending_path() -> str:
    return os.path.join(_dir(), "pending.json")


def _num(v) -> float | None:
    try:
        return float(str(v or "").replace(",", "").replace("$", "").strip())
    except ValueError:
        return None


def _lines(items) -> str:
    out = []
    for r in items or []:
        desc = str(r.get("desc") or "").strip()
        cost = _num(r.get("cost"))
        if desc or cost:
            out.append(f"{desc} {'${:,.2f}'.format(cost)}".strip() if cost is not None else desc)
    return "; ".join(out)


def _key(row: list) -> str:
    return "|".join(str(row[i]).strip().upper() for i in (1, 12, 11))


def rows_for(data: dict, package: str = "", who: str = "", now: dt.datetime | None = None) -> list[list]:
    """One row per vehicle on the bill, in COLUMNS order."""
    stamp = (now or dt.datetime.now()).strftime("%Y-%m-%d %H:%M")
    weowes, adjs = _lines(data.get("weOwes")), _lines(data.get("adjs"))
    out = []
    for veh in data.get("vehicles") or []:
        if not any(str(veh.get(k) or "").strip() for k in ("vin", "stock", "invoiceNo", "description")):
            continue
        price = _num(veh.get("price"))
        out.append([
            stamp, str(veh.get("invoiceNo") or "").strip(),
            (data.get("company") or "").strip(), (data.get("address") or "").strip(),
            (data.get("csz") or "").strip(), (data.get("fin") or "").strip(),
            (data.get("contactName") or "").strip(), (data.get("contactPhone") or "").strip(),
            (data.get("contactEmail") or "").strip(), str(data.get("fleetPO") or "").strip(),
            (data.get("salesperson") or "").strip(), str(veh.get("stock") or "").strip(),
            str(veh.get("vin") or "").strip(), (veh.get("description") or "").strip(),
            price if price is not None else "", weowes, adjs, package, who,
        ])
    return out


def _read_log() -> list[list]:
    try:
        with open(_log_path(), newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
    except FileNotFoundError:
        return []
    return rows[1:] if rows and rows[0] == COLUMNS else rows


def _read_pending() -> list[list]:
    try:
        with open(_pending_path(), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return []


def _write_pending(rows: list[list]) -> None:
    tmp = _pending_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rows, f)
    os.replace(tmp, _pending_path())


# ---------------- Google Sheets ----------------

def configured() -> bool:
    return bool(os.environ.get("FLEETQUOTE_SHEET_ID", "").strip()
                and os.environ.get("FLEETQUOTE_GOOGLE_KEY", "").strip())


def _session():
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account

    raw = os.environ["FLEETQUOTE_GOOGLE_KEY"].strip()
    info = json.loads(raw) if raw.startswith("{") else json.load(open(raw, encoding="utf-8"))
    return AuthorizedSession(service_account.Credentials.from_service_account_info(info, scopes=SCOPES))


def _tab(session, sheet_id: str) -> str:
    want = os.environ.get("FLEETQUOTE_SHEET_TAB", "").strip()
    if want:
        return want
    if sheet_id not in _tab_cache:
        r = session.get(f"{SHEETS_API}/{sheet_id}", params={"fields": "sheets.properties.title"}, timeout=30)
        r.raise_for_status()
        _tab_cache[sheet_id] = r.json()["sheets"][0]["properties"]["title"]
    return _tab_cache[sheet_id]


def _append(rows: list[list]) -> None:
    sheet_id = os.environ["FLEETQUOTE_SHEET_ID"].strip()
    s = _session()
    tab = _tab(s, sheet_id).replace("'", "''")
    r = s.post(
        f"{SHEETS_API}/{sheet_id}/values/'{tab}'!A1:append",
        params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
        json={"values": rows},
        timeout=30,
    )
    if not r.ok:
        try:
            msg = r.json().get("error", {}).get("message", "")
        except ValueError:
            msg = ""
        raise RuntimeError(f"Google Sheets said {r.status_code}: {msg or r.text[:200]}")


def flush() -> dict:
    """Send anything waiting. Returns {"sent": n, "pending": n, "error": str}."""
    with _lock:
        waiting = _read_pending()
        if not waiting:
            return {"sent": 0, "pending": 0, "error": ""}
        if not configured():
            return {"sent": 0, "pending": len(waiting), "error": ""}
        try:
            _append(waiting)
        except Exception as exc:  # network, permissions, bad key: keep the rows for next time
            return {"sent": 0, "pending": len(waiting), "error": f"{type(exc).__name__}: {exc}"}
        _write_pending([])
        return {"sent": len(waiting), "pending": 0, "error": ""}


# ---------------- public ----------------

def log_bill(data: dict, package: str = "", who: str = "") -> dict:
    """Record a bill. Vehicles already logged (same invoice #, VIN and stock) are skipped."""
    new_rows = rows_for(data, package, who)
    if not new_rows:
        raise ValueError("Add at least one vehicle before logging a bill.")
    with _lock:
        seen = {_key(r) for r in _read_log() if len(r) > 12}
        fresh = []
        for r in new_rows:
            k = _key(r)
            if k not in seen:
                seen.add(k)
                fresh.append(r)
        if fresh:
            path = _log_path()
            new_file = not os.path.exists(path)
            with open(path, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if new_file:
                    w.writerow(COLUMNS)
                w.writerows(fresh)
            _write_pending(_read_pending() + fresh)
    result = flush()
    return {
        "logged": len(fresh),
        "duplicates": len(new_rows) - len(fresh),
        "sheet": configured(),
        "sent": result["sent"],
        "pending": result["pending"],
        "error": result["error"],
    }


def csv_bytes() -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(COLUMNS)
    w.writerows(_read_log())
    return buf.getvalue().encode("utf-8-sig")
