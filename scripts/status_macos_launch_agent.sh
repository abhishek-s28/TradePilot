#!/usr/bin/env bash
set -euo pipefail

LABEL="com.tradebot.backend"
UID_NUM="$(id -u)"

launchctl print "gui/$UID_NUM/$LABEL"
