"""Shared utility functions for the Expense Bot."""

import re
from datetime import datetime


def parse_date(date_str: str) -> datetime | None:
    """Parse a date string in various formats.

    Supported formats:
    - DD.MM.YYYY  (German, e.g. "21.03.2026")
    - DD.MM.YY    (German short, e.g. "21.03.26")
    - YYYY-MM-DD  (ISO, e.g. "2026-03-21")
    - Mon DD, YYYY (English, e.g. "Mar 21, 2026")
    - Month DD, YYYY (English full, e.g. "March 21, 2026")
    - DD.MM.      (Short, current year assumed, e.g. "21.03.")

    Also strips trailing time components like ", 1:31 PM".
    """
    if not date_str:
        return None

    # Strip trailing time like ", 1:31 PM"
    clean = re.sub(r",\s*\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)", "", date_str).strip()

    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(clean, fmt)
        except ValueError:
            continue

    # DD.MM. (short, assume current year)
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.$", clean)
    if m:
        try:
            return datetime(datetime.now().year, int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass

    return None
