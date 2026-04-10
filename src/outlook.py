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

from src.config import OWN_EMAIL_DOMAIN

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
DATE_TOLERANCE = int(os.environ.get("BELEGE_DATE_TOLERANCE", "35"))
BELEGE_FOLDER = os.environ.get("BELEGE_FOLDER", "Belege")


# ─── Graph API Helpers ──────────────────────────────────────────────

def _graph_get(url: str, token: str, params: dict | None = None, _retried: bool = False) -> dict:
    """GET-Request an die Graph API. Bei 401 wird einmalig ein Token-Refresh versucht."""
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=30,
    )
    if resp.status_code == 401 and not _retried:
        print("  ⚠️  Graph API: 401 — versuche Token-Refresh ...")
        from src.auth import get_graph_token
        new_token = get_graph_token()
        return _graph_get(url, new_token, params, _retried=True)
    if resp.status_code == 401:
        print("  ❌ Graph API: Token abgelaufen (auch nach Refresh). Bitte .token_cache.json löschen und neu anmelden.")
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
    "WL*GOOGLE": ["google youtube", "google play"],
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
    "HOLIDAY INN": ["holiday inn", "ihg", "hiex", "invoice for your stay"],
    "CLAUDE.AI": ["anthropic", "claude"],
    "AUTHO": ["auth0"],
    "AUTH0": ["auth0"],
    # ─── Bisher fehlende Vendors ────────────────────────
    "SLACK": ["slack", "slack billing"],
    "SUEDDEUTSCHE": ["sueddeutsche", "sz-abo", "süddeutsche"],
    "APPLE.COM/BILL": ["apple", "apple.com"],
    "HP INSTANT INK": ["hp instant ink", "hp ink"],
    "CURSOR": ["cursor", "anysphere"],
    "GETTOBY": ["toby", "gettoby"],
    "MENTIMETER": ["mentimeter"],
    "BITDEFENDER": ["bitdefender"],
    "2CO.COM": ["bitdefender", "2checkout"],
    "GITKRAKEN": ["gitkraken"],
    "LINKEDIN": ["linkedin"],
    "CEREBRAS": ["cerebras"],
    "OPENROUTER": ["openrouter"],
    "SPEAKER DECK": ["speakerdeck", "speaker deck"],
    "POLYCAM": ["polycam"],
    "PRAGMATICENGINEER": ["pragmatic engineer"],
    "TURING POST": ["turing post"],
    "VINDSURF": ["windsurf", "exafunction"],  # MC-Tippfehler für WINDSURF
    "AWS": ["aws", "amazon web services"],
    "SUMUP": ["sumup"],
    "MB-TICKETS": ["mb-tickets", "mercedes"],
}


