"""
Mastercard-PDF Parser
=====================
Extrahiert DB-Buchungsnummern aus Mastercard/BusinessCard-Abrechnungs-PDFs
(qards/Sparkasse Format) mittels GPT Vision API.

Nutzung:
    from parse_mastercard import extract_db_bookings
    bookings = extract_db_bookings("path/to/mastercard.pdf")

    # Oder standalone:
    python parse_mastercard.py mastercard.pdf
"""

import base64
import json
import os
import sys
from pathlib import Path


# LLM-Konfiguration
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-5.4")

EXTRACTION_PROMPT = """Extract ALL Deutsche Bahn (DB Vertrieb GmbH) entries from this Mastercard/BusinessCard statement. Check ALL pages carefully.

For each entry return a JSON object with:
- booking_ref: the booking reference number (10-14 digits after "DB Vertrieb GmbH,")
- amount: the EUR amount (as number, from the "Betrag in EUR" column)
- date: the Belegdatum (DD.MM.YY)
- booking_date: the Buchungsdatum (DD.MM.YY)
- is_credit: true if the amount has a + sign (Gutschrift/Storno), false if - sign (Belastung)

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


def extract_db_bookings(pdf_path: str | Path) -> list[dict]:
    """
    Extrahiert Deutsche Bahn Buchungen aus dem Mastercard/BusinessCard-PDF
    mittels GPT Vision API.

    Returns:
        Liste von Dicts mit:
          - booking_ref: 12-stellige Auftragsnummer
          - amount: Betrag in EUR (als float)
          - date: Belegdatum (DD.MM.YY)
          - booking_date: Buchungsdatum (DD.MM.YY)
          - is_credit: True wenn Gutschrift/Storno (+)
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF nicht gefunden: {pdf_path}")

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
                {"type": "text", "text": EXTRACTION_PROMPT},
                *images,
            ],
        }],
        max_completion_tokens=4000,
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


def get_net_bookings(bookings: list[dict]) -> list[dict]:
    """
    Filtert Gutschriften/Stornos heraus.
    Gibt nur Belastungen (Debits) zurück, die tatsächlich eine Rechnung benötigen.
    """
    return [b for b in bookings if not b.get("is_credit")]


def print_summary(bookings: list[dict], show_credits: bool = True):
    """Gibt eine Zusammenfassung der gefundenen DB-Buchungen aus."""
    debits = [b for b in bookings if not b.get("is_credit")]
    credits = [b for b in bookings if b.get("is_credit")]

    print(f"\n{'=' * 60}")
    print(f"  DB-Buchungen gefunden: {len(bookings)} gesamt")
    print(f"  Belastungen: {len(debits)}  |  Gutschriften: {len(credits)}")
    print(f"{'=' * 60}\n")

    for b in bookings:
        sign = "+" if b.get("is_credit") else "-"
        typ = "GUTSCHRIFT" if b.get("is_credit") else "BELASTUNG "
        print(
            f"  {typ}  {b.get('date', ''):>8s}  "
            f"Auftrag: {b['booking_ref']}  "
            f"{sign}{b['amount']:>8.2f} EUR"
        )

    if debits:
        total = sum(b["amount"] for b in debits)
        print(f"\n  Summe Belastungen: {total:.2f} EUR")

    net = get_net_bookings(bookings)
    if len(net) != len(debits):
        print(f"\n  Nach Storno-Filterung: {len(net)} Buchung(en) für Rechnungs-Download")

    print()
    return net


# ─── Standalone ──────────────────────────────────────────
if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")

    if len(sys.argv) < 2:
        print("Nutzung: python parse_mastercard.py <pfad-zum-mastercard.pdf>")
        print("         python parse_mastercard.py *.pdf  (mehrere Dateien)")
        sys.exit(1)

    all_bookings = []
    for pdf in sys.argv[1:]:
        print(f"\n📄 Lese: {pdf}")
        bookings = extract_db_bookings(pdf)
        all_bookings.extend(bookings)
        print_summary(bookings)

    if len(sys.argv) > 2:
        print(f"\n{'=' * 60}")
        print(f"  GESAMT über alle Dateien: {len(all_bookings)} DB-Buchungen")
        net = get_net_bookings(all_bookings)
        refs = [b["booking_ref"] for b in net]
        print(f"  Auftragsnummern für Download: {', '.join(refs)}")
        print(f"{'=' * 60}")
