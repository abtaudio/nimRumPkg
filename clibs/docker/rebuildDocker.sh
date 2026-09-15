#!/usr/bin/env bash
# Rebuild thin Docker images for nimRumPkg C library cross-compilation
# Requires base images: ghcr.io/abtaudio/nimrum-cc-arm64, ghcr.io/abtaudio/nimrum-cc-armv7
# These are pulled automatically from ghcr.io on first build.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

docker build --platform linux/arm64  -t nimrumpkg-arm64 -f "$SCRIPT_DIR/arm64.Dockerfile" "$PKG_DIR"
docker build --platform linux/arm/v7 -t nimrumpkg-armv7 -f "$SCRIPT_DIR/armv7.Dockerfile" "$PKG_DIR"
