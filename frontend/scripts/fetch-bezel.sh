#!/bin/sh
# Downloads Apple's iPhone 17 bezels (Apple Design Resources) and copies the 17 Pro portrait PNGs
# into public/bezels/. Running this accepts Apple's Design Resources license; the PNGs stay gitignored.
set -e
cd "$(dirname "$0")/.."
DMG="${TMPDIR:-/tmp}/Bezel-iPhone-17.dmg"
[ -f "$DMG" ] || curl -L -o "$DMG" https://devimages-cdn.apple.com/design/resources/download/Bezel-iPhone-17.dmg
MNT=$(yes | hdiutil attach -nobrowse -readonly "$DMG" | awk -F'\t' '/\/Volumes\//{print $NF}')
mkdir -p public/bezels
SRC=$(find "$MNT" -name "iPhone 17 Pro - Silver - Portrait.png" | head -1)
cp "$SRC" public/bezels/iphone-17-pro-silver.png
cp "$(dirname "$SRC")/iPhone 17 Pro - Deep Blue - Portrait.png" public/bezels/iphone-17-pro-deep-blue.png
hdiutil detach "$MNT" >/dev/null
echo "bezels in public/bezels/"
