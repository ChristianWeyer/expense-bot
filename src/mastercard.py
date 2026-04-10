"""
Mastercard-PDF Parser
=====================
Extrahiert Buchungseinträge aus Mastercard/BusinessCard-Abrechnungs-PDFs
(qards/Sparkasse Format) mittels GPT Vision API.

Strategie:
  - Seitenweise Extraktion (pro Seite ein LLM-Call)
  - Zwischensummen-Verifikation pro Seite
  - Gesamtsaldo-Verifikation über alle Seiten
  - FX-Gebühren als eigene Kategorie (für Vollständigkeit, aber kein Beleg nötig)
  - Retry bei Verifikationsfehlern

Nutzung:
    from src.mastercard import extract_all_entries
    entries = extract_all_entries("path/to/mastercard.pdf")
"""

import base64
import json
import os
import re
import sys
from pathlib import Path


# LLM-Konfiguration
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-5.4")

PAGE_EXTRACTION_PROMPT = """Extract ALL entries from this SINGLE PAGE of a Mastercard/BusinessCard statement.
The statement is in German (qards/Sparkasse format).

IMPORTANT RULES:
1. Extract EVERY line item — vendors, charges, credits.
2. CRITICAL — "2% für Währungsumrechnung" lines: These ALWAYS appear directly below a foreign-currency entry. They are bank FX fees. Extract them with category "fx_fee" and vendor "FX Währungsumrechnung [PARENT_VENDOR]" where PARENT_VENDOR is the vendor from the line directly above. The EUR amount is the small fee (typically 0.12 to 6.37 EUR), NOT the USD conversion amount.
3. Many entries come in PAIRS: the main charge (e.g. "ANTHROPIC, SAN FRANCISCO ... USD 119.15 ... 103.36 -") followed by an FX fee line ("2% für Währungsumrechnung ... 2.07 -"). Always extract BOTH as separate entries.
4. Skip ONLY: "Zwischensumme", "Übertrag von Seite", headers, footers, and address blocks.
5. For entries with foreign currency: the EUR amount is in the rightmost "Betrag in EUR" column.
6. Credits have a "+" sign → is_credit: true. Debits have a "-" sign → is_credit: false.
7. Do NOT invent vendor names. Every vendor name must come directly from the text on the page. If a line says "2% für Währungsumrechnung", the vendor is NOT some other company — it is an FX fee.

{yellow_instruction}

For each entry return a JSON object with:
- vendor: the merchant/company name (e.g. "ANTHROPIC", "Amazon.de", "DB Vertrieb GmbH")
- description: additional text after the vendor (reference numbers, order IDs, city etc.)
- amount: the EUR amount as a positive number (from the "Betrag in EUR" column)
- date: the Belegdatum (DD.MM.YY format)
- booking_date: the Buchungsdatum (DD.MM.YY format)
- is_credit: true if the amount has a + sign (Gutschrift/Storno), false if - sign (Belastung)
- category: "db" if vendor contains "DB Vertrieb" or "D B Vertrieb", "fx_fee" for currency conversion fee lines, otherwise "other"
- booking_ref: the DB booking reference number (only for DB Vertrieb entries, null otherwise)
- marked: true if the entry is visually highlighted (yellow/colored background), false otherwise

Also extract the page subtotal if present:
- Look for "Zwischensumme von Seite X" — return the EUR amount as "page_subtotal"
- Look for "Neuer Saldo" — return the EUR amount as "final_total"
- Look for "Übertrag von Seite X" — return the EUR amount as "carry_over"

Return a JSON object with this structure:
{{
  "entries": [...],
  "page_subtotal": <number or null>,
  "carry_over": <number or null>,
  "final_total": <number or null>
}}

Return ONLY valid JSON, no other text."""


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


