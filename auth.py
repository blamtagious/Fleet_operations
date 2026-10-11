"""Login for the hosted deployment.

Off by default: running locally the app only listens on 127.0.0.1, so there is
nobody to authenticate. Setting FLEETQUOTE_AUTH=1 turns it on.

Passwords are PBKDF2-HMAC-SHA256 with a per-user salt. Sessions are stateless
signed cookies -- user id plus expiry, HMAC'd with FLEETQUOTE_SECRET -- so a
restart or a second instance doesn't log everybody out, and there is no session
store to keep.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from . import config

USERS_FILE = os.path.join(config.CONFIG_DIR, "users.json")
ITERATIONS = 240_000
SESSION_DAYS = 14


# --------------------------------------------------------------- passwords

def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_b64, dk_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"),
            base64.b64decode(salt_b64), int(iters),
        )
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except Exception:
        return False


# ------------------------------------------------------------------- users

def load_users() -> list:
    try:
        with open(USERS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def save_users(users: list) -> None:
    os.makedirs(os.path.dirname(USERS_FILE), exist_ok=True)
    tmp = USERS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, USERS_FILE)


def find_user(username: str):
    u = (username or "").strip().lower()
    for row in load_users():
        if row.get("username", "").lower() == u:
            return row
    return None


def set_password(username: str, new_password: str) -> bool:
    """Change a password and clear any must-change flag."""
    if len(new_password or "") < 10:
        raise ValueError("Use at least 10 characters.")
    users = load_users()
    for row in users:
        if row.get("username", "").lower() == (username or "").lower():
            row["password_hash"] = hash_password(new_password)
            row["must_change"] = False
            row["password_set_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            save_users(users)
            return True
    return False


def set_user(username: str, password: str, name: str = "",
             initials: str = "", seller_id: str = "",
             must_change: bool = False) -> dict:
    """Create the user, or reset an existing one's password."""
    username = (username or "").strip().lower()
    if not username or not password:
        raise ValueError("Username and password are both required.")
    if len(password) < 10:
        raise ValueError("Use at least 10 characters.")

    users = load_users()
    for row in users:
        if row.get("username", "").lower() == username:
            row["password_hash"] = hash_password(password)
            row["must_change"] = must_change
            if name:
                row["name"] = name
            if initials:
                row["initials"] = initials.upper()
            if seller_id:
                row["seller_id"] = seller_id
            save_users(users)
            return row

    row = {
        "username": username,
        "name": name or username,
        "initials": (initials or username[:2]).upper(),
        "seller_id": seller_id,
        "password_hash": hash_password(password),
        "must_change": must_change,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    users.append(row)
    save_users(users)
    return row


def delete_user(username: str) -> bool:
    users = load_users()
    keep = [r for r in users if r.get("username", "").lower() != (username or "").lower()]
    if len(keep) == len(users):
        return False
    save_users(keep)
    return True


def authenticate(username: str, password: str):
    row = find_user(username)
    if not row:
        # Spend the same time either way so a wrong username isn't detectable.
        hash_password(password or "x")
        return None
    if verify_password(password or "", row.get("password_hash", "")):
        return row
    return None


# ---------------------------------------------------------------- sessions

def _secret() -> bytes:
    if not config.SECRET:
        raise RuntimeError(
            "FLEETQUOTE_SECRET is not set. Generate one with:\n"
            "    python3 -c \"import secrets; print(secrets.token_urlsafe(48))\""
        )
    return config.SECRET.encode("utf-8")


def make_session(username: str) -> str:
    expires = int(time.time()) + SESSION_DAYS * 86400
    payload = f"{username}|{expires}".encode("utf-8")
    body = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    sig = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()[:40]
    return f"{body}.{sig}"


def read_session(token: str):
    """Return the user row for a valid, unexpired token, else None."""
    if not token or "." not in token:
        return None
    body, _, sig = token.rpartition(".")
    expect = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()[:40]
    if not hmac.compare_digest(sig, expect):
        return None
    try:
        pad = "=" * (-len(body) % 4)
        username, _, exp = base64.urlsafe_b64decode(body + pad).decode().rpartition("|")
        if int(exp) < time.time():
            return None
    except Exception:
        return None
    return find_user(username)
