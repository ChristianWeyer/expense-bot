#!/bin/bash
# Startet den Expense-Bot im venv
#
# Wenn CDP_URL und MC_PDF in .env gesetzt sind, reicht einfach:
#   ./run.sh                                    # Alles aus .env
#   ./run.sh --dry-run                          # Nur testen, nicht senden
#
# Oder mit expliziten Parametern:
#   ./run.sh --mc-pdf abrechnung.pdf            # MC-PDF → DB + Outlook + Amazon
#   ./run.sh --mc-pdf ~/Downloads/              # Neuestes PDF im Ordner
#   ./run.sh --cdp --mc-pdf abrechnung.pdf      # CDP + PDF
#   ./run.sh --cc chef@firma.de                 # Mit CC
#   ./run.sh --headed                           # Browser sichtbar (Debugging)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -d .venv ]; then
    echo "Fehler: venv nicht gefunden. Bitte zuerst ./setup.sh ausführen."
    exit 1
fi

if [ ! -f .env ]; then
    echo "Fehler: .env nicht gefunden. Bitte .env.template kopieren und ausfüllen."
    exit 1
fi

# Kurze Verzögerung wenn per launchd/WatchPaths getriggert,
# damit die Datei vollständig geschrieben ist.
sleep 2

source .venv/bin/activate
python expense_bot.py "$@"
