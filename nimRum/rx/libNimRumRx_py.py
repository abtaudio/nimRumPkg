"""ctypes wrapper for libNimRumRx.

Loads libNimRumRx_ct.so (a plain C shared library with no Python dependency)
and exposes the same module-level functions as the old libNimRumRx_py.so
Python C extension. This allows the .so to work across Python versions
without recompilation.
"""

import ctypes
import ctypes.util
import pathlib
from typing import Callable, Tuple

# ---------------------------------------------------------------------------
# Load shared library
# ---------------------------------------------------------------------------
_PKG_DIR = pathlib.Path(__file__).resolve().parent.parent
_THIS_DIR = _PKG_DIR
_SO_NAME = "libNimRumRx_ct.so"
_LIB_PATH = _THIS_DIR / _SO_NAME

if not _LIB_PATH.exists():
    _found = ctypes.util.find_library("NimRumRx_ct")
    if _found:
        _LIB_PATH = _found
    else:
        raise OSError(f"Cannot find {_SO_NAME} in {_THIS_DIR} or LD_LIBRARY_PATH")

_lib = ctypes.CDLL(str(_LIB_PATH))

# ---------------------------------------------------------------------------
# C type aliases
# ---------------------------------------------------------------------------
_c_int = ctypes.c_int
_c_uint32 = ctypes.c_uint32
_c_float = ctypes.c_float
_c_char_p = ctypes.c_char_p

# Callback type matching the C signature:
#   int callback(int state, float ppmCPU, float ppmPCM, int volumeIn,
#                int *softVolumeEnable_out, int *volumeOut_out)
_RX_CALLBACK_T = ctypes.CFUNCTYPE(
    _c_int,                          # return
    _c_int,                          # state
    _c_float,                        # ppmCPU
    _c_float,                        # ppmPCM
    _c_int,                          # volumeIn
    ctypes.POINTER(_c_int),          # softVolumeEnable_out
    ctypes.POINTER(_c_int),          # volumeOut_out
)

# ---------------------------------------------------------------------------
# Declare C function signatures
# ---------------------------------------------------------------------------
_lib.ct_libNimRumRxInit.argtypes = [
    _c_char_p, _c_char_p, _c_uint32, _c_char_p, _RX_CALLBACK_T,
    _c_int, _c_int, _c_int, _c_int, _c_char_p, _c_char_p,
    _c_char_p,
]
_lib.ct_libNimRumRxInit.restype = _c_int

_lib.ct_libNimRumRxStart.argtypes = []
_lib.ct_libNimRumRxStart.restype = _c_int

_lib.ct_libNimRumRxClose.argtypes = []
_lib.ct_libNimRumRxClose.restype = None

_lib.ct_libNimRumRxSetPrintLevel.argtypes = [_c_int]
_lib.ct_libNimRumRxSetPrintLevel.restype = None

_lib.ct_libNimRumRxSetLogs.argtypes = [_c_int]
_lib.ct_libNimRumRxSetLogs.restype = None

_lib.ct_libNimRumRxSetLogsPath.argtypes = [_c_char_p]
_lib.ct_libNimRumRxSetLogsPath.restype = None

_lib.ct_libNimRumRxSetStaticDelay.argtypes = [_c_int]
_lib.ct_libNimRumRxSetStaticDelay.restype = None

_lib.ct_libNimRumGetVersion.argtypes = []
_lib.ct_libNimRumGetVersion.restype = _c_char_p

# Keep a reference to the ctypes callback so it doesn't get garbage collected
_active_callback = None


# ---------------------------------------------------------------------------
# Public Python API — matches the old libNimRumRx_py C extension exactly
# ---------------------------------------------------------------------------

def c_libNimRumRxInit(bcastAddr: str, nic: str, myId: int, myName: str,
                      callback: Callable, useLocalClock: int,
                      pcmMode: int, forceS16: int, outputChannels: int,
                      pcmDevName: str, volDevName: str,
                      sshUser: str = "") -> int:
    """Initialize RX.

    The callback receives (state, ppmCPU, ppmPCM, volumeIn) and must
    return (returnValue, softVolumeEnable, volumeOut) as a tuple.

    Args:
        nic: Interface to bind every socket to and to read WiFi link stats
            from. "" lets the routing table and auto-detection decide, which
            is wrong on a device with two dongles on one subnet.
        pcmMode: Playback backend: 0=dummy, 1=ALSA auto, 2=ALSA force
            mmap+poll, 3=ALSA force writei. Resolved by nimRumRxCfg.
        forceS16: 1 = force ALSA S16_LE format instead of widest available.
        sshUser: SSH username for this device (reported to TX via ping).
    """
    global _active_callback

    def _bridge(state, ppm_cpu, ppm_pcm, volume_in,
                soft_vol_out, vol_out_ptr):
        ret_val, soft_vol, vol_out = callback(
            state, ppm_cpu, ppm_pcm, volume_in
        )
        soft_vol_out[0] = soft_vol
        vol_out_ptr[0] = vol_out
        return ret_val

    # Wrap in CFUNCTYPE and keep reference alive
    _active_callback = _RX_CALLBACK_T(_bridge)

    return _lib.ct_libNimRumRxInit(
        bcastAddr.encode("utf-8"),
        (nic or "").encode("utf-8"),
        _c_uint32(myId),
        myName.encode("utf-8"),
        _active_callback,
        useLocalClock,
        pcmMode,
        forceS16,
        outputChannels,
        (pcmDevName or "").encode("utf-8"),
        (volDevName or "").encode("utf-8"),
        (sshUser or "").encode("utf-8"),
    )


def c_libNimRumRxStart() -> int:
    """Start RX playback loop (blocks until stopped)."""
    return _lib.ct_libNimRumRxStart()


def c_libNimRumRxClose() -> None:
    """Close and clean up RX."""
    _lib.ct_libNimRumRxClose()


def c_libNimRumRxSetPrintLevel(level: int) -> None:
    """Set printout verbosity (0=ERR, 1=WARN, 2=NOTE, 3=DEBUG).

    Unrelated to c_libNimRumRxSetLogs, which controls .dat data logging.
    """
    _lib.ct_libNimRumRxSetPrintLevel(level)


def c_libNimRumRxSetLogs(enable: int) -> None:
    """Enable/disable RX logging."""
    _lib.ct_libNimRumRxSetLogs(enable)


def c_libNimRumRxSetLogsPath(path: str) -> None:
    """Set RX log file path."""
    _lib.ct_libNimRumRxSetLogsPath((path or "").encode("utf-8"))


def c_libNimRumRxSetStaticDelay(staticAdjust: int) -> None:
    """Set a static delay adjustment."""
    _lib.ct_libNimRumRxSetStaticDelay(staticAdjust)


# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

def c_libNimRumGetVersion() -> str:
    """Get nimRumLib version string (e.g. '1.0.0')."""
    raw = _lib.ct_libNimRumGetVersion()
    return raw.decode("utf-8") if raw else "unknown"
