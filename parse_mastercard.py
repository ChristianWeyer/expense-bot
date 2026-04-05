"""
Mastercard-PDF Parser
=====================
Extrahiert DB-Buchungsnummern aus Mastercard/BusinessCard-Abrechnungs-PDFs
(qards/Sparkasse Format).

Die DB-Einträge haben das Format:
    DD.MM.YY DD.MM.YY DB Vertrieb GmbH, 123456789012  260,10-

Die 12-stellige Nummer nach "DB Vertrieb GmbH, " ist die Auftragsnummer.
Einträge mit "+" am Ende sind Gutschriften/Stornos.

Nutzung:
    from parse_mastercard import extract_db_bookings
    bookings = extract_db_bookings("path/to/mastercard.pdf")

    # Oder standalone:
    python parse_mastercard.py mastercard.pdf
"""

import re
import sys
from pathlib import Path


def extract_text_from_pdf(pdf_path: str | Path) -> str:
    """Extrahiert Text aus einem PDF."""
    pdf_path = Path(pdf_path)

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF nicht gefunden: {pdf_path}")

    # Versuch 1: pdfplumber (bestes Layout-Ergebnis für dieses Format)
    try:
        import pdfplumber

        text = ""
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages:
                text += (page.extract_text() or "") + "\n"
        return text
    except ImportError:
        pass

    # Versuch 2: PyMuPDF
    try:
        import fitz

        doc = fitz.open(str(pdf_path))
        text = ""
        for page in doc:
            text += page.get_text() + "\n"
        doc.close()
        return text
    except ImportError:
        pass

    # Versuch 3: pdftotext (poppler)
    try:
        import subprocess

        result = subprocess.run(
            ["pdftotext", "-layout", str(pdf_path), "-"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result.stdout
    except FileNotFoundError:
        pass

    print("Kein PDF-Reader verfügbar. Bitte installiere:")
    print("    pip install pdfplumber")
    sys.exit(1)


def extract_db_bookings(pdf_path: str | Path) -> list[dict]:
    """
    Extrahiert Deutsche Bahn Buchungen aus dem Mastercard/BusinessCard-PDF.

    Returns:
        Liste von Dicts mit:
          - booking_ref: 12-stellige Auftragsnummer
          - amount: Betrag in EUR (als float)
          - date: Belegdatum (DD.MM.YY)
          - booking_date: Buchungsdatum (DD.MM.YY)
          - is_credit: True wenn Gutschrift/Storno (+)
          - raw_line: Original-Zeile
    """
    text = extract_text_from_pdf(pdf_path)
    lines = text.split("\n")
    results = []

    # Pattern für DB-Einträge:
    # DD.MM.YY DD.MM.YY DB Vertrieb GmbH, XXXXXXXXXXXX  Betrag[+-]
    db_pattern = re.compile(
        r"(\d{2}\.\d{2}\.\d{2})\s+"       # Belegdatum
        r"(\d{2}\.\d{2}\.\d{2})\s+"       # Buchungsdatum
        r"DB\s+Vertrieb\s+GmbH,\s*"       # Unternehmen
        r"(\d{10,14})\s+"                  # Auftragsnummer (10-14 stellig)
        r"([\d.,]+)"                       # Betrag
        r"([+-])"                          # Vorzeichen (- = Belastung, + = Gutschrift)
    )

    for line in lines:
        match = db_pattern.search(line)
        if match:
            date_str = match.group(1)
            booking_date = match.group(2)
            booking_ref = match.group(3)
            amount_str = match.group(4).replace(".", "").replace(",", ".")
            is_credit = match.group(5) == "+"

            try:
                amount = float(amount_str)
            except ValueError:
                amount = 0.0

            results.append({
                "booking_ref": booking_ref,
                "amount": amount,
                "date": date_str,
                "booking_date": booking_date,
                "is_credit": is_credit,
                "raw_line": line.strip(),
            })

    return results


def get_net_bookings(bookings: list[dict]) -> list[dict]:
    """
    Filtert Gutschriften/Stornos heraus.
    Gibt nur Belastungen (Debits) zurück, die tatsächlich eine Rechnung benötigen.
    """
    return [b for b in bookings if not b["is_credit"]]


def print_summary(bookings: list[dict], show_credits: bool = True):
    """Gibt eine Zusammenfassung der gefundenen DB-Buchungen aus."""
    debits = [b for b in bookings if not b["is_credit"]]
    credits = [b for b in bookings if b["is_credit"]]

    print(f"\n{'=' * 60}")
    print(f"  DB-Buchungen gefunden: {len(bookings)} gesamt")
    print(f"  Belastungen: {len(debits)}  |  Gutschriften: {len(credits)}")
    print(f"{'=' * 60}\n")

    for b in bookings:
        sign = "+" if b["is_credit"] else "-"
        typ = "GUTSCHRIFT" if b["is_credit"] else "BELASTUNG "
        print(
            f"  {typ}  {b['date']}  "
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
