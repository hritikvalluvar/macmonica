#!/bin/bash
set -e

PLIST_SRC="$(cd "$(dirname "$0")" && pwd)/com.macmonica.agent.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.macmonica.agent.plist"
MACMONICA_DIR="$HOME/.macmonica"

echo "Setting up macmonica..."

# Create data directory
mkdir -p "$MACMONICA_DIR"

# Unload existing agent if present
if launchctl list | grep -q com.macmonica.agent; then
    echo "Stopping existing agent..."
    launchctl unload "$PLIST_DST" 2>/dev/null || true
fi

# Copy and load plist
cp "$PLIST_SRC" "$PLIST_DST"
launchctl load "$PLIST_DST"

echo "Done! Collector is now running in the background."
echo ""
echo "Commands:"
echo "  macmonica              # Live dashboard"
echo "  macmonica history      # View trends"
echo "  macmonica status       # Check collector"
echo "  macmonica alerts       # View alerts"
echo "  macmonica recommend    # Get recommendations"
echo ""
echo "To stop: launchctl unload ~/Library/LaunchAgents/com.macmonica.agent.plist"
