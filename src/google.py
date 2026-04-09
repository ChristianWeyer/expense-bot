"""Google Payments Beleg-Download (YouTube Premium, Google One).

Transaktionen sind in einem iframe von payments.google.com.
Klickt Transaktionen per Betrag an und druckt die Seite als PDF.
"""

import re
import time
from pathlib import Path


ACTIVITY_URL = "https://pay.google.com/gp/w/home/activity"


def download_google_invoices(page, entries: list[dict], download_dir: Path) -> list[Path]:
    download_dir.mkdir(parents=True, exist_ok=True)

    google_entries = [
        e for e in entries
        if not e.get("is_credit")
        and any(k in e.get("vendor", "").upper() for k in ["GOOGLE", "YOUTUBE"])
        and "WL*GOOGLE" not in e.get("vendor", "").upper()
    ]
    if not google_entries:
        return []

    print(f"\n🔍 Google Payments: Suche {len(google_entries)} Beleg(e) ...")

    page.goto(ACTIVITY_URL, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(10000)

    # iframe finden
    iframe = None
    for frame in page.frames:
        if "payments.google.com" in frame.url and "timelineview" in frame.url:
            iframe = frame
            break

    if not iframe:
        print("  ⚠️  Kein payments.google.com iframe gefunden")
        return []

    text = iframe.evaluate("() => document.body ? document.body.innerText : ''")
    if '€' not in text and 'YouTube' not in text:
        print("  ❌ Keine Transaktionen sichtbar")
        return []

    print("  ✅ Transaktionen geladen")
    downloaded = []

    for entry in google_entries:
        amount = entry.get("amount", 0)
        date_str = entry.get("date", "")
        vendor = entry.get("vendor", "?")
        amount_str = f"{amount:.2f}".replace(".", ",")

        print(f"  🔍 {vendor}  {amount:.2f} EUR  ({date_str})")

        # Klick auf Transaktion im iframe
        found = iframe.evaluate(f"""() => {{
            const items = document.querySelectorAll('[data-was-visible="true"]');
            for (const item of items) {{
                const text = item.textContent || '';
                if ((text.includes('{amount_str}') || text.includes('−{amount_str}'))
                    && text.includes('€')) {{
                    item.click();
                    return true;
                }}
            }}
            return false;
        }}""")

        if found:
            page.wait_for_timeout(3000)

            # Seite als PDF drucken
            date_prefix = date_str.replace(".", "") + "_" if date_str else ""
            vendor_short = re.sub(r"[^\w]", "", vendor)[:20]
            fname = f"{date_prefix}{vendor_short}_Google_Beleg.pdf"
            save_path = download_dir / fname
            page.pdf(path=str(save_path), format="A4", print_background=True)

            if save_path.stat().st_size > 3000:
                downloaded.append(save_path)
                print(f"  ✅ {fname} ({save_path.stat().st_size / 1024:.1f} KB)")
            else:
                save_path.unlink(missing_ok=True)
                print(f"  ⚠️  PDF zu klein")

            # Zurück navigieren, iframe neu finden
            page.goto(ACTIVITY_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(8000)
            iframe = None
            for frame in page.frames:
                if "payments.google.com" in frame.url and "timelineview" in frame.url:
                    iframe = frame
                    break
            if not iframe:
                break
        else:
            print(f"  ⚠️  Betrag {amount_str} € nicht gefunden")

        time.sleep(1)

    if downloaded:
        print(f"  📦 {len(downloaded)} Google-Beleg(e) heruntergeladen")
    return downloaded
