"""
Beleg-Suche in Outlook
======================
Durchsucht einen Outlook-Mailordner ("Belege") per Microsoft Graph API
nach Rechnungs-Emails, die zu Mastercard-Abrechnungsposten passen,
und lädt die PDF-Anhänge herunter.

Nutzung:
    from src.outlook import match_and_download_receipts
    results = match_and_download_receipts(token, entries, download_dir)
"""

import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
DATE_TOLERANCE = int(os.environ.get("BELEGE_DATE_TOLERANCE", "35"))
BELEGE_FOLDER = os.environ.get("BELEGE_FOLDER", "Belege")


# ─── Graph API Helpers ──────────────────────────────────────────────

def _graph_get(url: str, token: str, params: dict | None = None) -> dict:
    """GET-Request an die Graph API."""
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=30,
    )
    if resp.status_code == 401:
        print("  ❌ Graph API: Token abgelaufen. Bitte .token_cache.json löschen und neu anmelden.")
        return {}
    if resp.status_code == 403:
        print("  ❌ Graph API: Fehlende Berechtigung (Mail.Read). Bitte .token_cache.json löschen und neu anmelden.")
        return {}
    if resp.status_code != 200:
        print(f"  ⚠️  Graph API Fehler {resp.status_code}: {resp.text[:200]}")
        return {}
    return resp.json()


def find_mail_folder(token: str, folder_name: str = BELEGE_FOLDER) -> str | None:
    """Sucht einen Mailordner per Name. Gibt die Folder-ID zurück."""
    # Top-Level-Ordner durchsuchen
    data = _graph_get(
        f"{GRAPH_BASE}/me/mailFolders",
        token,
        {"$filter": f"displayName eq '{folder_name}'", "$select": "id,displayName"},
    )
    folders = data.get("value", [])
    if folders:
        return folders[0]["id"]

    # In allen Top-Level-Ordnern nach Unterordnern suchen
    all_folders = _graph_get(
        f"{GRAPH_BASE}/me/mailFolders",
        token,
        {"$select": "id,displayName", "$top": "50"},
    )
    for folder in all_folders.get("value", []):
        children = _graph_get(
            f"{GRAPH_BASE}/me/mailFolders/{folder['id']}/childFolders",
            token,
            {"$filter": f"displayName eq '{folder_name}'", "$select": "id,displayName"},
        )
        for child in children.get("value", []):
            return child["id"]

    return None


# ─── Vendor-Matching ────────────────────────────────────────────────

# Mapping von MC-Vendor-Namen zu Suchbegriffen für die Mailsuche
VENDOR_KEYWORDS = {
    "ANTHROPIC": ["anthropic", "claude"],
    "OPENAI *CHATGPT": ["chatgpt", "chatgpt subscription"],
    "OPENAI": ["openai"],
    "GITHUB": ["github"],
    "FIGMA": ["figma"],
    "MICROSOFT": ["microsoft", "msbill"],
    "MSFT": ["microsoft", "msbill"],
    "GOOGLE": ["google"],
    "ADOBE": ["adobe"],
    "AMAZON": ["amazon"],
    "AMZN": ["amazon"],
    "HETZNER": ["hetzner"],
    "CLOUDFLARE": ["cloudflare"],
    "RENDER.COM": ["render"],
    "AUTH0": ["auth0"],
    "TAILSCALE": ["tailscale"],
    "PADDLE.NET* JUMA": ["juma"],
    "PADDLE.NET* REMOVE": ["remove.bg", "kaleido"],
    "PADDLE.NET* LORDICON": ["lordicon"],
    "PADDLE.NET* SPD2": ["voila", "spd2"],
    "PADDLE.NET": ["paddle"],
    "NGROK": ["ngrok"],
    "NOUNPROJECT": ["noun project"],
    "AI FOR DEVS": ["ai for devs", "ai-for-devs"],
    "HUGGINGFACE": ["huggingface", "hugging face"],
    "LANGCHAIN": ["langchain", "langsmith"],
    "LANGFUSE": ["langfuse"],
    "SPIEGEL": ["spiegel"],
    "HANDELSBL": ["handelsblatt"],
    "HEISE": ["heise"],
    "ELEVENLABS": ["elevenlabs"],
    "WINDSURF": ["windsurf", "exafunction"],
    "PERPLEXITY": ["perplexity"],
    "CLAUDE.AI": ["anthropic"],
    "NEW YORK TIMES": ["nytimes"],
    "WSJ": ["wsj", "wall street journal"],
    "DJ*WSJ": ["wsj", "wall street journal"],
    "D J": ["wsj", "wall street journal"],
    "AUDIBLE": ["audible"],
    "PAYPAL *FRAENK": ["fraenk", "paypal"],
    "PAYPAL": ["paypal"],
    "FRAENK": ["fraenk"],
    "MOL*OBJECTIVE": ["objective development", "obdev"],
    "WACHETE": ["wachete"],
    "X CORP": ["receipt from x", "x premium", "twitter"],
    "LATENT.SPACE": ["latent space", "swyx"],
    "HOLIDAY INN": ["holiday inn", "ihg"],
    "CLAUDE.AI": ["anthropic", "claude"],
    "AUTHO": ["auth0"],
    "AUTH0": ["auth0"],
}


