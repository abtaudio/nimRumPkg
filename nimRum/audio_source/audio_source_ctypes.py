"""ctypes wrapper for nimRumAudioSource.

Loads nimRumAudioSource_ct.so (a plain C shared library with no Python
dependency) and exposes the same module-level functions as the old
nimRumAudioSource_py.so Python C extension.
"""

import ctypes
import ctypes.util
import pathlib
from typing import Optional

# ---------------------------------------------------------------------------
# Load shared library
# ---------------------------------------------------------------------------
_PKG_DIR = pathlib.Path(__file__).resolve().parent.parent
_THIS_DIR = _PKG_DIR
_SO_NAME = "nimRumAudioSource_ct.so"
_LIB_PATH = _THIS_DIR / _SO_NAME

if not _LIB_PATH.exists():
    _found = ctypes.util.find_library("nimRumAudioSource_ct")
    if _found:
        _LIB_PATH = _found
    else:
        raise OSError(f"Cannot find {_SO_NAME} in {_THIS_DIR} or LD_LIBRARY_PATH")

_lib = ctypes.CDLL(str(_LIB_PATH))

# ---------------------------------------------------------------------------
# C type aliases
# ---------------------------------------------------------------------------
_c_int = ctypes.c_int
_c_float = ctypes.c_float
_c_uint32 = ctypes.c_uint32
_c_char_p = ctypes.c_char_p

# ---------------------------------------------------------------------------
# Declare C function signatures
# ---------------------------------------------------------------------------
_lib.ct_nimRumAudioSource_start.argtypes = [
    _c_char_p, _c_char_p, _c_char_p, _c_int, _c_char_p,
]
_lib.ct_nimRumAudioSource_start.restype = _c_int

_lib.ct_nimRumAudioSource_stop.argtypes = []
_lib.ct_nimRumAudioSource_stop.restype = _c_int

_lib.ct_nimRumAudioSource_setPrintLevel.argtypes = [_c_int]
_lib.ct_nimRumAudioSource_setPrintLevel.restype = None

_lib.ct_nimRumAudioSource_setVolumeDb.argtypes = [_c_int]
_lib.ct_nimRumAudioSource_setVolumeDb.restype = None

_lib.ct_nimRumAudioSource_setStreaming.argtypes = [_c_int]
_lib.ct_nimRumAudioSource_setStreaming.restype = None

_lib.ct_nimRumAudioSource_isStreaming.argtypes = []
_lib.ct_nimRumAudioSource_isStreaming.restype = _c_int

_lib.ct_nimRumAudioSource_requestActive.argtypes = []
_lib.ct_nimRumAudioSource_requestActive.restype = None

_lib.ct_nimRumAudioSource_requestDeselect.argtypes = []
_lib.ct_nimRumAudioSource_requestDeselect.restype = None

_lib.ct_nimRumAudioSource_isRunning.argtypes = []
_lib.ct_nimRumAudioSource_isRunning.restype = _c_int

_lib.ct_nimRumAudioSource_getStatus.argtypes = [
    ctypes.POINTER(_c_float), ctypes.POINTER(_c_uint32),
    ctypes.POINTER(_c_int),
]
_lib.ct_nimRumAudioSource_getStatus.restype = None

_lib.ct_nimRumAudioSource_setChannelLayout.argtypes = [_c_char_p]
_lib.ct_nimRumAudioSource_setChannelLayout.restype = None

_lib.ct_nimRumAudioSource_setFifoTargetSuggestion.argtypes = [_c_int]
_lib.ct_nimRumAudioSource_setFifoTargetSuggestion.restype = None


# ---------------------------------------------------------------------------
# Public Python API — matches the old nimRumAudioSource_py C extension
# ---------------------------------------------------------------------------

def c_nimRumAudioSource_start(sourceName: str,
                              targetHost: str,
                              alsaDevice: str, captureMode: int,
                              filePath: str = "") -> int:
    """Start audio source capture and streaming.

    Args:
        sourceName: Human-readable source name.
        targetHost: TX IP address, or "" for auto-discovery via ping.
        alsaDevice: ALSA capture device name.
        captureMode: 0=spdif, 2=file.
        filePath: Path to audio file (mode 2 only).
    """
    return _lib.ct_nimRumAudioSource_start(
        sourceName.encode("utf-8"),
        targetHost.encode("utf-8"),
        alsaDevice.encode("utf-8"),
        captureMode,
        filePath.encode("utf-8"),
    )


def c_nimRumAudioSource_stop() -> int:
    """Stop audio source."""
    return _lib.ct_nimRumAudioSource_stop()


def c_nimRumAudioSource_setPrintLevel(level: int) -> None:
    """Set audio source printout verbosity.

    Args:
        level: 0=ERR, 1=WARN, 2=NOTE, 3=DEBUG. The C side defaults to WARN.
    """
    _lib.ct_nimRumAudioSource_setPrintLevel(level)


def c_nimRumAudioSource_setVolumeDb(volumeDb: int) -> None:
    """Set volume in dB."""
    _lib.ct_nimRumAudioSource_setVolumeDb(volumeDb)


def c_nimRumAudioSource_setStreaming(enable: int) -> None:
    """Enable or disable streaming."""
    _lib.ct_nimRumAudioSource_setStreaming(enable)


def c_nimRumAudioSource_isStreaming() -> int:
    """Check if currently streaming (0=idle, 1=streaming)."""
    return _lib.ct_nimRumAudioSource_isStreaming()


def c_nimRumAudioSource_requestActive() -> None:
    """Request to become the active source (sends immediate ping to TX)."""
    _lib.ct_nimRumAudioSource_requestActive()


def c_nimRumAudioSource_requestDeselect() -> None:
    """Request to be deselected (sends immediate ping to TX)."""
    _lib.ct_nimRumAudioSource_requestDeselect()


def c_nimRumAudioSource_isRunning() -> int:
    """Check if audio source is running."""
    return _lib.ct_nimRumAudioSource_isRunning()


def c_nimRumAudioSource_getStatus() -> dict:
    """Get audio source status.

    Returns dict with keys: ppm, seqNum, channels.
    """
    ppm = _c_float(0.0)
    seq_num = _c_uint32(0)
    channels = _c_int(0)
    _lib.ct_nimRumAudioSource_getStatus(
        ctypes.byref(ppm), ctypes.byref(seq_num), ctypes.byref(channels),
    )
    return {
        "ppm": float(ppm.value),
        "seqNum": int(seq_num.value),
        "channels": int(channels.value),
    }


def c_nimRumAudioSource_setChannelLayout(layout: str) -> None:
    """Set the channel layout name announced in SRC ping."""
    _lib.ct_nimRumAudioSource_setChannelLayout(layout.encode("utf-8"))


def c_nimRumAudioSource_setFifoTargetSuggestion(packets: int) -> None:
    """Suggest, in the SRC ping, TX's input FIFO pre-fill depth for this source.

    Args:
        packets: Pre-fill depth in packets. 0 leaves the decision to TX, which is
            also what an older source sends. TX clamps the value against its own
            FIFO depth and logs what it settled on.
    """
    _lib.ct_nimRumAudioSource_setFifoTargetSuggestion(int(packets))
