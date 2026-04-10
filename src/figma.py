"""Figma Invoice Download — ueber interne API (kein Browser-Scraping noetig).

Die Figma API /api/plans/team/{id}/invoices liefert Stripe PDF-URLs direkt.
Braucht nur Figma-Session-Cookies aus dem CDP-Browser.
"""

import time
from datetime import datetime
from pathlib import Path

import requests as http_req

from src.config import FIGMA_TEAM_ID


def download_figma_invoices(page, entries: list[dict], download_dir: Path) -> list[tuple[dict, Path]]:
    """Lädt Figma-Invoices ueber die interne API.

    Returns:
        Liste von (entry, filepath) Tupeln.
    """
    download_dir.mkdir(parents=True, exist_ok=True)

    figma_entries = [e for e in entries if not e.get("is_credit") and "FIGMA" in e.get("vendor", "").upper()]
    if not figma_entries or not FIGMA_TEAM_ID:
        return []

    print(f"\n  Figma: Suche {len(figma_entries)} Rechnung(en) ...")

    cookies = page.context.cookies("https://www.figma.com")
    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

    try:
        resp = http_req.get(
            f"https://www.figma.com/api/plans/team/{FIGMA_TEAM_ID}/invoices",
            headers={"Cookie": cookie_str},
            timeout=60,
        )
        if resp.status_code != 200:
            print(f"  API Fehler: HTTP {resp.status_code}")
            return []

        invoices = resp.json().get("meta", {}).get("invoices", [])
        paid = [inv for inv in invoices if inv.get("state") == "paid" and inv.get("invoice_pdf_url")]
        print(f"  {len(paid)} bezahlte Invoice(s) mit PDF")
    except Exception as e:
        print(f"  API Fehler: {e}")
        return []

    if not paid:
        return []

    results = []
    used_invoices = set()

    for entry in figma_entries:
        amount = entry.get("amount", 0)
        date_str = entry.get("date", "")
        print(f"  Figma  {amount:.2f} EUR  ({date_str})")

        entry_date = None
        try:
            entry_date = datetime.strptime(date_str, "%d.%m.%y")
        except (ValueError, TypeError):
            pass

        # Passende Invoice finden (nach Datum, nur ungenutzte)
        best_inv = None
        best_distance = float('inf')

        for inv in paid:
            inv_id = inv.get("id", "")
            if inv_id in used_invoices:
                continue
            issued = inv.get("issued_at", "")[:10]
            try:
                inv_date = datetime.strptime(issued, "%Y-%m-%d")
                if entry_date:
                    distance = abs((inv_date - entry_date).days)
                    if distance < best_distance:
                        best_distance = distance
                        best_inv = inv
            except (ValueError, TypeError):
                pass

        if not best_inv:
            # Fallback: nächste ungenutzte
            for inv in paid:
                if inv.get("id", "") not in used_invoices:
                    best_inv = inv
                    break

        if not best_inv:
            print(f"  Keine PDF-URL")
            continue

        pdf_url = best_inv.get("invoice_pdf_url", "")
        if not pdf_url:
            print(f"  Keine PDF-URL")
            continue

        try:
            pdf_resp = http_req.get(pdf_url, timeout=30)
            if pdf_resp.status_code == 200 and pdf_resp.content[:4] == b"%PDF":
                date_prefix = date_str.replace(".", "") + "_" if date_str else ""
                fname = f"{date_prefix}Figma_Invoice.pdf"
                save_path = download_dir / fname
                save_path.write_bytes(pdf_resp.content)
                results.append((entry, save_path))
                used_invoices.add(best_inv.get("id", ""))
                print(f"  -> {fname} ({len(pdf_resp.content) / 1024:.1f} KB)")
            else:
                print(f"  PDF-Download: HTTP {pdf_resp.status_code}")
        except Exception as e:
            print(f"  Download fehlgeschlagen: {e}")

    if results:
        print(f"  {len(results)} Figma-Rechnung(en) heruntergeladen")
    return results