def _get_search_keywords(vendor: str) -> list[str]:
    """Extrahiert Suchbegriffe aus einem Vendor-Namen."""
    vendor_upper = vendor.upper()
    for prefix, keywords in VENDOR_KEYWORDS.items():
        if prefix in vendor_upper:
            return keywords

    # Fallback: erster sinnvoller Begriff aus dem Vendor-Namen
    # Strip typische Suffixe
    clean = re.sub(r"\s*(GmbH|AG|Ltd|Inc\.|LLC|INC|S\.R\.O\.|SAN FRANCISCO|BERLIN|DUBLIN|NEW YORK|LONDON|LISBOA|LUXEMBOURG|AMSTERDAM|BROOKLYN|SINGAPORE|BASTROP|MOUNTAIN VIEW|PRAGUE|GUNZENHAUSEN|KARLSRUHE).*", "", vendor, flags=re.IGNORECASE)
    clean = re.sub(r"[*#].*", "", clean).strip()
    if clean and len(clean) >= 3:
        return [clean.lower()]
    return [vendor.split(",")[0].split("*")[0].strip().lower()]


def _parse_date(date_str: str) -> datetime | None:
    """Parst ein Datum im Format DD.MM.YY."""
    try:
        return datetime.strptime(date_str, "%d.%m.%y")
    except (ValueError, TypeError):
        return None


# ─── Mail-Suche und Download ────────────────────────────────────────

# Begriffe die auf eine Rechnung/Beleg hindeuten
RECEIPT_TERMS = [
    "invoice", "receipt", "rechnung", "beleg", "quittung", "billing",
    "zahlung", "payment received", "payment for", "your payment",
    "bestellt", "bestellung", "order", "subscription", "abonnement",
]


