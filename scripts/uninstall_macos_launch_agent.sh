#!/usr/bin/env bash
set -euo pipefail

LABEL="com.tradebot.backend"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
UID_NUM="$(id -u)"

if launchctl print "gui/$UID_NUM/$LABEL" >/dev/null 2>&1; then
  launchctl bootout "gui/$UID_NUM/$LABEL" >/dev/null 2>&1 || true
fi

rm -f "$PLIST"
echo "Uninstalled $LABEL"

