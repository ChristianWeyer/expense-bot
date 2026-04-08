"""Cloudflare Rechnungs-Download per API (kein Browser nötig)."""

import os
import time
from datetime import datetime
from pathlib import Path

import requests


CF_API_BASE = "https://api.cloudflare.com/client/v4"


def _get_cf_token() -> str | None:
    """Holt den Cloudflare API Token aus .env oder 1Password."""
    from src.config import _get_secret
    return _get_secret("CLOUDFLARE_API_TOKEN",
        os.environ.get("OP_CLOUDFLARE_TOKEN", "").strip() or None)


def _get_account_id(token: str) -> str | None:
    """Ermittelt die Cloudflare Account-ID."""
    resp = requests.get(
        f"{CF_API_BASE}/accounts",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    if resp.status_code == 200:
        accounts = resp.json().get("result", [])
        if accounts:
            return accounts[0]["id"]
    return None


def download_cloudflare_invoices(
    entries: list[dict],
    download_dir: Path,
) -> list[Path]:
    """Lädt Cloudflare-Rechnungen per API herunter."""
    download_dir.mkdir(parents=True, exist_ok=True)

    cf_entries = [
        e for e in entries
        if not e.get("is_credit") and "CLOUDFLARE" in e.get("vendor", "").upper()
    ]
    if not cf_entries:
        return []

    token = _get_cf_token()
    if not token:
        print("\n☁️  Cloudflare: CLOUDFLARE_API_TOKEN nicht konfiguriert")
        return []

    print(f"\n☁️  Cloudflare: Suche {len(cf_entries)} Rechnung(en) per API ...")

    account_id = _get_account_id(token)
    if not account_id:
        print("  ❌ Cloudflare Account-ID nicht gefunden")
        return []

    # Billing History abrufen
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(
        f"{CF_API_BASE}/user/billing/history",
        headers=headers,
        params={"per_page": 20, "order": "occurred_at", "direction": "desc"},
        timeout=15,
    )

    if resp.status_code != 200:
        print(f"  ❌ API-Fehler: HTTP {resp.status_code}")
        return []

    invoices = resp.json().get("result", [])
    print(f"  📋 {len(invoices)} Invoice(s) in der Billing History")

    downloaded = []

    for entry in cf_entries:
        amount = entry.get("amount", 0)
        date_str = entry.get("date", "")
        print(f"  🔍 Cloudflare  {amount:.2f} EUR  ({date_str})")

        # Invoice per Betrag matchen
        for inv in invoices:
            if inv.get("_used"):
                continue
            inv_amount = inv.get("amount", 0)
            if abs(inv_amount - amount) <= 0.5:
                invoice_id = inv.get("id")
                if not invoice_id:
                    continue

                # PDF herunterladen
                pdf_resp = requests.get(
                    f"{CF_API_BASE}/accounts/{account_id}/billing/receipts/{invoice_id}/pdf",
                    headers=headers,
                    timeout=30,
                )

                if pdf_resp.status_code == 200 and pdf_resp.content[:4] == b"%PDF":
                    date_prefix = date_str.replace(".", "") + "_" if date_str else ""
                    fname = f"{date_prefix}Cloudflare_{invoice_id}.pdf"
                    save_path = download_dir / fname
                    save_path.write_bytes(pdf_resp.content)
                    downloaded.append(save_path)
                    inv["_used"] = True
                    print(f"  ✅ {fname} ({len(pdf_resp.content) / 1024:.1f} KB)")
                    break
                else:
                    print(f"  ⚠️  PDF-Download fehlgeschlagen: HTTP {pdf_resp.status_code}")
        else:
            print(f"  ⚠️  Keine passende Invoice gefunden")

        time.sleep(0.3)

    if downloaded:
        print(f"  📦 {len(downloaded)} Cloudflare-Rechnung(en) heruntergeladen")
    return downloaded
