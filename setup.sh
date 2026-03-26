#!/bin/bash
set -e

PLIST_SRC="$(cd "$(dirname "$0")" && pwd)/com.sysmon.agent.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.sysmon.agent.plist"
SYSMON_DIR="$HOME/.sysmon"

echo "Setting up sysmon..."

# Create data directory
mkdir -p "$SYSMON_DIR"

# Unload existing agent if present
if launchctl list | grep -q com.sysmon.agent; then
    echo "Stopping existing agent..."
    launchctl unload "$PLIST_DST" 2>/dev/null || true
fi

# Copy and load plist
cp "$PLIST_SRC" "$PLIST_DST"
launchctl load "$PLIST_DST"

echo "Done! Collector is now running in the background."
echo ""
echo "Commands:"
echo "  python3 -m sysmon              # Live dashboard"
echo "  python3 -m sysmon history      # View trends"
echo "  python3 -m sysmon status       # Check collector"
echo "  python3 -m sysmon alerts       # View alerts"
echo "  python3 -m sysmon recommend    # Get recommendations"
echo ""
echo "To stop: launchctl unload ~/Library/LaunchAgents/com.sysmon.agent.plist"
