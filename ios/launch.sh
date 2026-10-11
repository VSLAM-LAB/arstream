#!/bin/sh
# Brings ARStream to the foreground on the USB-connected iPhone, relaunching it if it is already running: iOS only
# keeps the app's TCP listener alive while it is in the foreground, so a backgrounded app refuses connections.
# Needs the app installed (ios/install.sh) and the phone plugged in and unlocked.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"

DEVICE=$(xcrun devicectl list devices 2>/dev/null | awk '/(available|connected)/ && /iPhone/ {for (i = 1; i <= NF; i++) if ($i ~ /^[0-9A-F]{8}-[0-9A-F]{16}$/) print $i}' | head -1)
[ -n "$DEVICE" ] || { echo "No iPhone available (plugged in and unlocked?)"; exit 1; }

# The bundle id of the Xcode project (README: change it there if the default one is taken).
BUNDLE_ID=$(sed -n 's/.*PRODUCT_BUNDLE_IDENTIFIER = \([^;]*\);.*/\1/p' "$HERE/ARStream/ARStream.xcodeproj/project.pbxproj" | head -1)
[ -n "$BUNDLE_ID" ] || { echo "No PRODUCT_BUNDLE_IDENTIFIER in the Xcode project"; exit 1; }

xcrun devicectl device process launch --terminate-existing --device "$DEVICE" "$BUNDLE_ID"
