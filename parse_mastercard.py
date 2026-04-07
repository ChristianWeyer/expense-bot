"""
Mastercard-PDF Parser
=====================
Extrahiert Buchungseinträge aus Mastercard/BusinessCard-Abrechnungs-PDFs
(qards/Sparkasse Format) mittels GPT Vision API.

Nutzung:
    from parse_mastercard import extract_db_bookings, extract_all_entries
    bookings = extract_db_bookings("path/to/mastercard.pdf")
    all_entries = extract_all_entries("path/to/mastercard.pdf")

    # Oder standalone:
    python parse_mastercard.py mastercard.pdf
    python parse_mastercard.py --all mastercard.pdf
"""

import base64
import json
import os
import sys
from pathlib import Path


# LLM-Konfiguration
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-5.4")

DB_EXTRACTION_PROMPT = """Extract ALL Deutsche Bahn (DB Vertrieb GmbH) entries from this Mastercard/BusinessCard statement. Check ALL pages carefully.

For each entry return a JSON object with:
- booking_ref: the booking reference number (10-14 digits after "DB Vertrieb GmbH,")
- amount: the EUR amount (as number, from the "Betrag in EUR" column)
- date: the Belegdatum (DD.MM.YY)
- booking_date: the Buchungsdatum (DD.MM.YY)
- is_credit: true if the amount has a + sign (Gutschrift/Storno), false if - sign (Belastung)

Return ONLY a JSON array, no other text."""

ALL_ENTRIES_PROMPT = """Extract entries from this Mastercard/BusinessCard statement. Check ALL pages carefully.
Skip subtotals ("Zwischensumme"), carry-overs ("Übertrag"), and currency conversion fee lines ("2% für Währungsumrechnung").

IMPORTANT: If some entries are highlighted/marked in yellow, extract ONLY those highlighted entries.
If NO entries are highlighted, extract ALL entries.

For each entry return a JSON object with:
- vendor: the merchant/company name (e.g. "ANTHROPIC", "Amazon.de", "DB Vertrieb GmbH")
- description: any additional text after the vendor (reference numbers, order IDs, city etc.)
- amount: the EUR amount as a positive number (from the "Betrag in EUR" column)
- date: the Belegdatum (DD.MM.YY)
- booking_date: the Buchungsdatum (DD.MM.YY)
- is_credit: true if the amount has a + sign (Gutschrift/Storno), false if - sign (Belastung)
- category: "db" if vendor contains "DB Vertrieb", otherwise "other"
- booking_ref: the DB booking reference number (only for DB Vertrieb entries, null otherwise)

Return ONLY a JSON array, no other text."""


def _pdf_to_images(pdf_path: Path) -> list[dict]:
    """Konvertiert PDF-Seiten in Base64-kodierte PNG-Bilder für die Vision API."""
    try:
        import fitz
    except ImportError:
        print("PyMuPDF nicht installiert. Bitte installiere:")
        print("    pip install pymupdf")
        sys.exit(1)

    doc = fitz.open(str(pdf_path))
    images = []
    for page in doc:
        pix = page.get_pixmap(dpi=200)
        b64 = base64.b64encode(pix.tobytes("png")).decode()
        images.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "high"},
        })
    doc.close()
    return images


