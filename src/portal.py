"""
Generischer Vendor-Portal Scraper
==================================
Lädt Rechnungen/Belege von Vendor-Portalen per Playwright herunter.
Konfiguriert über JSON-Dateien im portals/ Ordner.

Nutzt CDP (Chrome Canary) für Portale mit Cloudflare-Schutz.
Login-Sessions bleiben in Chrome erhalten.
Auto-Login per 1Password Credentials wenn nicht eingeloggt.
"""

import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeout

from src.config import OPENAI_EMAIL, OPENAI_PASSWORD, PAGE_TIMEOUT, DOWNLOAD_TIMEOUT, LOGIN_TIMEOUT
from src.util import parse_date


PORTALS_DIR = Path(__file__).parent.parent / "portals"

# Portal-spezifische Credentials (portal_id -> (email, password))
_PORTAL_CREDENTIALS: dict[str, tuple[str | None, str | None]] = {}


def _get_portal_credentials(portal_id: str) -> tuple[str | None, str | None]:
    """Liefert (email, password) fuer ein Portal aus 1Password/env."""
    if portal_id in _PORTAL_CREDENTIALS:
        return _PORTAL_CREDENTIALS[portal_id]

    # OpenAI und ChatGPT teilen sich die gleichen Credentials
    if portal_id in ("openai-api", "chatgpt"):
        return (OPENAI_EMAIL, OPENAI_PASSWORD)

    return (None, None)