def _get_search_keywords(vendor: str) -> list[str]:
    """Extrahiert Suchbegriffe aus einem Vendor-Namen.

    1. Prüft zuerst die explizite VENDOR_KEYWORDS-Map.
    2. Fallback: bereinigt den MC-Vendor-Namen intelligent:
       - Entfernt Referenz-IDs, Rechtsformen, Städte, www/TLD
       - Splittet an Pipe/Stern-Trennzeichen
       - Gibt den bereinigten Kern-Namen als Suchbegriff zurück
    """
    vendor_upper = vendor.upper()
    for prefix, keywords in VENDOR_KEYWORDS.items():
        if prefix in vendor_upper:
            return keywords

    # ── Fallback: intelligente Bereinigung ──────────────────────
    clean = vendor

    # Pipe-Separator (z.B. "2CO.COM|BITDEFENDER") → beide Teile
    if "|" in clean:
        parts = [p.strip() for p in clean.split("|") if p.strip()]
        # Den aussagekräftigeren Teil nehmen (längster ohne Zahlen)
        parts.sort(key=lambda p: (-len(re.sub(r"[^a-zA-Z]", "", p)), p))
        clean = parts[0] if parts else clean

    # Stern-Separator (z.B. "AMZN Mktp DE*Z11DP7IC4") → Teil vor dem Stern
    if "*" in clean:
        clean = clean.split("*")[0].strip()

    # Komma-Separator (z.B. "GITHUB, INC.") → Teil vor dem Komma
    if "," in clean:
        clean = clean.split(",")[0].strip()

    # Hash-Separator (z.B. "MICROSOFT#G139383942") → Teil vor dem Hash
    if "#" in clean:
        clean = clean.split("#")[0].strip()

    # Rechtsformen, Städte, Länder entfernen
    clean = re.sub(
        r"\s*\b(GmbH|AG|Ltd|Inc\.?|LLC|INC|S\.?R\.?O\.?|Co\.?\s*KG|& Co\.?|"
        r"SAN FRANCISCO|BERLIN|DUBLIN|NEW YORK|LONDON|LISBOA|LUXEMBOURG|"
        r"AMSTERDAM|BROOKLYN|SINGAPORE|BASTROP|MOUNTAIN VIEW|PRAGUE|"
        r"GUNZENHAUSEN|KARLSRUHE|DARMSTADT|US|DE)\b.*",
        "", clean, flags=re.IGNORECASE,
    ).strip()

    # www. Prefix entfernen
    clean = re.sub(r"^(?:WWW\.)", "", clean, flags=re.IGNORECASE)

    # Domain-Suffixe entfernen (.COM, .DE, .IO, .AI, .NET, etc.)
    clean = re.sub(r"\.(COM|DE|IO|AI|NET|ORG|CO)\b/?.*", "", clean, flags=re.IGNORECASE).strip()

    # Alphanumerische Referenz-IDs am Ende entfernen (z.B. "T060V1XRN", "KD 82137639")
    # Pattern: ein Token am Ende das Ziffern+Buchstaben mischt und >= 5 Zeichen lang ist
    clean = re.sub(r"\s+[A-Z0-9]{5,}$", "", clean, flags=re.IGNORECASE).strip()
    # Auch "KD 12345" Muster (Kürzel + Nummer)
    clean = re.sub(r"\s+[A-Z]{1,3}\s+\d{4,}$", "", clean, flags=re.IGNORECASE).strip()

    # Führendes "THE " entfernen
    clean = re.sub(r"^THE\s+", "", clean, flags=re.IGNORECASE).strip()

    # Trailing Satzzeichen
    clean = clean.strip(".,;: ")

    if clean and len(clean) >= 3:
        return [clean.lower()]

    # Letzter Fallback: Originalname vor erstem Trennzeichen
    raw = vendor.split(",")[0].split("*")[0].split("|")[0].split("#")[0].strip()
    raw = re.sub(r"^(?:WWW\.)", "", raw, flags=re.IGNORECASE)
    return [raw.lower()] if raw and len(raw) >= 2 else [vendor.lower()]


def _parse_date(date_str: str) -> datetime | None:
    """Parst ein Datum — delegiert an zentrale parse_date()."""
    from src.util import parse_date
    return parse_date(date_str)


def calc_billing_period(entries: list[dict]) -> tuple[datetime, datetime] | None:
    """Berechnet den Abrechnungszeitraum aus den Entries (frühestes + spätestes Belegdatum)."""
    all_dates = [_parse_date(e.get("date", "")) for e in entries]
    valid_dates = [d for d in all_dates if d is not None]
    if valid_dates:
        return (min(valid_dates), max(valid_dates))
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

    # Bonus: Hat PDF-Anhang (Tiebreaker — echte Rechnung > Email-Body)
    # Nur wenn bereits ein Keyword-Match vorliegt (score > 0), sonst
    # würden beliebige Emails mit Anhang falsch gematcht
    if msg.get("hasAttachments") and score > 0:
        score += 1

    # Bonus: Absender sieht nach Billing/Service aus
    if any(p in sender for p in ["billing", "invoice", "receipt", "service@", "aboservice", "feedback@", "noreply@tax"]):
        score += 2

    # Malus: Own outgoing email, not a vendor receipt
    if OWN_EMAIL_DOMAIN and OWN_EMAIL_DOMAIN.lower() in sender:
        score -= 8

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


