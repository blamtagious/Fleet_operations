"""Fleet Identification Number (FIN) code lookup by agency name."""

from __future__ import annotations

import os
import re

import openpyxl

from . import config

HERE = config.APP_DIR
ROOT = config.ROOT
FIN_XLSX = config.FIN_XLSX

_cache: list | None = None


def load(force=False) -> list:
    global _cache
    if _cache is not None and not force:
        return _cache
    wb = openpyxl.load_workbook(FIN_XLSX, data_only=True)
    ws = wb.worksheets[0]
    out = []
    for code, agency in ws.iter_rows(min_row=2, max_col=2, values_only=True):
        if not code or not agency:
            continue
        out.append({"fin": str(code).strip(), "agency": str(agency).strip()})
    _cache = out
    return out


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())


def _token_hit(token: str, words: list) -> bool:
    """Agency names in the FIN sheet are truncated (~28 chars), so a search
    token may be longer than the stored word: "district" vs "dist"."""
    for w in words:
        if w.startswith(token) or (len(w) >= 3 and token.startswith(w)):
            return True
    return False


def search(term: str, limit: int = 25) -> list:
    """Truncation-tolerant token search over agency names and FIN codes."""
    rows = load()
    term = _norm(term).strip()
    if not term:
        return []
    tokens = [t for t in term.split() if t]
    scored = []
    for r in rows:
        agency = _norm(r["agency"])
        words = agency.split() + [r["fin"].lower()]
        hits = sum(1 for t in tokens if _token_hit(t, words))
        if hits != len(tokens):
            continue
        exact = 1 if agency == term else 0
        lead = 1 if words and words[0].startswith(tokens[0]) else 0
        scored.append((exact, lead, -abs(len(agency) - len(term)), r))
    scored.sort(key=lambda t: t[:3], reverse=True)
    return [r for *_x, r in scored[:limit]]


def by_code(code: str):
    code = (code or "").strip().upper()
    for r in load():
        if r["fin"].upper() == code:
            return r
    return None


if __name__ == "__main__":
    import json, sys

    print(json.dumps(search(" ".join(sys.argv[1:]) or "southeast tennessee"), indent=2))