def _call_llm_single_page(client, page_image: dict, prompt: str, page_num: int, total_pages: int) -> dict:
    """Sendet ein einzelnes Seitenbild an die Vision API und parst die JSON-Antwort."""
    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                page_image,
            ],
        }],
        max_completion_tokens=4000,
    )

    result = response.choices[0].message.content
    tokens = response.usage.total_tokens if response.usage else 0
    print(f"    Seite {page_num}/{total_pages}: {tokens} Tokens")

    clean = result.strip()
    if clean.startswith("```"):
        clean = clean.split("\n", 1)[1].rsplit("```", 1)[0]

    try:
        parsed = json.loads(clean)
    except json.JSONDecodeError as e:
        print(f"    JSON-Parse-Fehler Seite {page_num}: {e}")
        print(f"    Antwort: {result[:300]}")
        return {"entries": [], "page_subtotal": None, "carry_over": None, "final_total": None}

    # Normalize: handle both old format (plain list) and new format (object with entries)
    if isinstance(parsed, list):
        parsed = {"entries": parsed, "page_subtotal": None, "carry_over": None, "final_total": None}

    entries = parsed.get("entries", [])
    for entry in entries:
        entry["amount"] = abs(entry.get("amount", 0))
        entry["_page"] = page_num

    return parsed


def _verify_page(page_result: dict, page_num: int) -> tuple[bool, str]:
    """Verifiziert eine Seite anhand der Zwischensumme."""
    entries = page_result.get("entries", [])
    subtotal = page_result.get("page_subtotal")
    carry_over = page_result.get("carry_over", 0) or 0

    if subtotal is None:
        return True, "keine Zwischensumme auf dieser Seite"

    # Summe der Einträge dieser Seite + Übertrag sollte = Zwischensumme sein
    # Debits (−) addieren, Credits (+) subtrahieren
    page_sum = 0
    for e in entries:
        amt = e.get("amount", 0)
        if e.get("is_credit"):
            page_sum -= amt
        else:
            page_sum += amt

    expected = abs(subtotal) - abs(carry_over)
    diff = abs(page_sum - expected)

    if diff < 1.0:  # ±1 EUR Toleranz für Rundungsfehler
        return True, f"Summe OK (Diff: {diff:.2f})"

    return False, f"Summe Seite {page_num}: {page_sum:.2f}, erwartet {expected:.2f} (Diff: {diff:.2f})"


def _verify_total(all_entries: list[dict], final_total: float | None) -> tuple[bool, str]:
    """Verifiziert die Gesamtsumme aller extrahierten Einträge.

    Der Saldo im PDF ist typischerweise negativ (Schuld) — wir vergleichen
    die absolute Summe der Einträge (Debits - Credits) mit abs(final_total).
    """
    if final_total is None:
        return True, "kein Gesamtsaldo im PDF"

    total = 0
    for e in all_entries:
        amt = e.get("amount", 0)
        if e.get("is_credit"):
            total -= amt
        else:
            total += amt

    diff = abs(abs(total) - abs(final_total))

    if diff < 2.0:  # ±2 EUR Toleranz (Rundungen ueber viele Eintraege)
        return True, f"Gesamtsaldo OK: {abs(total):.2f} vs {abs(final_total):.2f} (Diff: {diff:.2f})"

    return False, f"Gesamtsaldo ABWEICHUNG: berechnet {abs(total):.2f} vs PDF {abs(final_total):.2f} (Diff: {diff:.2f})"