def _call_llm(pdf_path: Path, prompt: str, max_tokens: int = 8000) -> list[dict]:
    """Sendet PDF-Bilder an die Vision API und parst die JSON-Antwort."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("❌ OPENAI_API_KEY nicht gesetzt in .env")
        sys.exit(1)

    from openai import OpenAI
    client = OpenAI(api_key=api_key)

    images = _pdf_to_images(pdf_path)
    print(f"  🤖 Sende {len(images)} Seiten an {LLM_MODEL} ...")

    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                *images,
            ],
        }],
        max_completion_tokens=max_tokens,
    )

    result = response.choices[0].message.content
    tokens = response.usage.total_tokens if response.usage else 0
    print(f"  ✅ Antwort erhalten ({tokens} Tokens)")

    # JSON parsen (ggf. Markdown-Code-Block entfernen)
    clean = result.strip()
    if clean.startswith("```"):
        clean = clean.split("\n", 1)[1].rsplit("```", 1)[0]

    try:
        entries = json.loads(clean)
    except json.JSONDecodeError as e:
        print(f"  ❌ JSON-Parse-Fehler: {e}")
        print(f"     Antwort: {result[:200]}")
        return []

    # Beträge normalisieren (manche Modelle liefern negative Zahlen statt is_credit)
    for entry in entries:
        entry["amount"] = abs(entry.get("amount", 0))

    return entries


def extract_db_bookings(pdf_path: str | Path) -> list[dict]:
    """Extrahiert nur Deutsche Bahn Buchungen aus dem Mastercard-PDF."""
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF nicht gefunden: {pdf_path}")
    return _call_llm(pdf_path, DB_EXTRACTION_PROMPT, max_tokens=4000)


def extract_all_entries(pdf_path: str | Path) -> list[dict]:
    """Extrahiert ALLE Buchungseinträge aus dem Mastercard-PDF."""
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF nicht gefunden: {pdf_path}")
    return _call_llm(pdf_path, ALL_ENTRIES_PROMPT, max_tokens=8000)


def get_db_entries(entries: list[dict]) -> list[dict]:
    """Filtert nur DB Vertrieb Einträge."""
    return [e for e in entries if e.get("category") == "db"]


def get_non_db_entries(entries: list[dict]) -> list[dict]:
    """Filtert alle Einträge außer DB Vertrieb (für Belegsuche)."""
    return [e for e in entries if e.get("category") != "db"]


def get_net_bookings(bookings: list[dict]) -> list[dict]:
    """Filtert Gutschriften/Stornos heraus. Nur Belastungen (Debits)."""
    return [b for b in bookings if not b.get("is_credit")]


def print_summary(bookings: list[dict], title: str = "DB-Buchungen"):
    """Gibt eine Zusammenfassung der Buchungen aus."""
    debits = [b for b in bookings if not b.get("is_credit")]
    credits = [b for b in bookings if b.get("is_credit")]

    print(f"\n{'=' * 60}")
    print(f"  {title}: {len(bookings)} gesamt")
    print(f"  Belastungen: {len(debits)}  |  Gutschriften: {len(credits)}")
    print(f"{'=' * 60}\n")

    for b in bookings:
        sign = "+" if b.get("is_credit") else "-"
        typ = "GUTSCHRIFT" if b.get("is_credit") else "BELASTUNG "
        vendor = b.get("vendor", b.get("booking_ref", "?"))
        print(
            f"  {typ}  {b.get('date', ''):>8s}  "
            f"{vendor:<35s}  "
            f"{sign}{b['amount']:>8.2f} EUR"
        )

    if debits:
        total = sum(b["amount"] for b in debits)
        print(f"\n  Summe Belastungen: {total:.2f} EUR")

    net = get_net_bookings(bookings)
    print()
    return net


# ─── Standalone ──────────────────────────────────────────
if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")

    if len(sys.argv) < 2:
        print("Nutzung: python parse_mastercard.py <pfad-zum-mastercard.pdf>")
        print("         python parse_mastercard.py --all <pdf>   (alle Einträge)")
        sys.exit(1)

    show_all = "--all" in sys.argv
    pdf_files = [a for a in sys.argv[1:] if a != "--all"]

    for pdf in pdf_files:
        print(f"\n📄 Lese: {pdf}")
        if show_all:
            entries = extract_all_entries(pdf)
            db = get_db_entries(entries)
            non_db = get_non_db_entries(entries)
            print_summary(db, "DB-Buchungen")
            print_summary(non_db, "Sonstige Belege")
        else:
            bookings = extract_db_bookings(pdf)
            print_summary(bookings)
