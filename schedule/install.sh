#!/bin/sh
# Install (or reinstall) the scan job: every 2 hours. Log: data/run.log
set -e
cd "$(dirname "$0")"
DEST="$HOME/Library/LaunchAgents/com.mevwatch.scan.plist"
launchctl bootout "gui/$(id -u)" "$DEST" 2>/dev/null || true
cp com.mevwatch.scan.plist "$DEST"
launchctl bootstrap "gui/$(id -u)" "$DEST"
echo "Installed: MEV Watch scan every 2 hours. Log: data/run.log"