def search_receipts_for_entry(
    token: str,
    folder_ids: list[str],
    entry: dict,
    billing_period: tuple[datetime, datetime] | None = None,
) -> list[dict]:
    """Sucht passende Emails zu einem MC-Eintrag in den angegebenen Ordnern.

    Durchsucht ALLE Keywords in ALLEN Ordnern und sammelt Kandidaten,
    statt beim ersten Treffer abzubrechen. Dedupliziert nach Message-ID.

    Args:
        billing_period: (frühestes_datum, spätestes_datum) des MC-Abrechnungszeitraums.
            Wenn angegeben, wird das Suchfenster relativ zum gesamten Zeitraum berechnet
            statt nur relativ zum einzelnen Eintrag. So werden auch Emails gefunden, die
            kurz vor oder nach dem Abrechnungszeitraum eingegangen sind.
    """
    keywords = _get_search_keywords(entry.get("vendor", ""))
    date = _parse_date(entry.get("date", ""))
    amount = entry.get("amount", 0)

    if not date:
        return []

    # Zeitfenster für Post-Filter:
    # Wenn billing_period bekannt: Suche von (frühestes Datum - 7 Tage) bis (spätestes Datum + 14 Tage).
    # So werden Emails gefunden die VOR der MC-Buchung kommen (z.B. Rechnung am 1., Buchung am 3.)
    # und solche die NACH dem Abrechnungszeitraum eintreffen (z.B. verspätete Bestätigungen).
    if billing_period:
        period_start, period_end = billing_period
        date_from = period_start - timedelta(days=7)
        date_to = period_end + timedelta(days=14)
    else:
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
                            msg["_best_keyword"] = keyword
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
        subject = (c.get("subject") or "").lower()
        sender = (c.get("from", {}).get("emailAddress", {}).get("address") or "").lower()
        best_kw = (c.get("_best_keyword") or "").lower()
        vendor_in_subject_or_sender = best_kw and (best_kw in subject or best_kw in sender)
        # Score 2 allein (nur Receipt-Term, kein Vendor-Match) -> zu schwach
        # Ausnahme: Billing-Absender haben mindestens Score 4 (Receipt-Term + Billing-Sender)
        if score == 2:
            if not any(p in sender for p in ["billing", "invoice", "receipt", "service@"]):
                continue
        # No vendor keyword in subject or sender -> require higher score
        # (prevents Graph $search body-only matches from being accepted)
        if not vendor_in_subject_or_sender and score < 5:
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


def _is_receipt_email(content: str) -> bool:
    """Prüft ob ein Email-Body tatsächlich eine Rechnung/Beleg enthält.

    Filtert Abo-Bestätigungen, Account-Updates und Newsletter raus.
    Ein echter Beleg enthält typischerweise einen Betrag (€/$/EUR) oder
    Rechnungs-Begriffe im Body-Text.
    """
    text = re.sub(r'<[^>]+>', ' ', content).lower()
    text = re.sub(r'\s+', ' ', text)

    # Muss mindestens einen Geldbetrag enthalten (€, $, EUR, USD)
    has_amount = bool(re.search(r'[\$€]\s*\d|EUR\s*\d|\d[.,]\d{2}\s*(?:€|EUR|\$|USD)', text, re.IGNORECASE))

    # Oder Receipt/Invoice-Begriffe im Body
    receipt_signals = ["rechnung", "invoice", "receipt", "beleg", "quittung",
                       "billing statement", "payment received", "amount due",
                       "total:", "gesamt:", "betrag:", "netto", "brutto",
                       "mwst", "vat", "tax"]
    has_receipt_signal = any(s in text for s in receipt_signals)

    return has_amount or has_receipt_signal


