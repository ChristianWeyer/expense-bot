"""Zentrale Ergebnis-Datenstruktur für die Entry->PDF Zuordnung."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class EntryResult:
    """Ergebnis für einen einzelnen MC-Eintrag."""
    entry: dict                          # Der MC-Eintrag (vendor, amount, date, ...)
    status: str = "pending"              # pending | matched | unmatched | link_only | skipped
    source: str = ""                     # Quelle: "outlook", "bahn", "amazon", "portal:openai", "heise", ...
    files: list[Path] = field(default_factory=list)  # Zugehörige PDFs
    receipt_url: str = ""                # Falls nur Link (kein PDF)
    email_subject: str = ""              # Betreff der gematchten Email
    note: str = ""                       # Zusätzliche Info (Fehlermeldung, etc.)

    @property
    def entry_id(self) -> str:
        return self.entry.get("_id", "")

    @property
    def vendor(self) -> str:
        return self.entry.get("vendor", "?")

    @property
    def amount(self) -> float:
        return self.entry.get("amount", 0)

    @property
    def date(self) -> str:
        return self.entry.get("date", "")

    @property
    def is_db(self) -> bool:
        return self.entry.get("category") == "db"

    @property
    def is_fx_fee(self) -> bool:
        return self.entry.get("category") == "fx_fee"

    @property
    def is_credit(self) -> bool:
        return self.entry.get("is_credit", False)


@dataclass
class RunResult:
    """Gesamtergebnis eines Bot-Laufs — trackt alle Einträge und ihre Belege."""
    mc_pdf_name: str = ""
    entries: list[EntryResult] = field(default_factory=list)

    def add_entries(self, raw_entries: list[dict]):
        """Fügt MC-Einträge als pending hinzu. FX-Gebühren werden als 'skipped' markiert."""
        for e in raw_entries:
            er = EntryResult(entry=e)
            if e.get("category") == "fx_fee":
                er.status = "skipped"
                er.note = "FX-Gebühr (kein Beleg nötig)"
            elif e.get("is_credit"):
                er.status = "skipped"
                er.note = "Gutschrift/Storno"
            self.entries.append(er)

    def find_entry(self, entry_id: str) -> "EntryResult | None":
        """Findet einen Eintrag per ID. Leere IDs werden ignoriert."""
        if not entry_id:
            return None
        for er in self.entries:
            if er.entry_id == entry_id:
                return er
        return None

    def mark_matched(self, entry: dict, files: list[Path], source: str, **kwargs):
        """Markiert einen Eintrag als gematcht mit PDFs.
        Nutzt _id für eindeutige Zuordnung, Fallback auf vendor+amount+date.
        """
        # Primär: per unique ID
        entry_id = entry.get("_id", "")
        if entry_id:
            er = self.find_entry(entry_id)
            if er and er.status == "pending":
                er.status = "matched"
                er.files = files
                er.source = source
                er.email_subject = kwargs.get("email_subject", "")
                er.note = kwargs.get("note", "")
                return

        # Sekundär: per object identity
        for er in self.entries:
            if er.entry is entry and er.status == "pending":
                er.status = "matched"
                er.files = files
                er.source = source
                er.email_subject = kwargs.get("email_subject", "")
                er.note = kwargs.get("note", "")
                return

        # Tertiär: Fallback vendor+amount+date (nur wenn eindeutig!)
        candidates = [
            er for er in self.entries
            if (er.status == "pending"
                and er.vendor == entry.get("vendor", "")
                and abs(er.amount - entry.get("amount", 0)) < 0.01
                and er.date == entry.get("date", ""))
        ]
        if len(candidates) == 1:
            er = candidates[0]
            er.status = "matched"
            er.files = files
            er.source = source
            er.email_subject = kwargs.get("email_subject", "")
            er.note = kwargs.get("note", "")

    def mark_link_only(self, entry: dict, receipt_url: str, source: str, **kwargs):
        """Markiert einen Eintrag als 'Link vorhanden, aber kein PDF'."""
        entry_id = entry.get("_id", "")
        if entry_id:
            er = self.find_entry(entry_id)
            if er and er.status == "pending":
                er.status = "link_only"
                er.receipt_url = receipt_url
                er.source = source
                er.email_subject = kwargs.get("email_subject", "")
                return

        for er in self.entries:
            if er.entry is entry and er.status == "pending":
                er.status = "link_only"
                er.receipt_url = receipt_url
                er.source = source
                er.email_subject = kwargs.get("email_subject", "")
                return

    def mark_unmatched(self, entry: dict, note: str = ""):
        """Markiert einen Eintrag explizit als nicht gefunden."""
        entry_id = entry.get("_id", "")
        if entry_id:
            er = self.find_entry(entry_id)
            if er and er.status == "pending":
                er.status = "unmatched"
                er.note = note
                return

        for er in self.entries:
            if er.entry is entry and er.status == "pending":
                er.status = "unmatched"
                er.note = note
                return

    # --- Abfragen ---

    @property
    def db_entries(self) -> list[EntryResult]:
        return [e for e in self.entries if e.is_db and not e.is_credit]

    @property
    def non_db_entries(self) -> list[EntryResult]:
        return [e for e in self.entries if not e.is_db and not e.is_fx_fee and not e.is_credit]

    @property
    def fx_fee_entries(self) -> list[EntryResult]:
        return [e for e in self.entries if e.is_fx_fee]

    @property
    def matched(self) -> list[EntryResult]:
        return [e for e in self.entries if e.status == "matched"]

    @property
    def unmatched(self) -> list[EntryResult]:
        return [e for e in self.entries if e.status in ("unmatched", "pending") and not e.is_credit and not e.is_fx_fee]

    @property
    def link_only(self) -> list[EntryResult]:
        return [e for e in self.entries if e.status == "link_only"]

    @property
    def skipped(self) -> list[EntryResult]:
        return [e for e in self.entries if e.status == "skipped"]

    @property
    def all_files(self) -> list[Path]:
        files = []
        for e in self.entries:
            files.extend(e.files)
        return files

    @property
    def total_debits(self) -> int:
        """Anzahl der belegpflichtigen Einträge (ohne FX-Gebühren und Credits)."""
        return len([e for e in self.entries if not e.is_credit and not e.is_fx_fee])

    def summary(self) -> str:
        """Kurzübersicht für Console-Output."""
        total = self.total_debits
        matched = len(self.matched)
        unmatched = len(self.unmatched)
        link_only = len(self.link_only)
        files = len(self.all_files)
        fx = len(self.fx_fee_entries)
        return (f"{matched}/{total} Belege gefunden ({files} PDFs), "
                f"{unmatched} offen, {link_only} nur Link, {fx} FX-Gebühren übersprungen")
