"""Download-History und Datei-Deduplizierung."""

import hashlib
import json
import time
from pathlib import Path

from src.config import HISTORY_FILE, DOWNLOAD_DIR


def load_history() -> set:
    """Lädt die Historie der bereits verarbeiteten Rechnungen."""
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE) as f:
            return set(json.load(f))
    return set()


def save_history(history: set):
    """Speichert die Historie."""
    with open(HISTORY_FILE, "w") as f:
        json.dump(list(history), f, indent=2)


def file_hash(filepath: Path) -> str:
    """Erstellt einen SHA256-Hash einer Datei zur Duplikat-Erkennung."""
    return hashlib.sha256(filepath.read_bytes()).hexdigest()


def _file_hash_md5(filepath: Path) -> str:
    """Erstellt einen MD5-Hash (nur für Rückwärtskompatibilität mit alter History)."""
    return hashlib.md5(filepath.read_bytes()).hexdigest()


def is_known_file(filepath: Path, history: set) -> bool:
    """Prüft ob eine Datei bereits in der History ist (SHA256 oder alter MD5)."""
    return file_hash(filepath) in history or _file_hash_md5(filepath) in history


def cleanup_old_invoices(keep_days: int):
    """Löscht PDFs aus belege/ die älter als keep_days Tage sind."""
    if not DOWNLOAD_DIR.exists():
        return
    cutoff = time.time() - (keep_days * 86400)
    removed = 0
    for pdf in DOWNLOAD_DIR.iterdir():
        if pdf.suffix.lower() != ".pdf":
            continue
        if pdf.stat().st_mtime < cutoff:
            pdf.unlink()
            removed += 1
    if removed:
        print(f"🧹 {removed} alte Rechnung(en) gelöscht (älter als {keep_days} Tage)")
