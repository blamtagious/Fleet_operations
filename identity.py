"""Per-machine identity, stored OUTSIDE the shared Drive folder.

Three people run their own copy of this app against one synced folder, so the
app must not keep per-person settings inside it -- Google Drive would sync one
person's settings onto everyone else and generate conflicted copies.

This lives in the OS home directory instead:

    ~/.fleetquote/user.json        (macOS / Linux)
    C:\\Users\\<name>\\.fleetquote\\user.json   (Windows)

The initials it holds are what keep quote numbers from colliding: Jason writes
Q-2026-JM-0001 while Craig writes Q-2026-CB-0001, so two people quoting at the
same time can never land on the same folder name.
"""

from __future__ import annotations

import json
import os
import re

HOME = os.path.expanduser("~")
CONF_DIR = os.path.join(HOME, ".fleetquote")
CONF_FILE = os.path.join(CONF_DIR, "user.json")


def _guess_initials() -> str:
    """A sane first guess from the OS account name, e.g. 'jason.mccullough'."""
    raw = (os.environ.get("USER") or os.environ.get("USERNAME") or "").strip()
    parts = [p for p in re.split(r"[^A-Za-z]+", raw) if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    if parts:
        return parts[0][:2].upper()
    return ""


def load() -> dict:
    data = {}
    try:
        with open(CONF_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}

    return {
        "initials": (data.get("initials") or "").upper(),
        "seller_id": data.get("seller_id") or "",
        "configured": bool(data.get("initials")),
        "suggested_initials": _guess_initials(),
        "path": CONF_FILE,
    }


def save(initials: str, seller_id: str = "") -> dict:
    initials = re.sub(r"[^A-Za-z0-9]", "", (initials or "")).upper()[:4]
    if not initials:
        raise ValueError("Initials are required.")
    os.makedirs(CONF_DIR, exist_ok=True)
    tmp = CONF_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"initials": initials, "seller_id": seller_id}, f, indent=2)
    os.replace(tmp, CONF_FILE)
    return load()


if __name__ == "__main__":
    print(json.dumps(load(), indent=2))
