#!/bin/bash
# Setup-Skript für den bahn.de Rechnungs-Bot
# Erstellt ein venv und installiert alle Abhängigkeiten

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "================================================"
echo "  bahn.de Rechnungs-Bot – Setup"
echo "================================================"
echo ""

# venv erstellen
echo "1) Erstelle Python venv ..."
python3 -m venv .venv
echo "   OK: .venv erstellt"

# Aktivieren
source .venv/bin/activate

# Dependencies installieren
echo ""
echo "2) Installiere Abhängigkeiten ..."
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "   OK: Pakete installiert"

# Playwright-Browser
echo ""
echo "3) Installiere Chromium für Playwright ..."
playwright install chromium
echo "   OK: Chromium installiert"

# .env anlegen falls nicht vorhanden
echo ""
if [ ! -f .env ]; then
    cp .env.template .env
    echo "4) .env aus Template erstellt"
    echo "   WICHTIG: Bitte .env jetzt mit deinen Daten ausfüllen:"
    echo "   $SCRIPT_DIR/.env"
else
    echo "4) .env existiert bereits – überspringe"
fi

echo ""
echo "================================================"
echo "  Setup abgeschlossen!"
echo ""
echo "  Nächste Schritte:"
echo "    1. Azure App Registration erstellen (siehe README)"
echo "    2. .env ausfüllen"
echo "    3. ./run.sh --mc-pdf <pdf> --headed --dry-run"
echo "================================================"