def _score_candidate(msg: dict, vendor_keyword: str, amount: float) -> int:
    """Bewertet wie gut eine Email zu einem MC-Eintrag passt (höher = besser)."""
    subject = (msg.get("subject") or "").lower()
    sender = (msg.get("from", {}).get("emailAddress", {}).get("address") or "").lower()
    score = 0

    # Vendor im Betreff oder Absender?
    if vendor_keyword.lower() in subject:
        score += 3
    if vendor_keyword.lower() in sender:
        score += 3

    # Rechnungs-/Beleg-Begriffe im Betreff?
    for term in RECEIPT_TERMS:
        if term in subject:
            score += 2
            break

    # Betrag im Betreff? (z.B. "49,99" oder "49.99")
    amount_str_comma = f"{amount:.2f}".replace(".", ",")
    amount_str_dot = f"{amount:.2f}"
    if amount_str_comma in subject or amount_str_dot in subject:
        score += 2

    # Bonus: Absender sieht nach Billing/Service aus
    if any(p in sender for p in ["billing", "invoice", "receipt", "service@", "noreply@tax"]):
        score += 2

    # Malus: Bot-generierte Emails (nicht weitergeleitete!)
    if "[automatisch]" in subject or "expense bot" in subject:
        score -= 10

    # Malus: Newsletter-Absender oder typische Newsletter-Subjects
    if any(p in sender for p in ["newsletter@", "noreply@news.", "substack.com", "radar", "briefing",
                                   "noreply@medium.com", "breakingnews@", "access@interactive",
                                   "morning.briefing", "bytebytego@", "infoservice.", "ix-inhalt@"]):
        score -= 5
    if any(p in subject for p in ["watchlist", "fn-watchlist", "update:", "events", "briefing", "10-point",
                                   "laden ein", "breaking news", "i quit", "here's why", "complete guide",
                                   "expertentalk", "live:"]):
        score -= 5

    return score


def search_receipts_for_entry(token: str, folder_ids: list[str], entry: dict) -> list[dict]:
    """Sucht passende Emails zu einem MC-Eintrag in den angegebenen Ordnern.

    Durchsucht ALLE Keywords in ALLEN Ordnern und sammelt Kandidaten,
    statt beim ersten Treffer abzubrechen. Dedupliziert nach Message-ID.
    """
    keywords = _get_search_keywords(entry.get("vendor", ""))
    date = _parse_date(entry.get("date", ""))
    amount = entry.get("amount", 0)

    if not date:
        return []

    # Zeitfenster für Post-Filter
    date_from = date - timedelta(days=3)
    date_to = date + timedelta(days=DATE_TOLERANCE)

    candidates = []
    seen_ids = set()

    for keyword in keywords:
        for folder_id in folder_ids:
            data = _graph_get(
                f"{GRAPH_BASE}/me/mailFolders/{folder_id}/messages",
                token,
                {
                    "$search": f'"{keyword}"',
                    "$select": "id,subject,receivedDateTime,from,hasAttachments",
                    "$top": "50",
                },
            )
            for msg in data.get("value", []):
                msg_id = msg.get("id", "")
                if msg_id in seen_ids:
                    continue
                seen_ids.add(msg_id)

                recv = msg.get("receivedDateTime", "")
                try:
                    recv_date = datetime.fromisoformat(recv.replace("Z", "+00:00")).replace(tzinfo=None)
                    if date_from <= recv_date <= date_to:
                        # Score mit dem besten Keyword berechnen
                        score = _score_candidate(msg, keyword, amount)
                        existing_score = msg.get("_score", -999)
                        if score > existing_score:
                            msg["_score"] = score
                        msg["_has_attachments"] = msg.get("hasAttachments", False)
                        candidates.append(msg)
                except (ValueError, TypeError):
                    pass

        time.sleep(0.3)

    candidates.sort(key=lambda m: m.get("_score", 0), reverse=True)
    # Mindest-Score 2, ABER: Vendor muss im Subject oder Sender vorkommen (Score >= 3)
    # ODER der Absender muss ein Billing-Absender sein (invoice@, receipts@, billing@)
    filtered = []
    for c in candidates:
        score = c.get("_score", 0)
        if score < 2:
            continue
        # Score 2 allein (nur Receipt-Term, kein Vendor-Match) -> zu schwach
        # Ausnahme: Billing-Absender haben mindestens Score 4 (Receipt-Term + Billing-Sender)
        if score == 2:
            sender = (c.get("from", {}).get("emailAddress", {}).get("address") or "").lower()
            if not any(p in sender for p in ["billing", "invoice", "receipt", "service@"]):
                continue
        filtered.append(c)
    return filtered


