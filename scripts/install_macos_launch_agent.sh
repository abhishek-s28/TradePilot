#!/usr/bin/env bash
set -euo pipefail

LABEL="com.tradebot.backend"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
VENV="${TRADEBOT_SERVICE_VENV:-$BACKEND/.service-venv}"
UVICORN="$VENV/bin/uvicorn"
SITE_PACKAGES="$VENV/lib/python3.11/site-packages"
if [ -z "${PYTHON_BIN:-}" ]; then
  if command -v python3.11 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3.11)"
  elif [ -x "/usr/local/opt/python@3.11/bin/python3.11" ]; then
    PYTHON_BIN="/usr/local/opt/python@3.11/bin/python3.11"
  elif [ -x "/opt/homebrew/opt/python@3.11/bin/python3.11" ]; then
    PYTHON_BIN="/opt/homebrew/opt/python@3.11/bin/python3.11"
  else
    PYTHON_BIN="python3"
  fi
fi
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
# Logs must live outside the project directory (macOS LaunchAgent security
# blocks writes to paths under ~/Downloads).
LOG_DIR="$HOME/Library/Logs/tradebot"
UID_NUM="$(id -u)"

mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"

if [ ! -x "$UVICORN" ]; then
  echo "Backend virtualenv or uvicorn not found. Installing backend dependencies..."
  rm -rf "$VENV"
  "$PYTHON_BIN" -m venv "$VENV"
  "$VENV/bin/pip" install --upgrade pip
  "$VENV/bin/pip" install -e "$BACKEND[dev]"
fi

LAUNCH_SCRIPT="$ROOT/scripts/run_backend.sh"
chmod +x "$LAUNCH_SCRIPT"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$LAUNCH_SCRIPT</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/backend.launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/backend.launchd.err.log</string>
</dict>
</plist>
PLIST

if launchctl print "gui/$UID_NUM/$LABEL" >/dev/null 2>&1; then
  launchctl bootout "gui/$UID_NUM/$LABEL" >/dev/null 2>&1 || true
fi

launchctl bootstrap "gui/$UID_NUM" "$PLIST"
launchctl enable "gui/$UID_NUM/$LABEL"
launchctl kickstart -k "gui/$UID_NUM/$LABEL"

echo "Installed and started $LABEL"
echo "API:  http://127.0.0.1:8000"
echo "Logs: $LOG_DIR/backend.out.log"
