"""Constants for the Digi Romania integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "digi_ro"

# Config entry keys
CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_COOKIES = "cookies"
CONF_HISTORY_LIMIT = "history_limit"
CONF_ADDRESS_ID = "address_id"
CONF_ADDRESS_LABEL = "address_label"

DEFAULT_HISTORY_LIMIT = 6
MIN_HISTORY_LIMIT = 1
MAX_HISTORY_LIMIT = 24

# Digi My Account (server-rendered HTML; no public JSON API).
BASE_URL = "https://www.digi.ro"
LOGIN_URL = f"{BASE_URL}/auth/login?redirectTo=%2F"
TWO_FA_URL = f"{BASE_URL}/auth/2fa?redirectTo=%2F"
TWO_FA_SEND_URL = f"{BASE_URL}/api-post-2fa-send-code"
TWO_FA_VALIDATE_URL = f"{BASE_URL}/api-post-2fa-validate-code"
ADDRESS_SELECT_URL = f"{BASE_URL}/auth/address-select?redirectTo=%2F"
ADDRESS_CONFIRM_URL = f"{BASE_URL}/store/address-confirm-existing"
INVOICES_URL = f"{BASE_URL}/my-account/invoices"
LOGIN_PATH = "/auth/login"

# Invoices change at most monthly; a daily poll is plenty. The poll also keeps
# the stored cookie jar fresh so the long-lived session survives between polls.
DEFAULT_SCAN_INTERVAL = timedelta(hours=12)

# A real-browser User-Agent — Digi's edge is friendlier to browser-like requests.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/146.0.0.0 Safari/537.36"
)

CURRENCY_RON = "RON"
