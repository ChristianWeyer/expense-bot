"""Email-Versand ueber Microsoft Graph API — mit strukturiertem Beleg-Report."""

import base64
import sys
from datetime import datetime
from pathlib import Path

import requests

from src.config import RECIPIENT_EMAIL, GRAPH_SEND_URL, DOWNLOAD_DIR
from src.auth import get_graph_token
from src.timer import Timer
from src.result import RunResult


def _build_body(result: RunResult) -> str:
    """Baut den Email-Body mit Zuordnungstabelle und Diagnostik."""
    now = datetime.now()
    lines = []
    lines.append("--- Automatisch generierte Email (Expense Bot) ---\n")
    lines.append(f"Hallo,\n")

    if result.mc_pdf_name:
        lines.append(f"Quelle: Mastercard-Abrechnung \"{result.mc_pdf_name}\"")

    lines.append(f"Ergebnis: {result.summary()}\n")
    lines.append(f"{'='*70}")

    # -- DB-Rechnungen --
    db = result.db_entries
    if db:
        matched_db = [e for e in db if e.status == "matched"]
        lines.append(f"\nDB-RECHNUNGEN ({len(matched_db)}/{len(db)}):")
        for e in db:
            if e.files:
                fnames = ", ".join(f.name for f in e.files)
                lines.append(f"  [OK] {e.date:>8s}  {e.amount:>8.2f} EUR  Ref: {e.entry.get('booking_ref', '?')}")
                lines.append(f"     -> {fnames}")
            else:
                lines.append(f"  [!!] {e.date:>8s}  {e.amount:>8.2f} EUR  Ref: {e.entry.get('booking_ref', '?')}")
                if e.note:
                    lines.append(f"     -> {e.note}")
                if e.entry.get("booking_ref"):
                    lines.append(f"     -> https://www.bahn.de/buchung/reise?auftragsnummer={e.entry['booking_ref']}")

    # -- Sonstige Belege --
    non_db = result.non_db_entries
    if non_db:
        matched = [e for e in non_db if e.status == "matched"]
        lines.append(f"\n{'='*70}")
        lines.append(f"\nBELEGE ({len(matched)}/{len(non_db)}):")
        lines.append(f"{'~'*70}")

        for e in non_db:
            vendor = e.vendor[:30]
            status_map = {"matched": "[OK]", "link_only": "[LN]", "unmatched": "[!!]", "pending": "[??]"}
            status_icon = status_map.get(e.status, "[??]")

            lines.append(f"  {status_icon} {e.date:>8s}  {vendor:<30s}  {e.amount:>8.2f} EUR")

            if e.files:
                for f in e.files:
                    lines.append(f"     -> {f.name}")
                if e.source:
                    lines.append(f"     Quelle: {e.source}")
            elif e.receipt_url:
                lines.append(f"     -> Link: {e.receipt_url[:80]}")
            elif e.status in ("unmatched", "pending"):
                lines.append(f"     -> KEIN BELEG GEFUNDEN")
                if e.note:
                    lines.append(f"     Grund: {e.note}")

    # -- FX-Gebuehren Zusammenfassung --
    fx = result.fx_fee_entries
    if fx:
        fx_total = sum(e.amount for e in fx)
        lines.append(f"\n{'='*70}")
        lines.append(f"\nFX-GEBUEHREN: {len(fx)} Positionen, Summe: {fx_total:.2f} EUR")
        lines.append(f"  (Bankgebuehren fuer Waehrungsumrechnung, kein Beleg noetig)")

    # -- Zusammenfassung --
    lines.append(f"\n{'='*70}")
    lines.append(f"\nZusammenfassung:")
    lines.append(f"  Gesamt:      {result.total_debits} Eintraege (ohne FX-Gebuehren)")
    lines.append(f"  Gefunden:    {len(result.matched)} mit PDF")
    lines.append(f"  Nur Link:    {len(result.link_only)}")
    lines.append(f"  Fehlend:     {len(result.unmatched)}")
    lines.append(f"  PDFs:        {len(result.all_files)} Dateien")
    if fx:
        lines.append(f"  FX-Gebuehr.: {len(fx)} (uebersprungen)")

    lines.append(f"\nErstellt am {now.strftime('%d.%m.%Y um %H:%M Uhr')}.")
    lines.append(f"--- Ende der automatischen Nachricht ---")

    return "\n".join(lines)


def _build_subject(result: RunResult) -> str:
    """Baut den Email-Betreff."""
    now = datetime.now()
    total = result.total_debits
    matched = len(result.matched)
    files = len(result.all_files)
    has_issues = len(result.unmatched) > 0

    subject = f"[Automatisch] Belege ({matched}/{total} gefunden, {files} PDFs)"
    subject += f" - {now.strftime('%d.%m.%Y')}"
    if result.mc_pdf_name:
        subject += f" - {result.mc_pdf_name}"
    if has_issues:
        subject += " WARNUNG"

    return subject


def send_email(result: RunResult, timer: Timer, dry_run: bool = False, cc_email: str | None = None):
    """Versendet den Beleg-Report per Microsoft Graph API."""
    files = result.all_files
    if not files and not result.unmatched:
        print("\nKeine Belege zum Versenden.")
        return

    cc_info = f" (CC: {cc_email})" if cc_email else ""
    print(f"\nVersende {len(files)} PDF(s) an {RECIPIENT_EMAIL}{cc_info} ...")
    print(f"   {result.summary()}")

    if dry_run:
        print("  Dry-Run: Email wird NICHT gesendet")
        body = _build_body(result)
        print("\n" + body)
        return

    token = get_graph_token()

    body_text = _build_body(result)
    subject = _build_subject(result)

    attachments = []
    for filepath in files:
        content_bytes = filepath.read_bytes()
        attachments.append({
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": filepath.name,
            "contentType": "application/pdf",
            "contentBytes": base64.b64encode(content_bytes).decode("utf-8"),
        })

    to_recipients = [{"emailAddress": {"address": RECIPIENT_EMAIL}}]
    cc_recipients = [{"emailAddress": {"address": cc_email}}] if cc_email else []

    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body_text},
            "toRecipients": to_recipients,
            "ccRecipients": cc_recipients,
            "attachments": attachments,
        },
        "saveToSentItems": True,
    }

    response = requests.post(
        GRAPH_SEND_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )

    if response.status_code == 202:
        print("  Email erfolgreich gesendet!")
        timer.lap("Email-Versand")
    else:
        print(f"  Email-Versand fehlgeschlagen (HTTP {response.status_code})")
        print(f"     {response.text[:200]}")
        print("     Belege sind trotzdem gespeichert in:", DOWNLOAD_DIR)
        sys.exit(1)
