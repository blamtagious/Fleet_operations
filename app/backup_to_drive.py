#!/usr/bin/env python3
"""Pull the hosted app's quotes and upfit library down into Google Drive.

Quotes live on the server once the app is hosted. This keeps a copy in the
Drive folder so there is always a local, openable set of files that does not
depend on the host staying alive.

Setup (once, on Jason's Mac):

    python3 app/backup_to_drive.py --setup

It asks for the app's address and a login, and stores them in
~/.fleetquote/backup.json (0600) -- deliberately outside the Drive folder so
the password never syncs to anyone else.

Then run it any time:

    python3 app/backup_to_drive.py

It writes into  <Quotes>/Backups/ :
    latest/                  unpacked, browse it like any folder
    fleetquote-YYYY-MM-DD.zip   dated archives, newest 30 kept
"""

from __future__ import annotations

import argparse
import datetime as dt
import getpass
import json
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.dont_write_bytecode = True

from lib import config  # noqa: E402

CONF = os.path.join(os.path.expanduser("~"), ".fleetquote", "backup.json")
DEST = os.path.join(config.ROOT, "Backups")
KEEP = 30


def setup():
    print("Backup setup — this is stored on this Mac only, never in Drive.\n")
    url = input("  App address (e.g. https://quotes.example.com): ").strip().rstrip("/")
    user = input("  Username: ").strip()
    pw = getpass.getpass("  Password: ")
    if not url or not user or not pw:
        sys.exit("  All three are required.")
    os.makedirs(os.path.dirname(CONF), exist_ok=True)
    tmp = CONF + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"url": url, "username": user, "password": pw}, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, CONF)
    print(f"\n  Saved to {CONF}")
    print("  Test it now with:  python3 app/backup_to_drive.py")


def load_conf() -> dict:
    try:
        with open(CONF, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        sys.exit(
            "  Not set up yet. Run:  python3 app/backup_to_drive.py --setup"
        )


def fetch(conf: dict) -> bytes:
    """Sign in, then download the archive."""
    jar = urllib.request.HTTPCookieProcessor()
    opener = urllib.request.build_opener(jar)

    form = urllib.parse.urlencode(
        {"username": conf["username"], "password": conf["password"]}
    ).encode()
    try:
        opener.open(conf["url"] + "/login", form, timeout=60)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            sys.exit("  Sign-in refused. Check the username and password.")
        raise

    if not any(c.name == "fq_session" for c in jar.cookiejar):
        sys.exit("  Sign-in did not take. Check the username and password.")

    with opener.open(conf["url"] + "/api/backup", timeout=300) as r:
        if r.headers.get("Content-Type", "").split(";")[0] != "application/zip":
            sys.exit("  The server did not return an archive. Is the address right?")
        return r.read()


def store(data: bytes) -> str:
    os.makedirs(DEST, exist_ok=True)
    stamp = dt.date.today().isoformat()
    archive = os.path.join(DEST, f"fleetquote-{stamp}.zip")

    with open(archive + ".part", "wb") as f:
        f.write(data)
    os.replace(archive + ".part", archive)

    # Unpack to a staging dir first, so a failure never leaves latest/ half-written.
    latest = os.path.join(DEST, "latest")
    with tempfile.TemporaryDirectory(dir=DEST) as tmp:
        with zipfile.ZipFile(archive) as z:
            z.extractall(tmp)
        swap = latest + ".old"
        if os.path.isdir(latest):
            os.rename(latest, swap)
        shutil.move(tmp, latest)
        os.makedirs(tmp, exist_ok=True)   # keep TemporaryDirectory happy
        if os.path.isdir(swap):
            shutil.rmtree(swap, ignore_errors=True)

    # Age out old archives.
    zips = sorted(
        f for f in os.listdir(DEST)
        if f.startswith("fleetquote-") and f.endswith(".zip")
    )
    for old in zips[:-KEEP]:
        os.remove(os.path.join(DEST, old))

    return archive


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--setup", action="store_true")
    args = ap.parse_args()
    if args.setup:
        return setup()

    conf = load_conf()
    print(f"  Pulling from {conf['url']} …")
    data = fetch(conf)
    path = store(data)
    with zipfile.ZipFile(path) as z:
        n = len(z.namelist())
    print(f"  {n} files, {len(data) / 1024:.0f} KB")
    print(f"  Archive : {path}")
    print(f"  Unpacked: {os.path.join(DEST, 'latest')}")


if __name__ == "__main__":
    main()
