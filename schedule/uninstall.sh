#!/bin/sh
DEST="$HOME/Library/LaunchAgents/com.mevwatch.scan.plist"
launchctl bootout "gui/$(id -u)" "$DEST" 2>/dev/null || true
rm -f "$DEST"
echo "Removed the MEV Watch schedule."
