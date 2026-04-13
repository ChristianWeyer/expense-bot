"""Email-Versand ueber Microsoft Graph API — mit strukturiertem Beleg-Report."""

import base64
import sys
from datetime import datetime
from pathlib import Path

import requests

import src.config as _cfg
from src.config import RECIPIENT_EMAIL, GRAPH_SEND_URL
from src.auth import get_graph_token
from src.timer import Timer
from src.result import RunResult


def _build_body(result: RunResult) -> str:
    """Baut den Email-Body mit Zuordnungstabelle und Diagnostik."""
    now = datetime.now()
    lines = []
    lines.append("--- Automatisch generierte Email (Expense Bot) ---\n")
    lines.append("Hallo,\n")
    lines.append(f"anbei {len(result.all_files)} Beleg(e) als PDF im Anhang.\n")

    if result.mc_pdf_name:
        lines.append(f"📄 Quelle: Mastercard-Abrechnung \"{result.mc_pdf_name}\"")
    lines.append(f"📊 Ergebnis: {result.summary()}\n")

    # -- DB-Rechnungen --
    db = result.db_entries
    if db:
        matched_db = [e for e in db if e.status == "matched"]
        failed_db = [e for e in db if e.status != "matched" and not e.is_credit]
        lines.append(f"{'='*70}")
        if failed_db:
            lines.append(f"\n🚂 DB-Rechnungen: {len(matched_db)} von {len(db)} heruntergeladen")
        else:
            lines.append(f"\n🚂 DB-Rechnungen: alle {len(matched_db)} erfolgreich heruntergeladen")
        for e in db:
            if e.files:
                for f in e.files:
                    lines.append(f"  ✅ {f.name}")
            else:
                ref = e.entry.get('booking_ref', '?')
                lines.append(f"  ❌ {e.date}  {e.amount:.2f} EUR  Ref: {ref}")
                if ref and ref != '?':
                    lines.append(f"     → https://www.bahn.de/buchung/reise?auftragsnummer={ref}")

    # -- Sonstige Belege --
    non_db = result.non_db_entries
    if non_db:
        matched = [e for e in non_db if e.status == "matched"]
        unmatched = [e for e in non_db if e.status in ("unmatched", "pending")]
        lines.append(f"\n{'='*70}")
        lines.append(f"\n📎 Belege: {len(matched)} von {len(non_db)} gefunden\n")

        # Gruppiert nach Quelle für bessere Übersicht
        by_source: dict[str, list] = {}
        for e in non_db:
            if e.status == "matched" and e.files:
                src = e.source or "unbekannt"
                by_source.setdefault(src, []).append(e)

        for source, entries in sorted(by_source.items()):
            source_label = {
                "outlook": "📧 Outlook Email-Belege",
                "outlook:html": "📧 Outlook (nur Email-Body)",
                "bahn": "🚂 Deutsche Bahn",
                "amazon": "📦 Amazon",
                "spiegel": "📰 Spiegel",
                "portal:chatgpt": "🤖 ChatGPT",
                "portal:openai-api": "🤖 OpenAI API",
                "portal:cloudflare": "☁️ Cloudflare",
                "cloudflare-api": "☁️ Cloudflare",
                "heise": "📡 Heise",
                "adobe": "🎨 Adobe",
                "figma": "🎨 Figma",
                "google": "▶️ Google/YouTube",
                "audible": "🎧 Audible",
            }.get(source, f"📋 {source}")

            lines.append(f"  {source_label} ({len(entries)}):")
            for e in entries:
                for f in e.files:
                    lines.append(f"    ✅ {e.date}  {e.vendor[:25]:<25s}  {e.amount:>8.2f} EUR")
                    lines.append(f"       → {f.name}")
            lines.append("")

        # Fehlende Belege
        if unmatched:
            lines.append(f"  ⚠️  Kein Beleg gefunden ({len(unmatched)}):")
            for e in unmatched:
                lines.append(f"    ❌ {e.date}  {e.vendor[:25]:<25s}  {e.amount:>8.2f} EUR")
            lines.append("")

    # -- FX-Gebuehren Zusammenfassung --
    fx = result.fx_fee_entries
    if fx:
        fx_total = sum(e.amount for e in fx)
        lines.append(f"{'='*70}")
        lines.append(f"\n💱 FX-Gebühren: {len(fx)} Positionen, Summe: {fx_total:.2f} EUR")
        lines.append(f"   (Bankgebühren für Währungsumrechnung, kein Beleg nötig)")

    # -- Zusammenfassung --
    lines.append(f"\n{'='*70}")
    lines.append(f"\n📊 Zusammenfassung:")
    lines.append(f"   ✅ Gefunden:    {len(result.matched)} mit PDF")
    if result.link_only:
        lines.append(f"   🔗 Nur Link:    {len(result.link_only)}")
    if result.unmatched:
        lines.append(f"   ❌ Fehlend:     {len(result.unmatched)}")
    lines.append(f"   📎 PDFs:        {len(result.all_files)} Dateien")
    if fx:
        lines.append(f"   💱 FX-Gebühr.:  {len(fx)} (übersprungen)")

    lines.append(f"\nErstellt am {now.strftime('%d.%m.%Y um %H:%M Uhr')}.")
    lines.append("--- Ende der automatischen Nachricht ---")

    return "\n".join(lines)


def _build_subject(result: RunResult) -> str:
    """Baut den Email-Betreff."""
    now = datetime.now()
    total = result.total_debits
    matched = len(result.matched)
    files = len(result.all_files)
    has_issues = len(result.unmatched) > 0

    subject = f"[Automatisch] Belege ({matched}/{total} gefunden, {files} PDFs)"
    subject += f" – {now.strftime('%d.%m.%Y')}"
    if result.mc_pdf_name:
        subject += f" – {result.mc_pdf_name}"
    if has_issues:
        subject += " ⚠️ UNVOLLSTÄNDIG"

    return subject


def send_email(result: RunResult, timer: Timer, dry_run: bool = False, cc_email: str | None = None):
    """Versendet den Beleg-Report per Microsoft Graph API."""
    all_files = result.all_files
    files = result.deduplicated_files
    dupes_removed = len(all_files) - len(files)
    if not files and not result.unmatched:
        print("\nKeine Belege zum Versenden.")
        return

    cc_info = f" (CC: {cc_email})" if cc_email else ""
    if dupes_removed:
        print(f"\nVersende {len(files)} PDF(s) an {RECIPIENT_EMAIL}{cc_info} ({dupes_removed} Duplikat(e) entfernt) ...")
    else:
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
        print("     Belege sind trotzdem gespeichert in:", _cfg.DOWNLOAD_DIR)
        sys.exit(1)
