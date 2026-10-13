#!/bin/sh
# Builds, signs, installs and launches ARStream on the USB-connected iPhone.
# Needs the signing Team set once in Xcode (Signing & Capabilities).
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/ARStream"
BUILD_DIR="$HERE/../build/ios-signed"

DEVICE=$(xcrun devicectl list devices 2>/dev/null | awk '/(available|connected)/ && /iPhone/ {for (i = 1; i <= NF; i++) if ($i ~ /^[0-9A-F]{8}-[0-9A-F]{16}$/) print $i}' | head -1)
[ -n "$DEVICE" ] || { echo "No iPhone available (plugged in and unlocked?)"; exit 1; }

# Built with the scheme for the connected device so its UDID is registered with the Team and the provisioning
# profile regenerated (a -target build ignores the destination, reuses the profile of an earlier phone and the install
# fails with "this provisioning profile cannot be installed on this device")
xcodebuild -project ARStream.xcodeproj -scheme ARStream -sdk iphoneos -configuration Debug \
    -destination "id=$DEVICE" -allowProvisioningUpdates -allowProvisioningDeviceRegistration \
    -quiet build SYMROOT="$BUILD_DIR"

xcrun devicectl device install app --device "$DEVICE" "$BUILD_DIR/Debug-iphoneos/ARStream.app"
sh "$HERE/launch.sh"
