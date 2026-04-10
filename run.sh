#!/bin/bash
# ════════════════════════════════════════════════════════════
# Expense Bot — One-Stop Run Script
# ════════════════════════════════════════════════════════════
#
# Nutzung:
#   ./run.sh                        # Auto: neuestes MC-PDF, CDP, Email
#   ./run.sh --dry-run              # Ohne Email-Versand
#   ./run.sh --mc-pdf datei.pdf     # Bestimmtes PDF
#   ./run.sh --headed               # Browser sichtbar
#   ./run.sh --marked-entries-only  # Nur gelbe Einträge
#
# Was passiert:
#   1. Prüft/startet Chrome Canary mit CDP
#   2. Findet neuestes MC-PDF (oder nutzt --mc-pdf Argument)
#   3. Startet Expense Bot mit allen Live-Scrapern
#   4. Schreibt Log-Datei nach logs/

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# --- Voraussetzungen ---
if [ ! -d .venv ]; then
    echo "FEHLER: .venv nicht gefunden. Bitte zuerst: python -m venv .venv && pip install -r requirements.txt"
    exit 1
fi
if [ ! -f .env ]; then
    echo "FEHLER: .env nicht gefunden. Bitte .env.template kopieren und ausfüllen."
    exit 1
fi

source .venv/bin/activate

# --- Log-Datei wird von Python (src/logger.py) erstellt ---

# --- Chrome Canary CDP ---
CDP_URL="${CDP_URL:-http://localhost:9222}"

echo "╔══════════════════════════════════════╗"
echo "║         Expense Bot Run              ║"
echo "╚══════════════════════════════════════╝"
echo ""

echo "Chrome Canary CDP ($CDP_URL) ..."
if curl -s --max-time 3 "$CDP_URL/json/version" > /dev/null 2>&1; then
    echo "  ✓ CDP erreichbar"
else
    echo "  Chrome Canary starten ..."
    if [ -d "/Applications/Google Chrome Canary.app" ]; then
        # Persistentes Profil für den Bot — bewahrt alle Login-Sessions
        # (nicht das Default-Profil, da Chrome nur eine Instanz pro Profil erlaubt)
        CHROME_PROFILE="$HOME/Library/Application Support/Google/Chrome Canary ExpenseBot"
        "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary" \
            --remote-debugging-port=9222 \
            --user-data-dir="$CHROME_PROFILE" \
            --no-first-run > /dev/null 2>&1 &
        sleep 2
        CDP_READY=false
        for i in 1 2 3; do
            if curl -s --max-time 3 "$CDP_URL/json/version" > /dev/null 2>&1; then
                CDP_READY=true
                echo "  ✓ Chrome Canary gestartet (Versuch $i)"
                break
            fi
            echo "  … CDP noch nicht bereit (Versuch $i/3)"
            sleep 2
        done
        if [ "$CDP_READY" = false ]; then
            echo "  ✗ Chrome Canary konnte nicht gestartet werden (3 Versuche)"
            exit 1
        fi
    else
        echo "  ✗ Chrome Canary nicht installiert"
        echo "    Bitte manuell starten: Google Chrome Canary --remote-debugging-port=9222"
        exit 1
    fi
fi

# --- MC-PDF finden ---
MC_PDF_ARG=""
HAS_MC_PDF=false
for arg in "$@"; do
    if [ "$HAS_MC_PDF" = true ]; then
        MC_PDF_ARG="$arg"
        break
    fi
    if [ "$arg" = "--mc-pdf" ]; then
        HAS_MC_PDF=true
    fi
done

if [ -z "$MC_PDF_ARG" ]; then
    # Neuestes PDF im beispiel-pdfs/ Ordner
    MC_PDF_ARG=$(ls -t beispiel-pdfs/*Abrechnung*.PDF beispiel-pdfs/*Abrechnung*.pdf 2>/dev/null | head -1 || true)
    if [ -z "$MC_PDF_ARG" ]; then
        MC_PDF_ARG=$(ls -t beispiel-pdfs/*.PDF beispiel-pdfs/*.pdf 2>/dev/null | head -1 || true)
    fi
    if [ -z "$MC_PDF_ARG" ]; then
        echo "FEHLER: Kein MC-PDF gefunden in beispiel-pdfs/"
        echo "  Bitte PDF ablegen oder --mc-pdf angeben"
        exit 1
    fi
fi

echo "MC-PDF: $MC_PDF_ARG"
echo ""

# --- Unbuffered Output für Live-Updates ---
export PYTHONUNBUFFERED=1

# --- Run ---
# Kurze Verzögerung falls per launchd/WatchPaths getriggert
sleep 1

# Baue Argumente: füge --cdp und --mc-pdf hinzu falls nicht schon gesetzt
ARGS=("$@")
if ! echo "$*" | grep -q -- '--cdp'; then
    ARGS+=(--cdp "$CDP_URL")
fi
if ! echo "$*" | grep -q -- '--mc-pdf'; then
    ARGS+=(--mc-pdf "$MC_PDF_ARG")
fi

python expense_bot.py "${ARGS[@]}"
