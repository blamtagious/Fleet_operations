"""Turn a parsed spec proposal into an editable draft quote record."""

from __future__ import annotations

import datetime as dt
import json
import os

from . import config, finlookup, pricing, store

HERE = config.APP_DIR
CONFIG = config.BUNDLED_CONFIG


def _cfg(name):
    with open(config.config_path(name), encoding="utf-8") as f:
        return json.load(f)


def settings():
    return _cfg("settings.json")


def sellers():
    return _cfg("sellers.json")


def default_seller():
    ss = sellers()
    for s in ss:
        if s.get("default"):
            return s
    return ss[0] if ss else {}


def draft_from_spec(spec: dict, contract_year: str = "") -> dict:
    """Build a draft quote. Every price stays editable in the UI."""
    st = settings()
    year = contract_year or spec.get("year") or str(dt.date.today().year)

    best, candidates = pricing.match(
        spec.get("model_code", ""),
        spec.get("order_code", ""),
        spec.get("engine", ""),
        year,
    )

    # Auto-resolve the FIN code from the customer organisation on the proposal.
    fin, fin_matches = "", []
    org = spec.get("customer_org", "")
    if org:
        fin_matches = finlookup.search(org, limit=5)
        if len(fin_matches) == 1:
            fin = fin_matches[0]["fin"]

    # Options: everything Ford actually charged for, minus the base vehicle.
    #
    # Important: a state bid line such as "W3H-640A ... 6.7L V8 Diesel" is
    # priced for that engine already. Re-adding the $10,995 diesel option from
    # the proposal would double-count it, so options that the matched bid row
    # already covers are flagged and default to excluded.
    sheet_desc = best["description"] if best else ""
    sheet_engine = pricing._engine_tokens(sheet_desc)

    options = []
    for o in spec.get("options", []):
        if o.get("section") == "Base Vehicle":
            continue
        if not o.get("is_priced"):
            continue

        flags, include = [], True
        if best and o.get("description", "").startswith("Engine:"):
            opt_engine = pricing._engine_tokens(o.get("description", ""))
            if opt_engine and sheet_engine and (opt_engine & sheet_engine):
                include = False
                flags.append(
                    "Already included in the state bid price for "
                    f"{best['model_number']} ({sheet_desc})."
                )

        options.append(
            {
                "code": o.get("code", ""),
                "description": o.get("description", ""),
                "section": o.get("section", ""),
                "msrp": o.get("msrp"),
                "price": o.get("msrp"),   # default to MSRP; editable per line
                "include": include,
                "flags": flags,
            }
        )

    # Vehicle line: quote at the state bid price when we found the model.
    bid = best["bid"] if best else None
    msrp = spec.get("base_price")
    veh_price = bid if bid is not None else msrp

    detail_bits = []
    if spec.get("order_code"):
        detail_bits.append(f"Order Code {spec['order_code']}")
    if spec.get("engine"):
        detail_bits.append(spec["engine"])
    if spec.get("exterior_color"):
        detail_bits.append(spec["exterior_color"])

    quote = {
        "quote_id": "",
        "quote_date": dt.date.today().strftime("%m/%d/%Y"),
        "contract_year": year,
        "status": "draft",
        "dealer": st["dealer"],
        "seller": default_seller(),
        "customer": {
            "name": spec.get("prepared_for", ""),
            "title": spec.get("customer_title", ""),
            "org": org,
            "fin": fin,
        },
        "vehicle": {
            "title": spec.get("vehicle_title", ""),
            "model_code": spec.get("model_code", ""),
            "order_code": spec.get("order_code", ""),
            "model_number": best["model_number"] if best else "",
            "price_level": spec.get("price_level", ""),
            "engine": spec.get("engine", ""),
            "exterior_color": spec.get("exterior_color", ""),
            "interior_color": spec.get("interior_color", ""),
            "quantity": 1,
            "lines": [
                {
                    "line": "1",
                    "description": spec.get("vehicle_title", ""),
                    "detail": " | ".join(detail_bits),
                    "price": veh_price,
                    "msrp": msrp,
                    "bid": bid,
                }
            ],
        },
        "pricing_source": {
            "matched": bool(best),
            "model_number": best["model_number"] if best else "",
            "description": best["description"] if best else "",
            "sheet_msrp": best["msrp"] if best else None,
            "sheet_bid": bid,
            "discount": best["discount"] if best else None,
            "year": best["year"] if best else year,
            "candidates": candidates[:8],
        },
        "fin_matches": fin_matches,
        "options": options,
        "upfits": [],
        "adjustments": [],
        "destination": spec.get("destination"),
        # State bid prices are quoted delivered, so destination stays off by
        # default; toggle it on if the contract line you are using excludes it.
        "include_destination": False,
        "warnings": ([] if best else [
            "No State of Tennessee bid row matched this model - the vehicle "
            "line is priced at proposal MSRP. Pick a contract line manually."
        ]),
        "notes": settings()["contract"]["default_note"],
        "spec": {
            "base_msrp": spec.get("base_price"),
            "subtotal": spec.get("subtotal"),
            "destination": spec.get("destination"),
            "total": spec.get("total"),
            "summary": spec.get("summary", {}),
            "prepared_by": spec.get("prepared_by", ""),
            "spec_date": spec.get("spec_date", ""),
            "all_options": spec.get("options", []),
        },
    }

    return recalc(quote)


def recalc(q: dict) -> dict:
    """Recompute totals from the editable line items."""

    def f(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return 0.0

    veh = sum(f(l.get("price")) for l in q.get("vehicle", {}).get("lines", []))
    opts = sum(f(o.get("price")) for o in q.get("options", []) if o.get("include", True))
    upf = sum(f(u.get("price")) for u in q.get("upfits", []) if u.get("include", True))
    dest = f(q.get("destination")) if q.get("include_destination") else 0.0
    adj = sum(f(a.get("amount")) for a in q.get("adjustments", []))

    unit = veh + opts + upf + dest + adj
    qty = int(q.get("vehicle", {}).get("quantity") or 1)

    q["totals"] = {
        "vehicle": round(veh, 2),
        "options": round(opts, 2),
        "upfitting": round(upf, 2),
        "destination": round(dest, 2),
        "adjustments": round(adj, 2),
        "unit_total": round(unit, 2),
        "quantity": qty,
        "grand_total": round(unit * qty, 2),
    }
    return q
