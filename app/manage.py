#!/usr/bin/env python3
"""Admin tasks for the hosted deployment.

    python3 app/manage.py secret                 generate a FLEETQUOTE_SECRET
    python3 app/manage.py adduser craig          create or reset a login
    python3 app/manage.py listusers
    python3 app/manage.py deluser craig
    python3 app/manage.py backup out.zip         archive quotes/ and upfit/

Passwords are asked for interactively so they never land in shell history.
"""

from __future__ import annotations

import getpass
import os

try:                      # arrow keys and backspace in input() prompts
    import readline       # noqa: F401
except ImportError:
    pass

import re
import secrets
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.dont_write_bytecode = True

from lib import auth, config, store  # noqa: E402


def cmd_secret():
    print(secrets.token_urlsafe(48))


def _ask(prompt: str, current: str = "") -> str:
    """Prompt, then scrub stray escape sequences from arrow keys."""
    raw = input(f"  {prompt} [{current}]: ")
    cleaned = re.sub(r"\x1b\[[0-9;]*[A-Za-z]|[\x00-\x1f\x7f]", "", raw).strip()
    return cleaned or current


def cmd_adduser(argv):
    """Interactive by default; fully non-interactive with flags.

    The flag form exists so the whole thing can be done in one command from
    anywhere -- `railway ssh python3 app/manage.py adduser craig --name ...`
    -- instead of needing an interactive shell inside the container.
    """
    opts, pos = {}, []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--name", "--initials", "--seller"):
            if i + 1 >= len(argv):
                sys.exit(f"  {a} needs a value.")
            opts[a.lstrip("-")] = argv[i + 1]
            i += 2
        elif a == "--generate":
            opts["generate"] = True
            i += 1
        else:
            pos.append(a)
            i += 1

    if not pos:
        sys.exit(
            "usage: manage.py adduser <username>\n"
            "       manage.py adduser <username> --name 'Full Name' "
            "--initials XX --seller <id> --generate"
        )
    username = pos[0]
    noninteractive = bool(opts.get("generate"))

    # Logins only exist in the hosted deployment. Creating one locally writes
    # password hashes into the Google Drive folder, where they would sync to
    # everyone -- almost certainly not what was intended.
    if not config.AUTH_REQUIRED:
        print("\n  !! Logins are switched off here, so this is NOT the server.")
        print(f"     This would write password hashes to {auth.USERS_FILE},")
        print("     inside the shared Drive folder.")
        print("\n     To create a login on the server, prefix the command with:")
        print(f"       railway ssh python3 app/manage.py adduser {username} ...")
        if noninteractive:
            sys.exit("\n  Refusing to write here. Run it against the server instead.")
        if input("\n  Continue anyway? [y/N]: ").strip().lower() not in ("y", "yes"):
            sys.exit("  Cancelled.")

    existing = auth.find_user(username)
    print(f"{'Resetting' if existing else 'Creating'} login for {username!r}.")

    if noninteractive:
        name = opts.get("name") or (existing or {}).get("name", "") or username
        initials = opts.get("initials") or (existing or {}).get("initials", "")
        seller = opts.get("seller") or (existing or {}).get("seller_id", "")
        pw = secrets.token_urlsafe(12)
    else:
        name = _ask("Full name", (existing or {}).get("name", ""))
        initials = _ask("Initials", (existing or {}).get("initials", ""))
        seller = _ask("Seller id", (existing or {}).get("seller_id", ""))
        pw = getpass.getpass("  Password (min 10 chars): ")
        if pw != getpass.getpass("  Again: "):
            sys.exit("  Passwords did not match.")

    try:
        row = auth.set_user(username, pw, name, initials, seller,
                            must_change=noninteractive)
    except ValueError as exc:
        sys.exit(f"  {exc}")

    print(f"  Saved {row['username']} ({row['initials']}) -> {row.get('name')}")
    if noninteractive:
        print()
        print("  ----------------------------------------------------")
        print(f"   username:  {row['username']}")
        print(f"   password:  {pw}")
        print("  ----------------------------------------------------")
        print("  This is a TEMPORARY password. They will be asked to")
        print("  choose their own the first time they sign in, so it")
        print("  does not matter that it is written down here.")


def cmd_listusers():
    users = auth.load_users()
    if not users:
        print("No logins yet. Create one with:  python3 app/manage.py adduser <name>")
        return
    for r in users:
        print(f"  {r['username']:16} {r.get('initials',''):5} {r.get('name','')}")


def cmd_deluser(argv):
    if not argv:
        sys.exit("usage: manage.py deluser <username>")
    print("Removed." if auth.delete_user(argv[0]) else "No such user.")


SKIP = {".DS_Store", "Thumbs.db", "desktop.ini", "Icon\r"}


def cmd_backup(argv):
    out = argv[0] if argv else "fleetquote-backup.zip"
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for label, base in (("quotes", store.QUOTES_DIR), ("upfit", store.UPFIT_DIR)):
            for dirpath, _d, files in os.walk(base):
                for fn in files:
                    if fn in SKIP or fn.endswith(".tmp"):
                        continue
                    full = os.path.join(dirpath, fn)
                    z.write(full, os.path.join(label, os.path.relpath(full, base)))
                    n += 1
    print(f"  {n} files -> {out} ({os.path.getsize(out) / 1024:.0f} KB)")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd, argv = sys.argv[1], sys.argv[2:]
    if cmd == "secret":
        cmd_secret()
    elif cmd == "adduser":
        cmd_adduser(argv)
    elif cmd == "listusers":
        cmd_listusers()
    elif cmd == "deluser":
        cmd_deluser(argv)
    elif cmd == "backup":
        cmd_backup(argv)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
