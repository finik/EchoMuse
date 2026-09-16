#!/bin/bash
# Build rook_panel.apk with no Gradle — aapt2 -> javac -> d8 -> zipalign -> apksigner.
#
# Gradle would dwarf what this builds (one manifest, one Java file); the
# crown_launcher precedent in this project does the same for the same reason.
# Override the SDK location with ANDROID_SDK.
set -euo pipefail

SDK=${ANDROID_SDK:-/opt/homebrew/share/android-commandlinetools}
BT=$(ls -d "$SDK"/build-tools/* | sort -V | tail -1)
# Compile against a modern platform but TARGET api 22: Fire OS 5 is Android 5.1,
# and anything newer compiles happily then fails at runtime on the device.
PLATFORM=$(ls -d "$SDK"/platforms/android-* | sort -V | tail -1)
MIN_API=22

HERE=$(cd "$(dirname "$0")" && pwd)
OUT="$HERE/build"
rm -rf "$OUT"; mkdir -p "$OUT/classes"

echo "SDK      $SDK"
echo "tools    $(basename "$BT")"
echo "platform $(basename "$PLATFORM")  (minSdk/target $MIN_API)"

# 1) Resources + manifest -> a base APK. No res/ dir at all: everything is
#    drawn programmatically, so there is nothing to compile but the manifest.
"$BT/aapt2" link \
  --manifest "$HERE/AndroidManifest.xml" \
  -I "$PLATFORM/android.jar" \
  --min-sdk-version "$MIN_API" \
  --target-sdk-version "$MIN_API" \
  --java "$OUT" \
  -o "$OUT/base.apk"

# 2) Java -> class files. --release 8 because d8 wants Java 8-compatible
#    bytecode and API 22 has no newer language support anyway.
find "$HERE/src" -name '*.java' > "$OUT/sources.txt"
javac --release 8 -nowarn \
  -classpath "$PLATFORM/android.jar" \
  -d "$OUT/classes" \
  @"$OUT/sources.txt"

# 3) class files -> dex, floored at the device's API level.
# --lib: without the platform jar d8 cannot see android.* supertypes and
# warns it may not be able to desugar correctly.
"$BT/d8" --min-api "$MIN_API" --lib "$PLATFORM/android.jar" --output "$OUT" \
  $(find "$OUT/classes" -name '*.class')

# 4) dex into the APK, align, sign with a throwaway debug key.
cd "$OUT"
cp base.apk unsigned.apk
zip -q unsigned.apk classes.dex

KEY="$HERE/debug.keystore"
if [ ! -f "$KEY" ]; then
  keytool -genkeypair -keystore "$KEY" -storepass android -keypass android \
    -alias rookpanel -keyalg RSA -keysize 2048 -validity 10000 \
    -dname "CN=rookpanel" >/dev/null 2>&1
  echo "generated $KEY"
fi

"$BT/zipalign" -f 4 unsigned.apk aligned.apk
"$BT/apksigner" sign \
  --ks "$KEY" --ks-pass pass:android --key-pass pass:android \
  --min-sdk-version "$MIN_API" \
  --out "$HERE/rook_panel.apk" aligned.apk

echo ""
echo "✓ $HERE/rook_panel.apk"
"$BT/apksigner" verify --print-certs "$HERE/rook_panel.apk" | head -2