def _save_email_body_as_pdf(token: str, message_id: str, download_dir: Path, prefix: str) -> Path | None:
    """Speichert den HTML-Body einer Email als Beleg (nur wenn es ein echter Receipt ist).

    Filtert Abo-Bestätigungen, Account-Updates und Newsletter raus.
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

    # Prüfe ob der Body tatsächlich Rechnungsinhalte hat
    if not _is_receipt_email(content):
        return None

    try:
        if content_type != "html":
            content = f"<html><body><pre style='font-family:sans-serif'>{content}</pre></body></html>"

        fname = f"{prefix}email_receipt.html.pdf"
        save_path = download_dir / fname

        # HTML → PDF via Playwright (headless Chromium)
        pdf_bytes = _html_to_pdf(content)
        if pdf_bytes:
            save_path.write_bytes(pdf_bytes)
            if save_path.stat().st_size > 500:
                return save_path

        # Fallback: HTML speichern wenn PDF-Konvertierung fehlschlägt
        save_path = download_dir / f"{prefix}email_receipt.html"
        save_path.write_text(content, encoding="utf-8")
        if save_path.stat().st_size > 100:
            return save_path
    except Exception:
        pass

    return None


_pdf_pw = None
_pdf_browser = None


def _html_to_pdf(html_content: str) -> bytes | None:
    """Konvertiert HTML-Content zu PDF via Playwright (shared Browser-Instanz)."""
    global _pdf_pw, _pdf_browser
    try:
        from playwright.sync_api import sync_playwright
        if _pdf_browser is None:
            _pdf_pw = sync_playwright().start()
            _pdf_browser = _pdf_pw.chromium.launch(headless=True)
        page = _pdf_browser.new_page()
        page.set_content(html_content, wait_until="networkidle")
        pdf_bytes = page.pdf(format="A4", margin={"top": "1cm", "bottom": "1cm", "left": "1cm", "right": "1cm"})
        page.close()
        return pdf_bytes
    except Exception:
        return None


def _cleanup_pdf_browser():
    """Schliesst den shared Playwright-Browser für HTML→PDF Konvertierung."""
    global _pdf_pw, _pdf_browser
    if _pdf_browser:
        try:
            _pdf_browser.close()
        except Exception:
            pass
        _pdf_browser = None
    if _pdf_pw:
        try:
            _pdf_pw.stop()
        except Exception:
            pass
        _pdf_pw = None


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

    billing_period = calc_billing_period(debits)
    if billing_period:
        bp_start = billing_period[0].strftime("%d.%m.%Y")
        bp_end = billing_period[1].strftime("%d.%m.%Y")
        print(f"  📅 Abrechnungszeitraum: {bp_start} – {bp_end}")

    print(f"  🔍 Suche Belege für {len(debits)} Einträge ...\n")

    for idx, entry in enumerate(debits, 1):
        vendor = entry.get("vendor", "?")
        amount = entry.get("amount", 0)
        date = entry.get("date", "")
        print(f"  [{idx}/{len(debits)}] {vendor:<30s}  {amount:>8.2f} EUR  ({date})")

        candidates = search_receipts_for_entry(token, search_folders, entry, billing_period)
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

        files = []
        if msg.get("_has_attachments"):
            files = download_attachments(token, msg["id"], download_dir, prefix)

        if files:
            for f in files:
                print(f"         📎 {f.name}")
            all_files.extend(files)
            used_message_ids.add(msg["id"])
            matched.append({
                "entry": entry,
                "email_subject": msg.get("subject", ""),
                "email_id": msg["id"],
                "files": files,
            })
        else:
            # Kein (Rechnungs-)PDF-Anhang — versuche Email-Body als HTML zu speichern.
            # Greift bei: Google Play Belege, Stripe Receipts, Emails mit nur AGB-Anhängen, etc.
            score = msg.get("_score", 0)
            if score >= 4:
                body_pdf = _save_email_body_as_pdf(token, msg["id"], download_dir, prefix)
                if body_pdf:
                    print(f"         📎 {body_pdf.name} (aus Email-Body)")
                    all_files.append(body_pdf)
                    used_message_ids.add(msg["id"])
                    matched.append({
                        "entry": entry,
                        "email_subject": msg.get("subject", ""),
                        "email_id": msg["id"],
                        "files": [body_pdf],
                    })
                else:
                    print(f"         ⚠️ Email ohne verwertbaren Inhalt")
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
