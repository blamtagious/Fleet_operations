"""Where things live, and how the app is configured.

Locally everything sits in the Google Drive folder. On a host the data lives on
a mounted volume instead, so every path is overridable by environment variable:

    FLEETQUOTE_DATA    parent of quotes/ and upfit/   (default: the repo root)
    FLEETQUOTE_REF     folder holding the two .xlsx   (default: the repo root)
    FLEETQUOTE_AUTH    "1" to require a login         (default: off)
    FLEETQUOTE_SECRET  session signing key            (required when AUTH is on)
    FLEETQUOTE_SHEET_ID / FLEETQUOTE_GOOGLE_KEY   log each bill to a Google Sheet (see billinglog.py)
    PORT               port to listen on              (hosts set this for you)
"""

from __future__ import annotations

import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(APP_DIR)


def _dir(env: str, default: str) -> str:
    return os.path.abspath(os.environ.get(env, "").strip() or default)


DATA_DIR = _dir("FLEETQUOTE_DATA", ROOT)
REF_DIR = _dir("FLEETQUOTE_REF", ROOT)

QUOTES_DIR = os.path.join(DATA_DIR, "quotes")
UPFIT_DIR = os.path.join(DATA_DIR, "upfit")
CONFIG_DIR = os.path.join(DATA_DIR, "config")

def _ref(*names: str) -> str:
    """First reference file that exists; uploads often swap spaces for underscores."""
    for n in names:
        p = os.path.join(REF_DIR, n)
        if os.path.isfile(p):
            return p
    return os.path.join(REF_DIR, names[0])


PRICING_XLSX = _ref("TT_Murfreesboro Ford Pricing.xlsx", "TT_Murfreesboro_Ford_Pricing.xlsx")
FIN_XLSX = _ref("FIN Codes.xlsx", "FIN_Codes.xlsx")

# Seller list and dealer details ship with the app, but a deployment can
# override them by dropping the same files into the data volume's config/.
BUNDLED_CONFIG = os.path.join(APP_DIR, "config")

AUTH_REQUIRED = os.environ.get("FLEETQUOTE_AUTH", "").strip() in ("1", "true", "yes")
SECRET = os.environ.get("FLEETQUOTE_SECRET", "").strip()
HOSTED = AUTH_REQUIRED or bool(os.environ.get("PORT"))


def config_path(name: str) -> str:
    """Prefer an operator-supplied config file, else the one bundled with the app."""
    override = os.path.join(CONFIG_DIR, name)
    return override if os.path.isfile(override) else os.path.join(BUNDLED_CONFIG, name)


def ensure_dirs() -> None:
    for d in (QUOTES_DIR, UPFIT_DIR):
        os.makedirs(d, exist_ok=True)
