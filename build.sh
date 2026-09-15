#!/usr/bin/env bash
# build.sh — Build nimRumAudioSource .so + assemble wheels
#
# Usage:
#   ./build.sh              Build audioSource + wheels
#   ./build.sh -c           Clean rebuild
#   ./build.sh -d "1 3"     Build + deploy to devices
#   ./build.sh -b           Restart after deploy
#
# Prerequisites:
#   - Docker images: nimrumpkg-arm64, nimrumpkg-armv7
#     (run clibs/docker/rebuildDocker.sh)
#   - TX/RX .so files in nimRum/aarch64/ and nimRum/armv7l/
#     (from nimRumLib/deploy_to_pkg.sh)

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; NC='\033[0m'
ok() { echo -e "${GREEN}✓${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; }
step() { echo -e "${CYAN}►${NC} $1"; }

# Parse options
doClean=0
doDeploy=""
doRestart=0

while getopts "cd:br" opt; do
    case $opt in
        c) doClean=1 ;;
        d) doDeploy="$OPTARG" ;;
        b) doRestart=1 ;;
        r) doRestart=2 ;;
        *) echo "Usage: $0 [-c] [-d \"1 3 4\"] [-b] [-r]"; exit 1 ;;
    esac
done

# Check TX/RX .so files are present (from nimRumLib)
for ARCH in aarch64 armv7l; do
    if [ ! -f "clibs/nimRumLib/${ARCH}/libNimRumTx_ct.so" ]; then
        fail "Missing clibs/nimRumLib/${ARCH}/libNimRumTx_ct.so"
        echo "  Run: cd ../nimRumLib && ./deploy_to_pkg.sh"
        exit 1
    fi
done

#### BUILD C LIBRARIES ####
buildArgs=""
[ $doClean -eq 1 ] && buildArgs="-c"

if ! ./clibs/build.sh $buildArgs; then
    fail "C libraries build failed!"
    exit 1
fi

#### ASSEMBLE WHEELS ####
step "Assembling wheels..."
rm -rf dist/

for ARCH in aarch64 armv7l; do
    # Stage all .so files for this arch into nimRum/ (flat, for wheel packaging)
    cp clibs/nimRumLib/${ARCH}/*_ct.so nimRum/
    cp clibs/out/${ARCH}/*_ct.so nimRum/
    cp clibs/out/${ARCH}/libnimRumDSP.so nimRum/

    # Write platform tag.
    #
    # PyPI will not accept every tag that builds. Its allowlist
    # (warehouse/forklift/legacy.py) contains linux_armv6l and linux_armv7l as explicit
    # concessions, but NOT linux_aarch64 — a linux_aarch64 wheel is rejected on upload,
    # which is how 2.8.2 was nearly published as 32-bit only. For aarch64 the accepted
    # form is a PEP 600 manylinux tag, so emit one.
    #
    # The glibc version is READ OFF THE BINARIES rather than hardcoded, so the tag states
    # what the artefacts actually require and follows the build image if it is upgraded.
    # Note the honest caveat: manylinux also expects no external library dependencies
    # beyond its allowed list, and these link libasound, which is not on it. The glibc
    # claim is exact; full manylinux policy compliance is not claimed, and the README
    # already lists libasound2-dev as a prerequisite.
    if [ "${ARCH}" = "aarch64" ]; then
        glibc=$(readelf -V nimRum/*_ct.so 2>/dev/null \
                | grep -o 'GLIBC_[0-9.]*' | sort -uV | tail -1 \
                | sed 's/GLIBC_//; s/\./_/g')
        if [ -z "${glibc}" ]; then
            fail "could not read the glibc requirement from the aarch64 .so files"
            exit 1
        fi
        echo "manylinux_${glibc}_${ARCH}" > platformName.txt
        ok "aarch64 tagged manylinux_${glibc}_${ARCH} (PyPI rejects linux_aarch64)"
    else
        echo "linux_${ARCH}" > platformName.txt
    fi

    # Build wheel
    python3 -m build --wheel 2>&1 | grep -E "^(Successfully|ERROR)" || true
done

# Clean staged files
rm -f nimRum/*_ct.so nimRum/libnimRumDSP.so platformName.txt

echo ""
ok "Wheels built:"
ls -1 dist/*.whl

#### DEPLOY ####
if [ -n "$doDeploy" ]; then
    deployArgs="-d \"${doDeploy}\""
    [ $doRestart -eq 1 ] && deployArgs="$deployArgs -b"
    [ $doRestart -eq 2 ] && deployArgs="$deployArgs -r"
    eval ./scripts/deploy.py $deployArgs
fi
