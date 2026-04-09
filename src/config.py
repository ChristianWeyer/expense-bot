"""Zentrale Konfiguration — lädt Secrets aus .env und 1Password."""

import os
import subprocess as _sp
from pathlib import Path

from dotenv import load_dotenv

# .env laden
load_dotenv(Path(__file__).parent.parent / ".env")


# ─── 1Password CLI ──────────────────────────────────────────

def _op_read(ref: str) -> str | None:
    """Liest ein Secret aus 1Password CLI. Gibt None zurück wenn nicht verfügbar."""
    if not ref:
        return None
    try:
        result = _sp.run(["op", "read", ref], capture_output=True, text=True, timeout=10)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, _sp.TimeoutExpired):
        pass
    return None


def _get_secret(env_var: str, op_ref: str | None = None) -> str | None:
    """Liest ein Secret: zuerst aus .env, dann aus 1Password als Fallback."""
    val = os.environ.get(env_var, "").strip()
    if val:
        return val
    if op_ref:
        return _op_read(op_ref)
    return None


# ─── 1Password-Referenzen ───────────────────────────────────

OP_BAHN = os.environ.get("OP_BAHN_PASSWORD", "").strip() or None
OP_AMAZON = os.environ.get("OP_AMAZON_PASSWORD", "").strip() or None

# ─── Credentials ────────────────────────────────────────────

BAHN_EMAIL = _get_secret("BAHN_EMAIL",
    os.environ.get("OP_BAHN_EMAIL", "").strip() or "op://Private/Bahn/username")
BAHN_PASSWORD = _get_secret("BAHN_PASSWORD",
    OP_BAHN or "op://Private/Bahn/password")
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL")

AZURE_CLIENT_ID = os.environ.get("AZURE_CLIENT_ID")
AZURE_TENANT_ID = os.environ.get("AZURE_TENANT_ID", "common")
CC_EMAIL = os.environ.get("CC_EMAIL", "").strip() or None
CDP_URL = os.environ.get("CDP_URL", "").strip() or None
MC_PDF = os.environ.get("MC_PDF", "").strip() or None

AMAZON_EMAIL = _get_secret("AMAZON_EMAIL",
    os.environ.get("OP_AMAZON_EMAIL", "").strip() or "op://Private/Amazon - Thinktecture/email")
AMAZON_PASSWORD = _get_secret("AMAZON_PASSWORD",
    OP_AMAZON or "op://Private/Amazon - Thinktecture/password")

try:
    KEEP_DAYS = int(os.environ.get("KEEP_DAYS", "30"))
except ValueError:
    KEEP_DAYS = 30

# Vendor-spezifische Config (ausgelagert statt hardcoded)
FIGMA_TEAM_ID = os.environ.get("FIGMA_TEAM_ID", "").strip() or None
OWN_EMAIL_DOMAIN = os.environ.get("OWN_EMAIL_DOMAIN", "thinktecture.com").strip()

# ─── Pfade ──────────────────────────────────────────────────

ROOT_DIR = Path(__file__).parent.parent
DOWNLOAD_DIR = ROOT_DIR / "belege"
BELEGE_DIR = DOWNLOAD_DIR  # Legacy-Alias
HISTORY_FILE = ROOT_DIR / ".download_history.json"
TOKEN_CACHE_FILE = ROOT_DIR / ".token_cache.json"
BROWSER_DATA_DIR = ROOT_DIR / ".browser-data"

# ─── URLs & Konstanten ──────────────────────────────────────

HOME_URL = "https://www.bahn.de"
TRIPS_URL = "https://www.bahn.de/buchung/reisen"
GRAPH_SEND_URL = "https://graph.microsoft.com/v1.0/me/sendMail"
SCOPES = ["Mail.Send", "Mail.Read"]

DOWNLOAD_BTN_SELECTOR = (
    'a:has-text("Rechnung als PDF herunterladen"):visible, '
    'button:has-text("Rechnung als PDF herunterladen"):visible'
)