def _login_portal(page, portal_id: str, config: dict, email: str, password: str) -> bool:
    """Generischer Portal-Login: Email -> Password -> Submit.

    Navigiert zur billing_url (nicht zur homepage!) — die meisten Portale
    leiten ungeloggte User von dort direkt auf ihre Login-Seite um.
    """
    name = config.get("name", portal_id)
    homepage = config.get("homepage", "")
    billing_url = config.get("billing_url", "")
    login_url = config.get("login_url") or billing_url or homepage

    print(f"     🔑 {name} Login ...")

    if login_url:
        try:
            page.goto(login_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        except Exception:
            pass
        page.wait_for_timeout(3000)

    # Optionaler Pre-Click Button (z.B. "Log in" in der Header-Leiste)
    pre_click = config.get("login_pre_click_selector")
    if pre_click:
        try:
            btn = page.locator(pre_click)
            if btn.count() > 0:
                btn.first.click(timeout=3000)
                page.wait_for_timeout(2000)
        except Exception:
            pass

    # Email eingeben: bevorzuge input[type="email"] um OAuth-Buttons nicht zu triggern.
    # WICHTIG: Submit-Button im SELBEN FORM wie das Email-Feld finden, sonst werden
    # OAuth-Buttons (Google/Apple/Microsoft) fälschlich geklickt!
    email_input = page.locator('input[type="email"], input[name="email"], input[name="username"]')
    if email_input.count() > 0:
        try:
            email_input.first.fill(email)
            page.wait_for_timeout(500)

            # Submit-Button im SELBEN FORM finden
            form_locator = email_input.first.locator('xpath=ancestor::form[1]')
            try:
                if form_locator.count() > 0:
                    cont_btn = form_locator.locator('button[type="submit"]')
                    if cont_btn.count() > 0:
                        cont_btn.first.click()
                        page.wait_for_timeout(4000)
            except Exception:
                pass
        except Exception:
            pass

    # Passwort eingeben
    pw_input = page.locator('input[name="password"], input[type="password"]')
    auto_login_worked = False
    try:
        pw_input.first.wait_for(state="visible", timeout=8000)
        pw_input.first.fill(password)
        page.wait_for_timeout(500)

        # Submit-Button im SELBEN FORM wie das Passwort-Feld
        pw_form = pw_input.first.locator('xpath=ancestor::form[1]')
        try:
            if pw_form.count() > 0:
                submit = pw_form.locator('button[type="submit"]')
                if submit.count() > 0:
                    submit.first.click()
                    page.wait_for_timeout(5000)
                    auto_login_worked = True
        except Exception:
            pass
    except PlaywrightTimeout:
        pass

    # Auto-Login fertig? URL prüfen (NICHT _is_authenticated aufrufen — das
    # navigiert weg und würde einen laufenden 2FA/OTP-Flow zerstören).
    current_url = page.url
    is_on_login = any(k in current_url.lower() for k in ("login", "signin", "auth", "sign-in", "sign_in"))

    if is_on_login or not auto_login_worked:
        print(f"     📱 {name}: Auto-Login nicht abgeschlossen (evtl. 2FA/OTP)")
        print(f"     → Bitte manuell in Chrome Canary einloggen. Warte max. 180s ...")
        # Warte bis die URL sich NICHT mehr auf einer Login-Seite befindet
        import time as _t
        deadline = _t.time() + 180
        while _t.time() < deadline:
            _t.sleep(3)
            try:
                current_url = page.url
            except Exception:
                continue
            if not any(k in current_url.lower() for k in ("login", "signin", "auth", "sign-in", "sign_in")):
                print(f"     ✅ {name} Login erfolgreich (URL: {current_url[:60]})")
                page.wait_for_timeout(2000)
                return True
        print(f"     ❌ {name} Login Timeout")
        return False

    print(f"     ✅ {name} Login erfolgreich")
    return True


def load_portal_configs() -> list[dict]:
    """Lädt alle Portal-Konfigurationen aus portals/*.json."""
    configs = []
    if not PORTALS_DIR.exists():
        return configs
    for f in sorted(PORTALS_DIR.glob("*.json")):
        try:
            with open(f) as fh:
                config = json.load(fh)
                config["_file"] = f.name
                configs.append(config)
        except (json.JSONDecodeError, IOError) as e:
            print(f"  ⚠️  Fehler in {f.name}: {e}")
    return configs


def _match_vendor(config: dict, vendor_name: str) -> bool:
    """Prüft ob ein Portal-Config zu einem Vendor-Namen passt."""
    keywords = config.get("match_keywords", [])
    if not keywords:
        keywords = [config.get("id", ""), config.get("name", "")]
    vendor_upper = vendor_name.upper()

    # Exclude-Keywords prüfen (z.B. "CHATGPT" bei OpenAI API)
    excludes = config.get("exclude_keywords", [])
    if any(ex.upper() in vendor_upper for ex in excludes if ex):
        return False

    return any(kw.upper() in vendor_upper for kw in keywords if kw)


def _is_authenticated(page, config: dict) -> bool:
    """Prüft ob der User im Portal eingeloggt ist.

    Strenger Check: URL muss gleich bleiben (kein Redirect auf Login) UND
    der Selector muss matchen. Beides zusammen verhindert false-positives.
    """
    check_url = config.get("auth_check_url")
    check_selector = config.get("auth_check_selector")

    if not check_url:
        return True  # Kein Auth-Check konfiguriert

    try:
        page.goto(check_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(5000)

        # URL-basierter Check: wurden wir auf eine Login-Seite umgeleitet?
        url_lower = page.url.lower()
        if any(k in url_lower for k in ("login", "auth", "signin", "sign-in", "sign_in")):
            return False

        # URL-Check: sind wir noch auf der check_url Domain?
        from urllib.parse import urlparse
        expected_host = urlparse(check_url).netloc
        actual_host = urlparse(page.url).netloc
        if expected_host and actual_host and expected_host != actual_host:
            return False

        # Selector-basierter Check (falls konfiguriert)
        if check_selector:
            return page.locator(check_selector).count() > 0

        return True
    except PlaywrightTimeout:
        return False


def _extract_invoices(page, config: dict) -> list[dict]:
    """Extrahiert Invoice-Daten von der Billing-Seite."""
    invoices_config = config.get("invoices", {})
    selector = invoices_config.get("selector", "")
    fields = invoices_config.get("fields", {})

    if not selector:
        return []

    elements = page.locator(selector)
    count = elements.count()
    results = []

    for i in range(count):
        el = elements.nth(i)
        invoice = {}

        for field_name, field_config in fields.items():
            try:
                if isinstance(field_config, str):
                    invoice[field_name] = el.locator(field_config).first.text_content().strip()
                elif isinstance(field_config, dict):
                    sub_sel = field_config.get("selector", "")
                    attr = field_config.get("attribute")
                    if sub_sel == "self":
                        # "self" = das gematchte Element selbst
                        if attr:
                            invoice[field_name] = el.get_attribute(attr) or ""
                        else:
                            invoice[field_name] = (el.text_content() or "").strip()
                    else:
                        sub_el = el.locator(sub_sel).first
                        if attr:
                            invoice[field_name] = sub_el.get_attribute(attr) or ""
                        else:
                            invoice[field_name] = (sub_el.text_content() or "").strip()
            except Exception:
                invoice[field_name] = ""

        if any(invoice.values()):
            results.append(invoice)

    return results


def _parse_invoice_date(date_text: str) -> datetime | None:
    """Parst verschiedene Datumsformate — delegiert an zentrale parse_date()."""
    return parse_date(date_text)


def _parse_entry_date(date_str: str) -> datetime | None:
    """Parst MC-Entry-Datum — delegiert an zentrale parse_date()."""
    return parse_date(date_str)


def _match_invoice_to_entry(invoices: list[dict], entry: dict) -> dict | None:
    """Findet die beste Invoice für einen MC-Entry per Datum und/oder Betrag.

    Strategie:
    1. Datum-Match (Invoice-Datum innerhalb ±7 Tage des Entry-Datums)
    2. Bei mehreren Datum-Treffern: nächstes Datum gewinnt
    3. Fallback: nächste ungenutzte Invoice (wenn keine Datum-Info vorhanden)
    """
    entry_date = _parse_entry_date(entry.get("date", ""))

    # Sammle Kandidaten mit Datum-Info
    candidates = []
    no_date_invoices = []

    for inv in invoices:
        if inv.get("_used"):
            continue
        inv_date = _parse_invoice_date(inv.get("date", ""))
        if inv_date and entry_date:
            # Invoice-Datum kann VOR oder NACH dem MC-Datum liegen:
            # - Vor: Prepaid/Abo-Rechnungen
            # - Nach: Nutzungsbasierte Rechnungen (OpenAI API etc.)
            diff_days = abs((inv_date - entry_date).days)
            if diff_days <= 21:  # Innerhalb 3 Wochen
                candidates.append((diff_days, inv))
        elif not inv.get("date", "").strip():
            no_date_invoices.append(inv)

    if candidates:
        # Nächstes Datum gewinnt
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]

    # Fallback: nächste ungenutzte (nur wenn KEINE Invoice Daten hat)
    if no_date_invoices:
        return no_date_invoices[0]

    # Letzter Fallback: irgendeine ungenutzte
    for inv in invoices:
        if not inv.get("_used"):
            return inv

    return None


def _download_invoice_pdf(page, invoice: dict, config: dict, download_dir: Path, date_str: str) -> Path | None:
    """Lädt eine einzelne Invoice-PDF herunter."""
    download_config = config.get("download", {})
    method = download_config.get("method", "stripe_url")
    portal_id = config.get("id", "unknown")

    date_prefix = date_str.replace(".", "") + "_" if date_str else ""
    vendor_name = re.sub(r"[^\w]", "", config.get("name", portal_id))[:20]

    if method == "stripe_url":
        url = invoice.get("pdf_url", "")
        if not url:
            return None

        # Stripe Invoice-Seite öffnen und PDF downloaden
        try:
            invoice_page = page.context.new_page()
            try:
                invoice_page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception:
                # Timeout ist OK — Stripe lädt manchmal langsam über CDP
                pass
            invoice_page.wait_for_timeout(8000)

            # "Download invoice" Button klicken
            # Invoice bevorzugen, Receipt nur als Fallback
            dl_btn = invoice_page.locator(
                'a:has-text("Download invoice"), button:has-text("Download invoice")'
            )
            if dl_btn.count() == 0:
                dl_btn = invoice_page.locator(
                    'a:has-text("Download receipt"), button:has-text("Download receipt")'
                )
            if dl_btn.count() > 0:
                with invoice_page.expect_download(timeout=DOWNLOAD_TIMEOUT) as dl_info:
                    dl_btn.first.click()
                download = dl_info.value
                fname = download.suggested_filename or f"{vendor_name}_invoice.pdf"
                save_path = download_dir / f"{date_prefix}{fname}"
                download.save_as(str(save_path))
                invoice_page.close()
                return save_path

            invoice_page.close()
        except Exception as e:
            print(f"       ⚠️  Stripe-Download fehlgeschlagen: {e}")
        return None

    elif method == "direct_link":
        url = invoice.get("pdf_url", "")
        if not url:
            return None
        try:
            # Cookies aus Browser übernehmen
            homepage = config.get("homepage", "")
            cookies = page.context.cookies(homepage) if homepage else []
            cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

            import requests as http_req
            resp = http_req.get(url, headers={"Cookie": cookie_str}, timeout=30)
            if resp.status_code == 200 and len(resp.content) > 1000:
                fname = f"{date_prefix}{vendor_name}_invoice.pdf"
                save_path = download_dir / fname
                save_path.write_bytes(resp.content)
                return save_path
        except Exception as e:
            print(f"       ⚠️  Direct-Download fehlgeschlagen: {e}")
        return None

    elif method == "click_button":
        selector = download_config.get("selector", "")
        if not selector:
            return None
        try:
            btn = page.locator(selector)
            if btn.count() > 0:
                with page.expect_download(timeout=DOWNLOAD_TIMEOUT) as dl_info:
                    btn.first.click()
                download = dl_info.value
                fname = download.suggested_filename or f"{vendor_name}_invoice.pdf"
                save_path = download_dir / f"{date_prefix}{fname}"
                download.save_as(str(save_path))
                return save_path
        except Exception as e:
            print(f"       ⚠️  Click-Download fehlgeschlagen: {e}")
        return None

    elif method == "print_page":
        try:
            fname = f"{date_prefix}{vendor_name}_invoice.pdf"
            save_path = download_dir / fname
            page.pdf(path=str(save_path), format="A4", print_background=True)
            if save_path.stat().st_size > 1000:
                return save_path
            save_path.unlink(missing_ok=True)
        except Exception as e:
            print(f"       ⚠️  Print-PDF fehlgeschlagen: {e}")
        return None

    return None


def download_portal_invoices(
    page,
    entries: list[dict],
    download_dir: Path,
) -> list[tuple[dict, Path, str]]:
    """
    Versucht für ungematchte MC-Einträge Rechnungen von Vendor-Portalen
    herunterzuladen. Nutzt JSON-Konfigurationen aus portals/*.json.

    Returns:
        Liste von (entry, filepath, portal_id) Tupeln.
    """
    configs = load_portal_configs()
    if not configs:
        return []

    download_dir.mkdir(parents=True, exist_ok=True)
    results = []

    # Nur Belastungen ohne is_credit
    debits = [e for e in entries if not e.get("is_credit")]
    if not debits:
        return []

    # Welche Vendor haben ein Portal-Config?
    matched_configs = {}
    for entry in debits:
        vendor = entry.get("vendor", "")
        for config in configs:
            if _match_vendor(config, vendor):
                portal_id = config["id"]
                if portal_id not in matched_configs:
                    matched_configs[portal_id] = {"config": config, "entries": []}
                matched_configs[portal_id]["entries"].append(entry)
                break

    if not matched_configs:
        return []

    print(f"\n  🔍 Portal-Download: {len(matched_configs)} Vendor mit Config ...")

    for portal_id, data in matched_configs.items():
        config = data["config"]
        portal_entries = data["entries"]
        name = config.get("name", portal_id)

        print(f"\n  {name} ({len(portal_entries)} Eintraege)")

        # Auth prüfen
        if not _is_authenticated(page, config):
            cred_email, cred_pw = _get_portal_credentials(portal_id)
            if cred_email and cred_pw:
                if not _login_portal(page, portal_id, config, cred_email, cred_pw):
                    continue
                # Nach Login erneut pruefen
                if not _is_authenticated(page, config):
                    print(f"     ⚠️ {name}: Login scheinbar erfolgreich, aber Auth-Check schlaegt fehl")
                    continue
            else:
                print(f"     ⚠️ Nicht eingeloggt bei {name}")
                print(f"     → Credentials in 1Password konfigurieren oder in Chrome Canary einloggen")
                continue

        # Billing-Seite laden
        billing_url = config.get("billing_url")
        if not billing_url:
            continue

        page.goto(billing_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        # Warten bis Invoice-Links sichtbar sind (SPA-Content)
        invoices_config = config.get("invoices", {})
        selector = invoices_config.get("selector", "")
        if selector:
            try:
                page.wait_for_selector(selector, timeout=PAGE_TIMEOUT)
                page.wait_for_timeout(2000)
            except Exception:
                page.wait_for_timeout(10000)
        else:
            page.wait_for_timeout(10000)

        # Invoices extrahieren
        invoices = _extract_invoices(page, config)
        print(f"     {len(invoices)} Invoice(s) auf der Seite")

        for entry in portal_entries:
            amount = entry.get("amount", 0)
            date_str = entry.get("date", "")
            vendor = entry.get("vendor", "?")
            print(f"     {vendor}  {amount:.2f} EUR  ({date_str})")

            matched_invoice = _match_invoice_to_entry(invoices, entry)

            if matched_invoice:
                path = _download_invoice_pdf(page, matched_invoice, config, download_dir, date_str)
                if path:
                    matched_invoice["_used"] = True
                    results.append((entry, path, portal_id))
                    print(f"     📎 {path.name} ({path.stat().st_size / 1024:.1f} KB)")
                else:
                    print(f"     ❌ Download fehlgeschlagen")
            else:
                print(f"     ⚠️ Keine passende Invoice gefunden")

        time.sleep(1)

    if results:
        print(f"\n  ✅ {len(results)} Portal-Rechnung(en) heruntergeladen")

    return results
