#!/usr/bin/env bash
# build.sh — Build all C libraries (audio_source + dsp) for arm64 and armv7
#
# Usage:
#   ./build.sh          Build both libs, both architectures
#   ./build.sh -c       Clean rebuild
#
# Prerequisites:
#   - Docker images: nimrumpkg-arm64, nimrumpkg-armv7
#     (run docker/rebuildDocker.sh to create them)

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PKG_DIR"

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; NC='\033[0m'
ok() { echo -e "${GREEN}✓${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; }
step() { echo -e "${CYAN}►${NC} $1"; }

# Parse options
doClean=0
while getopts "c" opt; do
    case $opt in
        c) doClean=1 ;;
        *) echo "Usage: $0 [-c]"; exit 1 ;;
    esac
done

if [ $doClean -eq 1 ]; then
    rm -rf build/ RESULT/
    ok "Cleaned build/ RESULT/"
fi

#### DOCKER SETUP ####
myUid=$(id -u "$USER")
myGid=$(id -g "$USER")
dockerUser="-u ${myUid}:${myGid}"
dockerMount="--mount type=bind,src=${PKG_DIR},dst=/home/nimble/nimRumPkg/"
cmakeToolchain="/home/nimble/cmake/CMake_toolchain_DOCKER.cmake"

#### BUILD FUNCTION ####
build_lib() {
    local lib_name="$1"
    local cmake_src="$2"
    local output_so="$3"

    for ARCH in aarch64 armv7l; do
        local build_dir="build/${lib_name}_${ARCH}"
        local docker_image
        local platform
        if [ "$ARCH" = "aarch64" ]; then
            docker_image="nimrumpkg-arm64"
            platform="linux/arm64"
        else
            docker_image="nimrumpkg-armv7"
            platform="linux/arm/v7"
        fi

        step "Docker build: ${docker_image} (${lib_name} ${ARCH})"
        # Re-configure when VERSION is newer than the build directory, not only when the
        # directory is missing.
        #
        # CMake reads VERSION at CONFIGURE time and bakes the parts in as -D defines, so
        # skipping configure freezes them. That hid a real fault for three weeks: the
        # AudioSource wire version was derived from the major, our build dirs were
        # configured while it was 1, and the value stayed 1 through the whole 1.x -> 2.7.0
        # history. Only a fresh clone would have compiled anything different — i.e. the
        # bug was invisible here and waiting for the first public recipient. The wire
        # version is pinned now, but the display fields still come from VERSION and the
        # same staleness would apply to anything added later.
        local buildCmd="cd /home/nimble/nimRumPkg && \
            if [ ! -d ${build_dir} ] || [ VERSION -nt ${build_dir}/CMakeCache.txt ]; then \
                cmake -G Ninja -DCMAKE_TOOLCHAIN_FILE=${cmakeToolchain} -DTARGET_ARCH=${ARCH} \
                    -B${build_dir} -S${cmake_src} -DCMAKE_BUILD_TYPE=release; \
            fi && \
            ninja -C ${build_dir} install"

        if ! docker run --platform "${platform}" ${dockerUser} ${dockerMount} --rm "${docker_image}" "${buildCmd}"; then
            fail "${lib_name} ${ARCH} build failed!"
            exit 1
        fi
        ok "${lib_name} ${ARCH} build complete"

        # Copy result to clibs/out.
        #
        # mkdir -p, because a fresh clone has no clibs/out: the directory is a build
        # artefact and is gitignored, so it exists in a tree that has built before and
        # nowhere else. Without this the first build in a new checkout fails with
        # "cp: cannot create regular file 'clibs/out/<arch>/'" after the compile has
        # already succeeded - found 2026-09-15 by building an exported public tree.
        mkdir -p "clibs/out/${ARCH}"
        if [ -f "RESULT/${ARCH}/nimRum/${output_so}" ]; then
            cp "RESULT/${ARCH}/nimRum/${output_so}" "clibs/out/${ARCH}/"
            ok "${ARCH}: ${output_so} → clibs/out/${ARCH}/"
        else
            fail "Missing: RESULT/${ARCH}/nimRum/${output_so}"
            exit 1
        fi
    done
}

#### BUILD ALL LIBS ####
build_lib "audio_source" "clibs/audio_source" "nimRumAudioSource_ct.so"
build_lib "dsp" "clibs/dsp" "libnimRumDSP.so"

echo ""
ok "All C libraries built successfully."
