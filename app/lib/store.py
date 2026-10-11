"""On-disk schema for fleet quotes and the reusable upfitter-quote library.

Layout (rooted at the Quotes folder):

    quotes/<year>/<quote-id>__<customer-slug>/
        quote.json          canonical record (everything needed to re-render)
        quote.pdf           generated SWC sales quote
        spec/               original Ford spec proposal(s), verbatim
        attachments/        upfitter PDFs snapshotted onto this quote

    upfit/<upfitter-slug>/<year>/<reference-slug>/
        upfit.json          metadata incl. `applies_to` chassis matching rules
        <source files>      the upfitter's own quote PDF etc.

The upfit tree is a *library*: records live independently of any one quote so
the same body quote can be pulled into many deals, and `applies_to` is what
lets the app say "these upfits fit this chassis".
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil

from . import config

HERE = config.APP_DIR
ROOT = config.ROOT
QUOTES_DIR = config.QUOTES_DIR
UPFIT_DIR = config.UPFIT_DIR


def slug(s: str, maxlen: int = 48) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", (s or "").strip()).strip("-").lower()
    return (s[:maxlen].strip("-")) or "untitled"


def _read_json(p):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_json(p, data):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, p)


# --------------------------------------------------------------------------
# Quotes
# --------------------------------------------------------------------------

def next_quote_id(year: str | None = None, initials: str = "") -> str:
    """Next free quote number.

    With initials the sequence is per person -- Q-2026-JM-0003 -- so two people
    quoting at the same moment against the shared Drive folder cannot produce
    the same folder name. Without them it falls back to the plain Q-2026-0003.
    """
    year = str(year or dt.date.today().year)
    initials = re.sub(r"[^A-Za-z0-9]", "", (initials or "")).upper()
    ydir = os.path.join(QUOTES_DIR, year)
    prefix = f"Q-{year}-{initials}-" if initials else f"Q-{year}-"
    pattern = re.compile(rf"^{re.escape(prefix)}(\d+)")

    n = 0
    if os.path.isdir(ydir):
        for name in os.listdir(ydir):
            m = pattern.match(name)
            if m:
                n = max(n, int(m.group(1)))
    return f"{prefix}{n + 1:04d}"


def quote_dir(quote_id: str, customer: str = "", create: bool = False) -> str | None:
    m = re.match(r"^Q-(\d{4})-", quote_id or "")
    year = m.group(1) if m else str(dt.date.today().year)
    ydir = os.path.join(QUOTES_DIR, year)
    if os.path.isdir(ydir):
        for name in os.listdir(ydir):
            if name.startswith(quote_id):
                return os.path.join(ydir, name)
    if create:
        d = os.path.join(ydir, f"{quote_id}__{slug(customer)}")
        os.makedirs(os.path.join(d, "spec"), exist_ok=True)
        os.makedirs(os.path.join(d, "attachments"), exist_ok=True)
        return d
    return None


def _strip_private(obj):
    """Drop UI-only keys (``_dir``, ``_fit`` ...) before they reach disk."""
    if isinstance(obj, dict):
        return {k: _strip_private(v) for k, v in obj.items() if not k.startswith("_")}
    if isinstance(obj, list):
        return [_strip_private(v) for v in obj]
    return obj


def save_quote(quote: dict, spec_files: list | None = None) -> dict:
    """Persist a quote record; returns it with `quote_id` and paths filled in."""
    quote = _strip_private(quote)
    qid = quote.get("quote_id") or next_quote_id(quote.get("contract_year"))
    quote["quote_id"] = qid
    customer = quote.get("customer", {}).get("org") or quote.get("customer", {}).get("name", "")
    d = quote_dir(qid, customer, create=True)

    now = dt.datetime.now().isoformat(timespec="seconds")
    quote.setdefault("created_at", now)
    quote["updated_at"] = now

    for src in spec_files or []:
        if os.path.isfile(src):
            dest = os.path.join(d, "spec", os.path.basename(src))
            if os.path.abspath(src) != os.path.abspath(dest):
                shutil.copy2(src, dest)

    _write_json(os.path.join(d, "quote.json"), quote)
    quote["_dir"] = d
    return quote


def load_quote(quote_id: str):
    d = quote_dir(quote_id)
    if not d:
        return None
    q = _read_json(os.path.join(d, "quote.json"))
    if q:
        q["_dir"] = d
        q["_has_pdf"] = os.path.isfile(os.path.join(d, "quote.pdf"))
    return q


def list_quotes(limit: int = 500) -> list:
    out = []
    if not os.path.isdir(QUOTES_DIR):
        return out
    for year in sorted(os.listdir(QUOTES_DIR), reverse=True):
        ydir = os.path.join(QUOTES_DIR, year)
        if not os.path.isdir(ydir):
            continue
        for name in sorted(os.listdir(ydir), reverse=True):
            q = _read_json(os.path.join(ydir, name, "quote.json"))
            if not q:
                continue
            out.append(
                {
                    "quote_id": q.get("quote_id"),
                    "customer": q.get("customer", {}),
                    "vehicle_title": q.get("vehicle", {}).get("title", ""),
                    "total": q.get("totals", {}).get("grand_total"),
                    "qty": q.get("vehicle", {}).get("quantity", 1),
                    "updated_at": q.get("updated_at"),
                    "has_pdf": os.path.isfile(os.path.join(ydir, name, "quote.pdf")),
                    "dir": os.path.join(ydir, name),
                }
            )
            if len(out) >= limit:
                return out
    return out


# --------------------------------------------------------------------------
# Upfit library
# --------------------------------------------------------------------------

UPFIT_TEMPLATE = {
    "id": "",
    "upfitter": "",
    "reference": "",
    "title": "",
    "body_type": "",
    "year": "",
    "quote_date": "",
    "valid_until": "",
    "price": None,
    "line_items": [],
    "applies_to": {
        "model_codes": [],     # e.g. ["W3H", "W4H"] -- empty means "any"
        "chassis": "",         # free text, e.g. "F-350/F-450 Chassis Cab DRW"
        "cab": "",             # Regular / SuperCab / Crew Cab
        "wheelbase": [],       # e.g. ["179"]
        "cab_to_axle": [],     # e.g. ["60"]
        "drw": None,           # True / False / None = either
    },
    "notes": "",
    "tags": [],
    "files": [],
}


_FAMILIES = (
    ("Bronco Sport", r"bronco\s+sport"), ("Bronco", r"bronco"), ("Expedition", r"expedition"),
    ("Explorer", r"explorer"), ("Mustang", r"mustang"), ("Maverick", r"maverick"),
    ("Ranger", r"ranger"), ("Transit", r"transit"),
    ("Super Duty", r"super\s*duty|\bf-?[2-7]\d0\b|\bsd\b"), ("F-150", r"f-?150"),
)


def model_family(text: str) -> str:
    """'2027 F-250 4x4 SD Super Cab ...' -> 'Super Duty'; '' when unrecognised."""
    for name, pat in _FAMILIES:
        if re.search(pat, text or "", re.I):
            return name
    return ""


def upfit_dir(upfitter: str, year: str, reference: str, create=False) -> str:
    d = os.path.join(UPFIT_DIR, slug(upfitter), str(year or "undated"), slug(reference))
    if create:
        os.makedirs(d, exist_ok=True)
    return d


def save_upfit(rec: dict, source_files: list | None = None,
               original_id: str = "") -> dict:
    """Create or update a library record.

    Editing the upfitter, year or reference changes where the record lives, so
    pass `original_id` when saving an edit: the existing folder (and the
    upfitter's own PDFs inside it) is moved rather than left orphaned.
    """
    base = dict(UPFIT_TEMPLATE)
    base.update({k: v for k, v in rec.items() if k in UPFIT_TEMPLATE})
    applies = dict(UPFIT_TEMPLATE["applies_to"])
    applies.update(rec.get("applies_to") or {})
    # The edit form only knows the original fields; keep the catalog's extras.
    prev = (load_upfit(original_id) or {}).get("applies_to", {}) if original_id else {}
    for k in ("models", "bed", "years"):
        if k in prev and k not in (rec.get("applies_to") or {}):
            applies[k] = prev[k]
    base["applies_to"] = applies

    base["year"] = str(base.get("year") or dt.date.today().year)
    base["reference"] = base.get("reference") or dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    base["id"] = f"{slug(base['upfitter'])}/{base['year']}/{slug(base['reference'])}"

    # Line items, when present, are the source of truth for the price.
    items = [i for i in (base.get("line_items") or []) if str(i.get("description", "")).strip()]
    base["line_items"] = items
    if items:
        total = 0.0
        for i in items:
            try:
                total += float(i.get("price") or 0)
            except (TypeError, ValueError):
                pass
        base["price"] = round(total, 2)

    d = upfit_dir(base["upfitter"], base["year"], base["reference"], create=False)

    # Renaming: move the old folder across, keeping its attachments.
    old = load_upfit(original_id) if original_id else None
    if old and old.get("_dir") and os.path.abspath(old["_dir"]) != os.path.abspath(d):
        if os.path.exists(d):
            raise ValueError(
                f"An upfit already exists at {base['id']}. "
                "Change the reference, or edit that record instead."
            )
        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.move(old["_dir"], d)
        _prune_empty(os.path.dirname(old["_dir"]))

    os.makedirs(d, exist_ok=True)

    files = list(base.get("files") or [])
    for src in source_files or []:
        if os.path.isfile(src):
            dest = os.path.join(d, os.path.basename(src))
            if os.path.abspath(src) != os.path.abspath(dest):
                shutil.copy2(src, dest)
            if os.path.basename(src) not in files:
                files.append(os.path.basename(src))
    # Drop names whose file is gone (removed by hand, or via the UI).
    base["files"] = [f for f in files if os.path.isfile(os.path.join(d, f))]

    base.setdefault("created_at", (old or {}).get("created_at")
                    or dt.datetime.now().isoformat(timespec="seconds"))
    base["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")

    _write_json(os.path.join(d, "upfit.json"), base)
    base["_dir"] = d
    return base


def _prune_empty(path: str) -> None:
    """Remove now-empty upfitter/year folders left behind by a rename."""
    root = os.path.abspath(UPFIT_DIR)
    path = os.path.abspath(path)
    while path.startswith(root) and path != root:
        try:
            if os.listdir(path):
                return
            os.rmdir(path)
        except OSError:
            return
        path = os.path.dirname(path)


def delete_upfit(upfit_id: str) -> bool:
    """Delete a library record and its attachments. Quotes already written
    keep their own copy of the line, so this never rewrites past quotes."""
    rec = load_upfit(upfit_id)
    if not rec or not rec.get("_dir"):
        return False
    d = os.path.abspath(rec["_dir"])
    if not d.startswith(os.path.abspath(UPFIT_DIR)):
        return False
    parent = os.path.dirname(d)
    shutil.rmtree(d)
    _prune_empty(parent)
    return True


def delete_upfit_file(upfit_id: str, filename: str) -> dict | None:
    """Detach one attached document from a record."""
    rec = load_upfit(upfit_id)
    if not rec:
        return None
    name = os.path.basename(filename or "")
    target = os.path.join(rec["_dir"], name)
    if name and os.path.isfile(target) and name in (rec.get("files") or []):
        os.remove(target)
    rec["files"] = [f for f in (rec.get("files") or []) if f != name]
    _write_json(os.path.join(rec["_dir"], "upfit.json"), _strip_private(rec))
    return rec


def upfit_usage() -> dict:
    """{upfit_id: [{quote_id, customer, total}, ...]} across every saved quote."""
    usage: dict[str, list] = {}
    for meta in list_quotes():
        q = _read_json(os.path.join(meta["dir"], "quote.json")) or {}
        for u in q.get("upfits") or []:
            uid = u.get("upfit_id")
            if not uid:
                continue
            usage.setdefault(uid, []).append(
                {
                    "quote_id": q.get("quote_id"),
                    "customer": (q.get("customer") or {}).get("org")
                    or (q.get("customer") or {}).get("name", ""),
                    "price": u.get("price"),
                }
            )
    return usage


def _expiry(rec: dict) -> tuple:
    """(status, days_left) from valid_until. Accepts MM/DD/YYYY or YYYY-MM-DD."""
    raw = (rec.get("valid_until") or "").strip()
    if not raw:
        return "none", None
    parsed = None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y", "%d/%m/%Y"):
        try:
            parsed = dt.datetime.strptime(raw, fmt).date()
            break
        except ValueError:
            continue
    if parsed is None:
        return "unknown", None
    days = (parsed - dt.date.today()).days
    if days < 0:
        return "expired", days
    if days <= 30:
        return "soon", days
    return "ok", days


def list_upfits(with_usage: bool = False) -> list:
    out = []
    if not os.path.isdir(UPFIT_DIR):
        return out
    for dirpath, _dirs, files in os.walk(UPFIT_DIR):
        if "upfit.json" in files:
            rec = _read_json(os.path.join(dirpath, "upfit.json"))
            if rec:
                rec["_dir"] = dirpath
                status, days = _expiry(rec)
                rec["_expiry"] = status
                rec["_days_left"] = days
                rec["_files"] = [
                    f for f in (rec.get("files") or [])
                    if os.path.isfile(os.path.join(dirpath, f))
                ]
                out.append(rec)
    if with_usage:
        usage = upfit_usage()
        for rec in out:
            rec["_used_by"] = usage.get(rec.get("id"), [])
    out.sort(key=lambda r: (r.get("upfitter", "").lower(), r.get("year", ""),
                            r.get("reference", "")))
    return out


def load_upfit(upfit_id: str):
    for rec in list_upfits():
        if rec.get("id") == upfit_id:
            return rec
    return None


# --- "which upfits apply to this chassis?" ---------------------------------

def _wb_tokens(title: str) -> set[str]:
    return set(re.findall(r"(\d{3})\"?\s*WB", title or "", re.I)) | set(
        re.findall(r"\b(\d{3})\b", title or "")
    )


def applicable_upfits(spec: dict) -> list:
    """Rank library upfits by how well they fit a parsed spec proposal.

    Returns records annotated with `_fit` = "exact" | "likely" | "possible"
    and `_why` explaining the match. Records with an explicit conflict
    (wrong model code, wrong wheel configuration) are dropped.
    """
    title = spec.get("vehicle_title", "") or ""
    model = (spec.get("model_code") or "").upper()
    is_drw = bool(re.search(r"\bDRW\b", title, re.I))
    is_srw = bool(re.search(r"\bSRW\b", title, re.I))
    cab = ""
    for c in ("Crew Cab", "SuperCrew", "SuperCab", "Super Cab", "Regular Cab"):
        if re.search(c, title, re.I):
            cab = "Crew Cab" if c == "SuperCrew" else c
            break
    family = model_family(title)
    ym = re.search(r"\b(20\d\d)\b", title)
    model_year = int(ym.group(1)) if ym else None
    bm = re.search(r"(\d+(?:\.\d+)?)\s*['\u2019]?\s*(?:box|bed)\b", title, re.I)
    title_bed = float(bm.group(1)) if bm else None
    wbs = _wb_tokens(title)

    out = []
    for rec in list_upfits():
        a = rec.get("applies_to") or {}
        score, why, conflict = 0, [], False

        codes = [c.upper() for c in (a.get("model_codes") or [])]
        if codes:
            if model and model in codes:
                score += 10
                why.append(f"model {model}")
            elif model:
                conflict = True

        if a.get("drw") is not None and (is_drw or is_srw):
            if bool(a["drw"]) == is_drw:
                score += 4
                why.append("DRW" if is_drw else "SRW")
            else:
                conflict = True

        rec_cab = (a.get("cab") or "").lower().replace(" ", "")
        if rec_cab and cab:
            if rec_cab == cab.lower().replace(" ", ""):
                score += 3
                why.append(cab)
            else:
                conflict = True

        rec_wb = {str(x) for x in (a.get("wheelbase") or [])}
        if rec_wb:
            if rec_wb & wbs:
                score += 3
                why.append(f"{sorted(rec_wb & wbs)[0]}\" WB")
            elif wbs:
                conflict = True

        models = a.get("models") or []
        if models and family:
            if family in models:
                score += 6
                why.append(family)
            else:
                conflict = True
        if a.get("bed") and title_bed is not None:
            if abs(float(a["bed"]) - title_bed) < 0.01:
                score += 3
                why.append(f"{a['bed']}' bed")
            else:
                conflict = True
        yrs = a.get("years") or []
        if len(yrs) == 2 and model_year:
            if yrs[0] <= model_year <= yrs[1]:
                score += 1
            else:
                conflict = True

        if conflict:
            continue
        if not codes and not rec_wb and not rec_cab and not models:
            why.append("no chassis restriction")

        rec = dict(rec)
        rec["_score"] = score
        rec["_fit"] = "exact" if score >= 13 else ("likely" if score >= 7 else "possible")
        rec["_why"] = ", ".join(why) or "unrestricted"
        out.append(rec)

    out.sort(key=lambda r: r["_score"], reverse=True)
    return out


if __name__ == "__main__":
    import json as _j

    print("quotes dir :", QUOTES_DIR)
    print("upfit dir  :", UPFIT_DIR)
    print("next id    :", next_quote_id())
    print("quotes     :", len(list_quotes()))
    print("upfits     :", len(list_upfits()))
