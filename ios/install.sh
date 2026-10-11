#!/bin/sh
# Builds, signs, installs and launches ARStream on the USB-connected iPhone.
# Needs the signing Team set once in Xcode (Signing & Capabilities).
set -e
cd "$(dirname "$0")/ARStream"
BUILD_DIR="$(pwd)/../../build/ios-signed"

xcodebuild -project ARStream.xcodeproj -target ARStream -sdk iphoneos -configuration Debug \
    -allowProvisioningUpdates -quiet build SYMROOT="$BUILD_DIR"

DEVICE=$(xcrun devicectl list devices 2>/dev/null | awk '/(available|connected)/ && /iPhone/ {for (i = 1; i <= NF; i++) if ($i ~ /^[0-9A-F]{8}-[0-9A-F]{16}$/) print $i}' | head -1)
[ -n "$DEVICE" ] || { echo "No iPhone available (plugged in and unlocked?)"; exit 1; }

xcrun devicectl device install app --device "$DEVICE" "$BUILD_DIR/Debug-iphoneos/ARStream.app"
BUNDLE_ID=$(/usr/libexec/PlistBuddy -c "Print CFBundleIdentifier" "$BUILD_DIR/Debug-iphoneos/ARStream.app/Info.plist")
xcrun devicectl device process launch --terminate-existing --device "$DEVICE" "$BUNDLE_ID"
