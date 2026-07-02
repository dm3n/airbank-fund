#!/bin/bash
# Deliberate install step (contract assertion 26) — the loop never self-installs.
set -euo pipefail
PLIST_SRC="$(dirname "$0")/com.airbank.loop.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.airbank.loop.plist"
cp "$PLIST_SRC" "$PLIST_DST"
launchctl unload "$PLIST_DST" 2>/dev/null || true
launchctl load "$PLIST_DST"
echo "Airbank loop installed: every 15 min, 24/7. Uninstall: launchctl unload $PLIST_DST"
