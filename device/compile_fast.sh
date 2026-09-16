#!/bin/bash
# Builds the device binary natively with the host Go toolchain and the local
# Android NDK clang — no Docker, no emulation.
#
# compile.sh's pinned compiler image is published amd64-only, so on an arm64
# host Docker runs the whole toolchain under emulation. This script is for
# iteration; compile.sh remains the release path, because its digest pin is
# deliberate.
set -euo pipefail

SDK=${ANDROID_SDK:-/opt/homebrew/share/android-commandlinetools}
NDK=${ANDROID_NDK:-$(ls -d "$SDK"/ndk/* | sort -V | tail -1)}
PREBUILT=$(ls -d "$NDK"/toolchains/llvm/prebuilt/* | head -1)
API=22   # FireOS 5 = Android 5.1

HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"

export CC="$PREBUILT/bin/armv7a-linux-androideabi${API}-clang"
export CXX="$PREBUILT/bin/armv7a-linux-androideabi${API}-clang++"
export CGO_ENABLED=1
export GOOS=android
export GOARCH=arm
export GOARM=7
# Android 5.1's linker needs the SysV hash section, which modern lld omits.
export CGO_LDFLAGS="-Wl,--hash-style=both"
# Known-harmless warnings in the vendored C; same set compile.sh uses.
export CGO_CFLAGS="-Wno-deprecated-declarations -Wno-null-dereference"

# tinyalsa. The compiler image builds it for API 22 into its own NDK sysroot, so
# a native build has to supply it. The source is not in this repo — it lives at
# /tinyalsa inside the pinned base image — so it is extracted from that same
# image once and cached in device/.tinyalsa (gitignored), then cross-compiled
# with the NDK clang above.
#
# Taking it from the pinned image rather than from tinyalsa upstream is
# deliberate: it is the identical source the release path compiles, so a binary
# built here and a binary built by compile.sh differ only in the toolchain, not
# in what they link against. Override with TINYALSA=/path (needs include/ and
# lib/libtinyalsa.so).
IMAGE_DIGEST=$(awk -F'FROM ' '/^FROM /{print $2}' "$HERE/compiler/Dockerfile")
TINYALSA=${TINYALSA:-$HERE/.tinyalsa}

if [ ! -f "$TINYALSA/lib/libtinyalsa.so" ]; then
  echo "tinyalsa not built at $TINYALSA — bootstrapping from the pinned image"
  command -v docker >/dev/null || {
    echo "error: need docker once to extract tinyalsa from $IMAGE_DIGEST," >&2
    echo "       or set TINYALSA=/path to a prebuilt armv7a sysroot." >&2
    exit 1
  }
  SRC="$TINYALSA/src"
  if [ ! -f "$SRC/Makefile" ]; then
    mkdir -p "$TINYALSA"
    CID=$(docker create "$IMAGE_DIGEST" /bin/true)
    trap 'docker rm -f "$CID" >/dev/null 2>&1 || true' EXIT
    docker cp "$CID:/tinyalsa/." "$TINYALSA/" >/dev/null
    docker rm -f "$CID" >/dev/null; trap - EXIT
  fi
  # Cross-compile for the device; the image's own build targets Linux x86.
  #
  # The shared library is linked by hand rather than by tinyalsa's own
  # `libtinyalsa.so` target, whose recipe passes bare `-soname=` — a flag clang
  # rejects (it needs -Wl,-soname=). Only the .a target is driven by make.
  #
  # This library exists ONLY to satisfy the linker: the device provides the real
  # one at /system/lib/libtinyalsa.so, and the firmware resolves against that at
  # runtime.
  make -C "$SRC" clean >/dev/null 2>&1 || true
  make -C "$SRC" CC="$CC" AR="$PREBUILT/bin/llvm-ar" libtinyalsa.a >/dev/null
  "$CC" -shared -Wl,-soname=libtinyalsa.so -o "$SRC/libtinyalsa.so" "$SRC"/*.o
  mkdir -p "$TINYALSA/lib"
  cp "$SRC"/libtinyalsa.a "$SRC"/libtinyalsa.so "$TINYALSA/lib/"
  echo "built tinyalsa for armv7a in $TINYALSA/lib"
fi

[ -d "$TINYALSA/include" ] || { echo "no tinyalsa headers at $TINYALSA/include" >&2; exit 1; }
export CGO_CFLAGS="$CGO_CFLAGS -I$TINYALSA/include"
export CGO_LDFLAGS="$CGO_LDFLAGS -L$TINYALSA/lib -ltinyalsa"

[ -x "$CC" ] || { echo "no NDK clang at $CC"; exit 1; }

REPO_ROOT=$(git rev-parse --show-toplevel)
GIT_VERSION=$(git -C "$REPO_ROOT" describe --tags --match 'v*' --always --dirty 2>/dev/null || echo unknown)
case "$GIT_VERSION" in
  *dirty*|unknown) VERSION="$(date +%Y%m%d-%H%M)-dev" ;;
  *)               VERSION="$GIT_VERSION" ;;
esac

echo "NDK      $(basename "$NDK") ($(basename "$PREBUILT"))"
echo "go       $(go version | awk '{print $3, $4}')"
echo "building EchoMuse $VERSION for android/arm (API $API)..."

mkdir -p build
time go build -tags server \
  -ldflags "-X github.com/wilbowes/EchoMuse/internal/client.Version=${VERSION} \
            -X github.com/wilbowes/EchoMuse/internal/client.BuildUnix=$(date +%s)" \
  -o build/server ./cmd/

echo ""
echo "✓ build/server  ($VERSION)"
file build/server