def _extract_receipt_url(token: str, message_id: str) -> str | None:
    """Extrahiert einen Receipt/Invoice-Link aus dem Email-Body."""
    data = _graph_get(
        f"{GRAPH_BASE}/me/messages/{message_id}",
        token,
        {"$select": "body"},
    )
    body = (data.get("body", {}).get("content") or "")

    receipt_keywords = ["receipt", "invoice", "billing", "rechnung", "download", "beleg", "order", "bestellung"]
    skip_keywords = ["unsubscribe", "mailto:", "privacy", "terms", "help", "cancel", "settings"]

    # Suche 1: Links deren URL Receipt/Invoice-Begriffe enthält
    url_pattern = re.compile(r'href=["\']?(https?://[^"\'>\s]+)', re.IGNORECASE)
    for match in url_pattern.finditer(body):
        url = match.group(1)
        url_lower = url.lower()
        if any(kw in url_lower for kw in receipt_keywords):
            if not any(skip in url_lower for skip in skip_keywords):
                return url

    # Suche 2: Links deren Anchor-Text oder umgebender Text (±100 Zeichen)
    # Receipt/Invoice-Begriffe enthält.
    # z.B. "find your receipt <a href="...">here</a>"
    anchor_pattern = re.compile(r'<a\s[^>]*href=["\']?(https?://[^"\'>\s]+)["\']?[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
    for match in anchor_pattern.finditer(body):
        url = match.group(1)
        anchor_text = re.sub(r'<[^>]+>', '', match.group(2)).lower().strip()
        # Kontext: 150 Zeichen vor und nach dem Link
        start = max(0, match.start() - 150)
        end = min(len(body), match.end() + 150)
        context = re.sub(r'<[^>]+>', ' ', body[start:end]).lower()

        if any(kw in anchor_text or kw in context for kw in receipt_keywords):
            if not any(skip in url.lower() for skip in skip_keywords):
                return url

    return None


def _save_email_body_as_pdf(token: str, message_id: str, download_dir: Path, prefix: str) -> Path | None:
    """Speichert den HTML-Body einer Email als PDF (fuer Receipts ohne Anhang).

    Speichert als HTML-Datei. Fuer PDF-Konvertierung kann spaeter
    Playwright oder ein externer Konverter genutzt werden.
    """
    data = _graph_get(
        f"{GRAPH_BASE}/me/messages/{message_id}",
        token,
        {"$select": "body,subject"},
    )
    body = data.get("body", {})
    content = body.get("content", "")
    content_type = body.get("contentType", "text")

    if not content or len(content) < 100:
        return None

    try:
        if content_type == "html":
            fname = f"{prefix}email_receipt.html"
        else:
            # Plain text -> wrap in minimal HTML
            content = f"<html><body><pre style='font-family:sans-serif'>{content}</pre></body></html>"
            fname = f"{prefix}email_receipt.html"

        save_path = download_dir / fname
        save_path.write_text(content, encoding="utf-8")
        if save_path.stat().st_size > 100:
            return save_path
    except Exception:
        pass

    return None


def _is_invoice_pdf(pdf_bytes: bytes) -> bool:
    """Prüft ob ein PDF eine Rechnung/Beleg ist (nicht Kündigungsbestätigung, AGB, etc.)."""
    try:
        import fitz
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        text = ""
        for page_idx in range(min(2, len(doc))):
            text += doc[page_idx].get_text().lower()
        doc.close()

        # Positive Signale: enthält Rechnungs-typische Begriffe
        invoice_signals = [
            "rechnung", "invoice", "receipt", "beleg", "quittung",
            "betrag", "amount", "total", "subtotal", "netto", "brutto",
            "mwst", "ust", "vat", "tax", "eur", "usd",
        ]
        has_invoice_signal = any(s in text for s in invoice_signals)

        # Negative Signale: Kündigungsbestätigungen, Willkommens-Briefe, etc.
        rejection_signals = [
            "kuendigungsbestaetigung", "kündigungsbestätigung",
            "kuendigung zum", "kündigung zum",
            "ihr abonnement endet", "your subscription ends",
            "willkommen", "welcome aboard",
        ]
        has_rejection = any(s in text for s in rejection_signals)

        if has_rejection and not has_invoice_signal:
            return False
        return has_invoice_signal
    except Exception:
        return True  # Im Zweifel akzeptieren


def download_attachments(token: str, message_id: str, download_dir: Path, prefix: str = "") -> list[Path]:
    """Lädt PDF-Anhänge einer Email herunter (bevorzugt Invoice/Receipt-Dateien).

    Validiert PDF-Inhalt: Kündigungsbestätigungen etc. werden abgelehnt.
    Dedupliziert identische PDFs per Hash.
    """
    data = _graph_get(
        f"{GRAPH_BASE}/me/messages/{message_id}/attachments",
        token,
    )

    import base64
    import hashlib
    invoices = []
    receipts = []
    seen_hashes = set()

    for att in data.get("value", []):
        name = att.get("name", "")
        content_type = att.get("contentType", "")
        size = att.get("size", 0)

        if not (name.lower().endswith(".pdf") or "pdf" in content_type.lower()):
            continue
        if size > 10 * 1024 * 1024:
            continue

        name_lower = name.lower()
        skip_names = ["agb", "widerrufsrecht", "datenschutz", "terms", "privacy", "conditions"]
        if any(skip in name_lower for skip in skip_names):
            continue

        content_bytes = att.get("contentBytes")
        if not content_bytes:
            continue

        pdf_bytes = base64.b64decode(content_bytes)

        # Deduplizierung: identische PDFs nur einmal speichern
        pdf_hash = hashlib.sha256(pdf_bytes).hexdigest()
        if pdf_hash in seen_hashes:
            continue
        seen_hashes.add(pdf_hash)

        # Inhaltsprüfung: ist das PDF eine Rechnung?
        if not _is_invoice_pdf(pdf_bytes):
            print(f"         -> {name}: keine Rechnung (uebersprungen)")
            continue

        safe_name = re.sub(r"[^\w.\-]", "_", name)
        save_path = download_dir / f"{prefix}{safe_name}"
        save_path.write_bytes(pdf_bytes)

        if "invoice" in name_lower:
            invoices.append(save_path)
        elif "receipt" in name_lower:
            receipts.append(save_path)
        else:
            invoices.append(save_path)

    # Invoice hat Vorrang — Receipts nur wenn keine Invoice vorhanden
    if invoices:
        for r in receipts:
            r.unlink(missing_ok=True)
        return invoices
    return receipts


# ─── Orchestrator ───────────────────────────────────────────────────

def match_and_download_receipts(
    token: str,
    entries: list[dict],
    download_dir: Path,
) -> dict:
    """
    Sucht für jeden MC-Eintrag passende Belege im Outlook-Ordner
    und lädt die PDF-Anhänge herunter.

    Returns:
        {
            "matched": [{"entry": ..., "email_subject": ..., "files": [...]}],
            "unmatched": [entry, ...],
            "downloaded_files": [Path, ...],
        }
    """
    download_dir.mkdir(parents=True, exist_ok=True)

    # 1. Mail-Ordner finden (Belege + Archive als Fallback)
    search_folders = []
    search_folder_names = os.environ.get("BELEGE_SEARCH_FOLDERS", f"{BELEGE_FOLDER},Archiv,Archive")
    for folder_name in search_folder_names.split(","):
        print(f"  📂 Suche Outlook-Ordner '{folder_name}' ...")
        fid = find_mail_folder(token, folder_name)
        if fid:
            search_folders.append(fid)
            print(f"     ✅ gefunden")
        else:
            print(f"     ⚠️  nicht gefunden")
    if not search_folders:
        print(f"  ❌ Keine durchsuchbaren Ordner gefunden!")
        return {"matched": [], "unmatched": entries, "downloaded_files": []}

    matched = []
    unmatched = []
    all_files = []
    used_message_ids = set()  # Dedup nur für Emails OHNE Anhang (Link-only)

    # Nur Belastungen (keine Gutschriften)
    debits = [e for e in entries if not e.get("is_credit")]
    print(f"  🔍 Suche Belege für {len(debits)} Einträge ...\n")

    for idx, entry in enumerate(debits, 1):
        vendor = entry.get("vendor", "?")
        amount = entry.get("amount", 0)
        date = entry.get("date", "")
        print(f"  [{idx}/{len(debits)}] {vendor:<30s}  {amount:>8.2f} EUR  ({date})")

        candidates = search_receipts_for_entry(token, search_folders, entry)
        # Emails ohne Anhang nur einmal nutzen (Link-Dedup)
        candidates = [c for c in candidates
                      if c.get("_has_attachments") or c.get("id") not in used_message_ids]
        if not candidates:
            print(f"         ⚠️  Kein passender Beleg gefunden")
            unmatched.append(entry)
            continue

        # Besten Kandidaten wählen:
        # 1. Nur solche mit Anhang (wenn vorhanden)
        # 2. Sortiert nach Datum-Nähe zum MC-Eintrag (nächstes Datum zuerst)
        with_att = [c for c in candidates if c.get("_has_attachments")]
        pool = with_att if with_att else candidates

        entry_date = _parse_date(date)
        if entry_date and len(pool) > 1:
            def _date_distance(msg):
                recv = msg.get("receivedDateTime", "")
                try:
                    recv_date = datetime.fromisoformat(recv.replace("Z", "+00:00")).replace(tzinfo=None)
                    return abs((recv_date - entry_date).total_seconds())
                except (ValueError, TypeError):
                    return 999999999
            pool.sort(key=_date_distance)

        msg = pool[0]
        subject = msg.get("subject", "")[:60]
        print(f"         ✅ {subject}")

        # PDF-Anhänge herunterladen
        date_prefix = date.replace(".", "") + "_" if date else ""
        vendor_short = re.sub(r"[^\w]", "", vendor)[:20]
        prefix = f"{date_prefix}{vendor_short}_"

        if not msg.get("_has_attachments"):
            used_message_ids.add(msg["id"])

        if msg.get("_has_attachments"):
            files = download_attachments(token, msg["id"], download_dir, prefix)
            if files:
                for f in files:
                    print(f"         -> {f.name}")
                all_files.extend(files)
                matched.append({
                    "entry": entry,
                    "email_subject": msg.get("subject", ""),
                    "email_id": msg["id"],
                    "files": files,
                })
            else:
                print(f"         Email gefunden aber kein PDF-Anhang")
                unmatched.append(entry)
        else:
            # Kein PDF-Anhang — versuche Email-Body als PDF zu speichern
            # (funktioniert gut fuer Google Play Belege, Stripe Receipts, etc.)
            score = msg.get("_score", 0)
            if score >= 4:
                body_pdf = _save_email_body_as_pdf(token, msg["id"], download_dir, prefix)
                if body_pdf:
                    print(f"         -> {body_pdf.name} (aus Email-Body)")
                    all_files.append(body_pdf)
                    matched.append({
                        "entry": entry,
                        "email_subject": msg.get("subject", ""),
                        "email_id": msg["id"],
                        "files": [body_pdf],
                    })
                else:
                    print(f"         Email ohne PDF -> weiter an Portal-Scraper")
                    unmatched.append(entry)
            else:
                print(f"         Email ohne PDF -> weiter an Portal-Scraper")
                unmatched.append(entry)

        time.sleep(0.3)  # Rate-Limiting

    print(f"\n{'=' * 60}")
    print(f"  Belege: {len(matched)} gefunden, {len(unmatched)} offen")
    print(f"  PDFs heruntergeladen: {len(all_files)}")
    print(f"{'=' * 60}")

    return {
        "matched": matched,
        "unmatched": unmatched,
        "downloaded_files": all_files,
    }