def extract_all_entries(pdf_path: str | Path, marked_only: bool = False, max_retries: int = 2) -> list[dict]:
    """Extrahiert alle Buchungseinträge seitenweise mit Verifikation.

    Args:
        pdf_path: Pfad zum Mastercard-PDF.
        marked_only: Wenn True, nur gelb markierte Einträge extrahieren.
        max_retries: Anzahl Retry-Versuche bei Verifikationsfehlern.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF nicht gefunden: {pdf_path}")

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("OPENAI_API_KEY nicht gesetzt in .env")
        sys.exit(1)

    from openai import OpenAI
    client = OpenAI(api_key=api_key)

    images = _pdf_to_images(pdf_path)
    total_pages = len(images)
    print(f"  Sende {total_pages} Seiten einzeln an {LLM_MODEL} ...")

    if marked_only:
        yellow_instruction = (
            "IMPORTANT: Still extract ALL entries (for verification), but set marked: true\n"
            "for entries that are visually highlighted (yellow/colored background).\n"
            "Set marked: false for all other entries."
        )
    else:
        yellow_instruction = "Set marked: false for all entries (no filtering requested)."

    prompt = PAGE_EXTRACTION_PROMPT.format(yellow_instruction=yellow_instruction)

    all_entries = []
    final_total = None
    verification_issues = []

    for page_num, page_image in enumerate(images, 1):
        attempt = 0
        page_ok = False
        page_result = None

        while attempt <= max_retries and not page_ok:
            if attempt > 0:
                print(f"    Retry {attempt}/{max_retries} für Seite {page_num} ...")

            page_result = _call_llm_single_page(client, page_image, prompt, page_num, total_pages)
            ok, msg = _verify_page(page_result, page_num)

            if ok:
                page_ok = True
                if "OK" in msg:
                    print(f"    {msg}")
            else:
                print(f"    Verifikation fehlgeschlagen: {msg}")
                attempt += 1

        if page_result:
            page_entries = page_result.get("entries", [])
            all_entries.extend(page_entries)

            if page_result.get("final_total") is not None:
                final_total = page_result["final_total"]

            if not page_ok:
                verification_issues.append(f"Seite {page_num}: Zwischensumme nicht verifizierbar")

    # Gesamtsaldo-Verifikation
    total_ok, total_msg = _verify_total(all_entries, final_total)
    print(f"  {total_msg}")

    if not total_ok:
        verification_issues.append(total_msg)

    # Statistiken
    debits = [e for e in all_entries if not e.get("is_credit") and e.get("category") != "fx_fee"]
    credits = [e for e in all_entries if e.get("is_credit")]
    fx_fees = [e for e in all_entries if e.get("category") == "fx_fee"]
    db_entries = [e for e in all_entries if e.get("category") == "db"]

    print(f"  Extrahiert: {len(debits)} Belastungen, {len(credits)} Gutschriften, "
          f"{len(fx_fees)} FX-Gebühren, {len(db_entries)} DB-Buchungen")

    if verification_issues:
        print(f"  WARNUNG: {len(verification_issues)} Verifikationsprobleme:")
        for issue in verification_issues:
            print(f"    - {issue}")

    # Assign unique IDs
    for idx, entry in enumerate(all_entries):
        entry["_id"] = f"p{entry.get('_page', 0)}_{idx}"

    # Filter auf markierte Einträge (nach Verifikation!)
    if marked_only:
        marked = [e for e in all_entries if e.get("marked")]
        if marked:
            print(f"  → Nur gelb markierte Einträge: {len(marked)} von {len(all_entries)}")
            return marked
        else:
            print(f"  ⚠️  Keine gelb markierten Einträge erkannt — verwende alle {len(all_entries)}")

    return all_entries


def extract_db_bookings(pdf_path: str | Path) -> list[dict]:
    """Extrahiert nur Deutsche Bahn Buchungen aus dem Mastercard-PDF."""
    all_entries = extract_all_entries(pdf_path)
    return get_db_entries(all_entries)


def get_db_entries(entries: list[dict]) -> list[dict]:
    """Filtert nur DB Vertrieb Einträge."""
    return [e for e in entries if e.get("category") == "db"]


def get_non_db_entries(entries: list[dict]) -> list[dict]:
    """Filtert alle Einträge außer DB Vertrieb und FX-Gebühren."""
    return [e for e in entries if e.get("category") not in ("db", "fx_fee")]


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


# --- Standalone ---
if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")

    if len(sys.argv) < 2:
        print("Nutzung: python -m src.mastercard <pfad-zum-mastercard.pdf>")
        print("         python -m src.mastercard --all <pdf>   (alle Einträge)")
        sys.exit(1)

    show_all = "--all" in sys.argv
    pdf_files = [a for a in sys.argv[1:] if a != "--all"]

    for pdf in pdf_files:
        print(f"\n Lese: {pdf}")
        entries = extract_all_entries(pdf)
        db = get_db_entries(entries)
        non_db = get_non_db_entries(entries)
        print_summary(db, "DB-Buchungen")
        if show_all:
            print_summary(non_db, "Sonstige Belege")
