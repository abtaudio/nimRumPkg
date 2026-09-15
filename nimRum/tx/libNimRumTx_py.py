"""ctypes wrapper for libNimRumTx and nimRumAudioSourceRx.

Loads libNimRumTx_ct.so (a plain C shared library with no Python dependency)
and exposes the same module-level functions as the old libNimRumTx_py.so
Python C extension. This allows the .so to work across Python versions
without recompilation.
"""

import ctypes
import ctypes.util
import os
import pathlib
from typing import List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Load shared library
# ---------------------------------------------------------------------------
_PKG_DIR = pathlib.Path(__file__).resolve().parent.parent
_THIS_DIR = _PKG_DIR
_SO_NAME = "libNimRumTx_ct.so"
_LIB_PATH = _THIS_DIR / _SO_NAME

if not _LIB_PATH.exists():
    # Fallback: try LD_LIBRARY_PATH
    _found = ctypes.util.find_library("NimRumTx_ct")
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
_c_char_p = ctypes.c_char_p
_c_int32 = ctypes.c_int32
_c_int32_p = ctypes.POINTER(ctypes.c_int32)
_c_uint32 = ctypes.c_uint32
_c_int8 = ctypes.c_int8
_c_uint8 = ctypes.c_uint8
_c_uint16 = ctypes.c_uint16
_c_int16 = ctypes.c_int16

_MAX_CLIENTS = 32


# ---------------------------------------------------------------------------
# Status struct (mirrors lib_nimrum_tx_status_t in C)
# ---------------------------------------------------------------------------
class _LibNimRumTxStatus(ctypes.Structure):
    _fields_ = [
        ("num_clients", _c_int),
        ("tx_ppm", _c_float),
        ("cli_status", _c_int * _MAX_CLIENTS),
        ("cli_paused", _c_int * _MAX_CLIENTS),
        ("cli_nq_jitter_p95", _c_uint16 * _MAX_CLIENTS),
        ("cli_nq_jitter_max", _c_uint16 * _MAX_CLIENTS),
        ("cli_nq_loss_permille", _c_uint16 * _MAX_CLIENTS),
        ("cli_nq_burst_ms", _c_uint16 * _MAX_CLIENTS),
        ("_reserved_08", _c_uint8 * _MAX_CLIENTS),
        ("cli_nq_rec_latency", _c_uint16 * _MAX_CLIENTS),
        ("_reserved_17", _c_int * _MAX_CLIENTS),
        ("_reserved_18", _c_int * _MAX_CLIENTS),
        ("cli_wifi_signal_dbm", _c_int8 * _MAX_CLIENTS),
        ("cli_wifi_link_quality", _c_uint8 * _MAX_CLIENTS),
        ("cli_wifi_tx_retries", _c_uint16 * _MAX_CLIENTS),
        ("cli_wifi_rx_errors", _c_uint16 * _MAX_CLIENTS),
        ("cli_ppm_tx", _c_int16 * _MAX_CLIENTS),
        ("cli_ppm_pcm", _c_int16 * _MAX_CLIENTS),
        ("cli_ppm_cpu", _c_int16 * _MAX_CLIENTS),
        ("cli_co_filt_std_dev_us", _c_int16 * _MAX_CLIENTS),
        ("cli_avg_tta_us", _c_int16 * _MAX_CLIENTS),
        ("cli_co_stable", _c_uint8 * _MAX_CLIENTS),
        ("cli_co_filters_full", _c_uint8 * _MAX_CLIENTS),
        # Accumulated totals — see libNimRumTx.h. Order and width must match the C
        # struct exactly; libNimRumTxGetStatus writes into a buffer allocated here.
        ("_reserved_13", _c_uint32 * _MAX_CLIENTS),
        ("_reserved_15", _c_uint32 * _MAX_CLIENTS),
        ("_reserved_10", _c_uint32 * _MAX_CLIENTS),
        ("_reserved_04", _c_uint8 * _MAX_CLIENTS),
        ("_reserved_05", _c_uint16 * _MAX_CLIENTS),
        ("_reserved_06", _c_int16 * _MAX_CLIENTS),
        ("_reserved_12", _c_uint16 * _MAX_CLIENTS),
        ("cli_rx_missed_pkts", _c_uint32 * _MAX_CLIENTS),
        ("_reserved_03", _c_int16 * _MAX_CLIENTS),
        ("_reserved_02", _c_uint16 * _MAX_CLIENTS),
        ("_reserved_14", _c_int16 * _MAX_CLIENTS),
        ("_reserved_00", ctypes.c_int64 * _MAX_CLIENTS),
        ("_reserved_01", _c_uint8 * _MAX_CLIENTS),
        ("cli_alsa_buffer_ms", _c_uint8 * _MAX_CLIENTS),
        ("_reserved_16", _c_uint16 * _MAX_CLIENTS),
        ("cli_cpu_load_percent", _c_uint8 * _MAX_CLIENTS),
        ("_reserved_07", _c_uint16 * _MAX_CLIENTS),
        ("_reserved_11", _c_uint32 * _MAX_CLIENTS),
        ("_reserved_09", _c_uint32 * _MAX_CLIENTS),
        ("cli_ip_addr", (ctypes.c_char * 16) * _MAX_CLIENTS),
        ("src_fifo_count", _c_int),
        ("src_overflow_count", _c_int),
        ("src_fifo_latency_us", _c_int),
        ("src_underrun_count", _c_int),
        ("src_read_count", _c_int),
        # Seen devices registry
        ("num_seen_devices", _c_int),
        ("seen_host_name", (ctypes.c_char * 16) * 32),
        ("seen_ip_addr", (ctypes.c_char * 16) * 32),
        ("seen_ssh_user", (ctypes.c_char * 16) * 32),
        ("seen_roles", _c_uint8 * 32),
        ("seen_is_configured", _c_uint8 * 32),
        ("seen_lib_version_major", _c_uint8 * 32),
        ("seen_lib_version_minor", _c_uint8 * 32),
        ("seen_lib_version_patch", _c_uint8 * 32),
        ("seen_version_mismatch", _c_uint8 * 32),
        ("seen_last_seen_ns", (ctypes.c_int64) * 32),
        # Leading indicators, nimRumLib 2.0.0. MUST stay in the same order and
        # with the same types as lib_nimrum_tx_status_t in libNimRumTx.h: this
        # struct is allocated here and passed as an out-parameter, so a mismatch
        # is a buffer overflow, not a misread. LIB_VERSION_MIN guards the pairing.
        ("cli_min_dly_frames", _c_int16 * _MAX_CLIENTS),
        ("cli_xfer_pad_cnt", _c_uint32 * _MAX_CLIENTS),
        ("cli_xfer_pad_frames", _c_uint32 * _MAX_CLIENTS),
        ("cli_halted_fill_cnt", _c_uint32 * _MAX_CLIENTS),
        ("cli_halted_fill_frames", _c_uint32 * _MAX_CLIENTS),
        # Last in the C struct too. 1 = internal build, 0 = public build with the
        # internal-only per-client fields zeroed. See
        # nimRumLib/inc/libNimRumPublicBuild.h - one switch controls the data and
        # the UI together so they cannot disagree.
        ("full_diagnostics", _c_uint8),
    ]


# ---------------------------------------------------------------------------
# Declare C function signatures
# ---------------------------------------------------------------------------
_lib.ct_libNimRumTxInit.argtypes = [_c_int, _c_int,
                                     ctypes.POINTER(_c_char_p)]
_lib.ct_libNimRumTxInit.restype = _c_int

_lib.ct_libNimRumTxClose.argtypes = []
_lib.ct_libNimRumTxClose.restype = None

_lib.ct_libNimRumTxConfigure.argtypes = [_c_int, _c_int, _c_int,
                                          ctypes.POINTER(_c_int)]
_lib.ct_libNimRumTxConfigure.restype = _c_int

_lib.ct_libNimRumTXForceWave.argtypes = [_c_int]
_lib.ct_libNimRumTXForceWave.restype = _c_int

_lib.ct_libNimRumTxProcess.argtypes = [ctypes.POINTER(_c_int32_p), _c_int,
                                        _c_int, _c_float,
                                        ctypes.POINTER(_c_int)]
_lib.ct_libNimRumTxProcess.restype = _c_int

_lib.ct_libNimRumTxSetVolume.argtypes = [_c_int, _c_int]
_lib.ct_libNimRumTxSetVolume.restype = None

_lib.ct_libNimRumTxSetChannel.argtypes = [_c_int, ctypes.POINTER(_c_int),
                                           _c_int]
_lib.ct_libNimRumTxSetChannel.restype = None

_lib.ct_libNimRumTxSetLatency.argtypes = [_c_int, _c_int]
_lib.ct_libNimRumTxSetLatency.restype = None

_lib.ct_libNimRumTxLogs.argtypes = [_c_int]
_lib.ct_libNimRumTxLogs.restype = None

_lib.ct_libNimRumTxSetLogsPath.argtypes = [_c_char_p]
_lib.ct_libNimRumTxSetLogsPath.restype = None

_lib.ct_libNimRumTxSetPrintLevel.argtypes = [_c_int]
_lib.ct_libNimRumTxSetPrintLevel.restype = None

_lib.ct_nimRumAudioSourceRx_getFifoLatencyUs.argtypes = []
_lib.ct_nimRumAudioSourceRx_getFifoLatencyUs.restype = _c_int

_lib.ct_libNimRumTxGetStatus.argtypes = [ctypes.POINTER(_LibNimRumTxStatus)]
_lib.ct_libNimRumTxGetStatus.restype = None
_lib.ct_libNimRumTx_resetCounters.argtypes = []
_lib.ct_libNimRumTx_resetCounters.restype = None

_lib.ct_libNimRumChannelMixer.argtypes = [_c_int32_p, _c_int32_p, _c_int32_p,
                                           _c_int, _c_int]
_lib.ct_libNimRumChannelMixer.restype = None

_lib.ct_nimRumAudioSourceRx_init.argtypes = [_c_int]
_lib.ct_nimRumAudioSourceRx_init.restype = _c_int

_lib.ct_nimRumAudioSourceRx_getData.argtypes = [
    ctypes.POINTER(_c_int32_p), _c_int,
    ctypes.POINTER(_c_int), ctypes.POINTER(_c_float),
    ctypes.POINTER(_c_uint32), ctypes.POINTER(_c_int),
]
_lib.ct_nimRumAudioSourceRx_getData.restype = _c_int

_lib.ct_nimRumAudioSourceRx_close.argtypes = []
_lib.ct_nimRumAudioSourceRx_close.restype = None

_lib.ct_nimRumAudioSourceRx_getActiveSourceId.argtypes = []
_lib.ct_nimRumAudioSourceRx_getActiveSourceId.restype = _c_int

_lib.ct_nimRumAudioSourceRx_getActiveSourceName.argtypes = []
_lib.ct_nimRumAudioSourceRx_getActiveSourceName.restype = _c_char_p

_lib.ct_nimRumAudioSourceRx_getActiveChannelLayout.argtypes = []
_lib.ct_nimRumAudioSourceRx_getActiveChannelLayout.restype = _c_char_p

_lib.ct_nimRumAudioSourceRx_setActiveSourceId.argtypes = [_c_int]
_lib.ct_nimRumAudioSourceRx_setActiveSourceId.restype = None

_lib.ct_nimRumAudioSourceRx_getKnownSources.argtypes = [
    ctypes.POINTER(_c_int), _c_char_p, _c_int,
]
_lib.ct_nimRumAudioSourceRx_getKnownSources.restype = _c_int

_lib.ct_nimRumAudioSourceRx_getSourceLinkStats.argtypes = [
    ctypes.POINTER(_c_int8), ctypes.POINTER(_c_uint8),
    ctypes.POINTER(_c_uint16), ctypes.POINTER(_c_uint16),
]
_lib.ct_nimRumAudioSourceRx_getSourceLinkStats.restype = None

_lib.ct_libNimRumGetVersion.argtypes = []
_lib.ct_libNimRumGetVersion.restype = _c_char_p


# ---------------------------------------------------------------------------
# Public Python API — matches the old libNimRumTx_py C extension exactly
# ---------------------------------------------------------------------------

def c_libNimRumTxInit(myId: int, clientNames: Sequence[str]) -> int:
    """Initialize TX with client name list. Returns 0 on success."""
    n = len(clientNames)
    arr = (_c_char_p * n)(*[name.encode("utf-8") for name in clientNames])
    return _lib.ct_libNimRumTxInit(myId, n, arr)


def c_libNimRumTxClose() -> None:
    """Shut down TX."""
    _lib.ct_libNimRumTxClose()


def c_libNimRumTxConfigure(bytesPerSample: int, sampleRate: int,
                           payloadType: int) -> Tuple[int, int]:
    """Configure TX. Returns (result, framesPerInterval)."""
    fpi = _c_int(0)
    res = _lib.ct_libNimRumTxConfigure(bytesPerSample, sampleRate,
                                        payloadType,
                                        ctypes.byref(fpi))
    return (res, fpi.value)


def c_libNimRumTXForceWave(clientIdx: int) -> int:
    """Force wave codec for a client."""
    return _lib.ct_libNimRumTXForceWave(clientIdx)


def c_libNimRumTxProcess(data: Sequence, numOfFrames: int,
                         txPPM: float) -> Tuple[int, int]:
    """Encode and send audio data. data is list of bytearray buffers.

    Returns (result, numOfChannels).
    """
    num_ch = len(data)
    # Build array of pointers to buffer data
    ptrs = (_c_int32_p * num_ch)()
    for i in range(num_ch):
        buf = data[i]
        # bytearray / bytes-like → cast to int32 pointer
        ptrs[i] = ctypes.cast(
            (_c_int32 * (len(buf) // 4)).from_buffer(buf),
            _c_int32_p,
        )
    num_ch_out = _c_int(0)
    res = _lib.ct_libNimRumTxProcess(ptrs, num_ch, numOfFrames,
                                      _c_float(txPPM),
                                      ctypes.byref(num_ch_out))
    return (res, num_ch_out.value)


def c_libNimRumTxSetVolume(clientIdx: int, volume: int) -> None:
    """Set per-client volume."""
    _lib.ct_libNimRumTxSetVolume(clientIdx, volume)


def c_libNimRumTxSetChannel(clientIdx: int, channelArr: Sequence[int]) -> None:
    """Set channel mapping for a client."""
    n = len(channelArr)
    arr = (_c_int * n)(*channelArr)
    _lib.ct_libNimRumTxSetChannel(clientIdx, arr, n)


def c_libNimRumTxSetLatency(clientIdx: int, latency_us: int) -> None:
    """Set latency offset for a client."""
    _lib.ct_libNimRumTxSetLatency(clientIdx, latency_us)


def c_nimRumAudioSourceRx_getFifoLatencyUs() -> int:
    """Return the latency the source FIFO adds, in microseconds.

    Cheap enough to poll per loop pass, unlike the full status struct.
    """
    return _lib.ct_nimRumAudioSourceRx_getFifoLatencyUs()


def c_libNimRumTxSetPrintLevel(level: int) -> None:
    """Set TX printout verbosity.

    Args:
        level: 0=ERR, 1=WARN, 2=NOTE, 3=DEBUG.
    """
    _lib.ct_libNimRumTxSetPrintLevel(level)


def c_libNimRumTxLogs(enable: int) -> None:
    """Enable/disable TX logging."""
    _lib.ct_libNimRumTxLogs(enable)


def c_libNimRumTxSetLogsPath(path: str) -> None:
    """Set TX log file path."""
    _lib.ct_libNimRumTxSetLogsPath(path.encode("utf-8"))


def c_libNimRumTxListToBuf(din: Sequence, dout: Sequence) -> None:
    """Copy nested Python lists into bytearray buffers (channel x samples).

    din: list of lists of int (channel samples)
    dout: list of bytearrays (pre-allocated output buffers)
    """
    for ch_idx, channel in enumerate(din):
        buf = dout[ch_idx]
        arr = (_c_int32 * (len(buf) // 4)).from_buffer(buf)
        for s_idx, sample in enumerate(channel):
            arr[s_idx] = sample


def c_libNimRumChannelMixer(buf: Sequence, numOfSamples: int,
                            chRes: int, chA: int, chB: int,
                            fader: int) -> None:
    """Mix two channels into a result channel using a crossfader position.

    buf: list of bytearray buffers indexed by channel number.
    """
    buf_a = (_c_int32 * (len(buf[chA]) // 4)).from_buffer(buf[chA])
    buf_b = (_c_int32 * (len(buf[chB]) // 4)).from_buffer(buf[chB])
    buf_res = (_c_int32 * (len(buf[chRes]) // 4)).from_buffer(buf[chRes])

    _lib.ct_libNimRumChannelMixer(
        ctypes.cast(buf_res, _c_int32_p),
        ctypes.cast(buf_a, _c_int32_p),
        ctypes.cast(buf_b, _c_int32_p),
        numOfSamples, fader,
    )


def c_libNimRumTx_resetCounters() -> None:
    """Zero the accumulating per-client damage counters.

    Only the fields TX sums rather than mirrors: missed packets, starve, xrun and the
    two silence paths. Everything else is mirrored from the RX and would reappear on
    the next reply, so resetting it would be a lie that lasts 8 ms.
    """
    _lib.ct_libNimRumTx_resetCounters()


def c_libNimRumTxGetStatus() -> dict:
    """Get TX status as a dict matching the old C extension output."""
    st = _LibNimRumTxStatus()
    _lib.ct_libNimRumTxGetStatus(ctypes.byref(st))

    clients = []
    for i in range(st.num_clients):
        cli = {
            "status": int(st.cli_status[i]),
            "paused": int(st.cli_paused[i]),
            "nqJitterP95": int(st.cli_nq_jitter_p95[i]),
            "nqJitterMax": int(st.cli_nq_jitter_max[i]),
            "nqLossPermille": int(st.cli_nq_loss_permille[i]),
            "nqBurstMs": int(st.cli_nq_burst_ms[i]),
            "nqRecLatency": int(st.cli_nq_rec_latency[i]) * 10,
            "wifiSignal": int(st.cli_wifi_signal_dbm[i]),
            "wifiQuality": int(st.cli_wifi_link_quality[i]),
            "wifiTxRetries": int(st.cli_wifi_tx_retries[i]),
            "wifiRxErrors": int(st.cli_wifi_rx_errors[i]),
            "ppmTx": float(st.cli_ppm_tx[i]) / 10.0,
            "ppmPcm": float(st.cli_ppm_pcm[i]) / 10.0,
            "ppmCpu": float(st.cli_ppm_cpu[i]) / 10.0,
            "coFiltStdDevUs": int(st.cli_co_filt_std_dev_us[i]),
            "avgTtaUs": int(st.cli_avg_tta_us[i]),
            "coStable": int(st.cli_co_stable[i]),
            "coFiltersFull": bool(st.cli_co_filters_full[i]),
            # Leading indicator: running min output buffer fill, -1 = not reported
            "minDlyFrames": int(st.cli_min_dly_frames[i]),
            # The two paths that emit silence, as cumulative totals
            "xferPadCnt": int(st.cli_xfer_pad_cnt[i]),
            "xferPadFrames": int(st.cli_xfer_pad_frames[i]),
            "haltedFillCnt": int(st.cli_halted_fill_cnt[i]),
            "haltedFillFrames": int(st.cli_halted_fill_frames[i]),
            "rxMissedPkts": int(st.cli_rx_missed_pkts[i]),
            "alsaBufferMs": int(st.cli_alsa_buffer_ms[i]),
            "cpuLoad": float(st.cli_cpu_load_percent[i]) / 100.0,
            "ipAddr": bytes(st.cli_ip_addr[i]).split(b"\x00", 1)[0].decode("utf-8", errors="replace"),
        }
        clients.append(cli)

    result = {
        "numClients": st.num_clients,
        "txPPM": float(st.tx_ppm),
        "clients": clients,
        "srcFifoCount": st.src_fifo_count,
        "srcOverflowCount": st.src_overflow_count,
        # Lets the WebUI hide the internal-only rows in a public build rather than
        # rendering them as zeros. One C define drives both - see
        # nimRumLib/inc/libNimRumPublicBuild.h.
        "fullDiagnostics": bool(st.full_diagnostics),
        "srcFifoLatencyUs": st.src_fifo_latency_us,
        "srcUnderrunCount": st.src_underrun_count,
        "srcReadCount": st.src_read_count,
    }

    # Seen devices registry
    seen_devices = []
    for i in range(st.num_seen_devices):
        dev = {
            "hostName": bytes(st.seen_host_name[i]).split(b"\x00", 1)[0].decode("utf-8", errors="replace"),
            "ipAddr": bytes(st.seen_ip_addr[i]).split(b"\x00", 1)[0].decode("utf-8", errors="replace"),
            "sshUser": bytes(st.seen_ssh_user[i]).split(b"\x00", 1)[0].decode("utf-8", errors="replace"),
            "roles": int(st.seen_roles[i]),
            "isConfigured": int(st.seen_is_configured[i]),
            "libVersion": f"{st.seen_lib_version_major[i]}.{st.seen_lib_version_minor[i]}.{st.seen_lib_version_patch[i]}",
            "versionMismatch": int(st.seen_version_mismatch[i]),
            "lastSeenNs": int(st.seen_last_seen_ns[i]),
        }
        seen_devices.append(dev)
    result["seenDevices"] = seen_devices

    return result


# ---------------------------------------------------------------------------
# nimRumAudioSourceRx functions
# ---------------------------------------------------------------------------

def c_nimRumAudioSourceRx_init(port: int) -> int:
    """Initialize audio source receiver on UDP port."""
    return _lib.ct_nimRumAudioSourceRx_init(port)


def c_nimRumAudioSourceRx_getData(
    data: Sequence, numOfFrames: int
) -> Tuple[int, int, float, int, int]:
    """Get audio data from source.

    Returns (result, activeChannels, txPPM, sampleRate, bytesPerSample).
    """
    num_ch = len(data)
    ptrs = (_c_int32_p * num_ch)()
    for i in range(num_ch):
        buf = data[i]
        ptrs[i] = ctypes.cast(
            (_c_int32 * (len(buf) // 4)).from_buffer(buf),
            _c_int32_p,
        )

    active_ch = _c_int(0)
    tx_ppm = _c_float(0.0)
    sample_rate = _c_uint32(48000)
    bps = _c_int(2)

    res = _lib.ct_nimRumAudioSourceRx_getData(
        ptrs, num_ch,
        ctypes.byref(active_ch), ctypes.byref(tx_ppm),
        ctypes.byref(sample_rate), ctypes.byref(bps),
    )
    return (res, active_ch.value, tx_ppm.value,
            int(sample_rate.value), bps.value)


def c_nimRumAudioSourceRx_close() -> None:
    """Close audio source receiver."""
    _lib.ct_nimRumAudioSourceRx_close()


def c_nimRumAudioSourceRx_getActiveSourceId() -> int:
    """Get active source ID."""
    return _lib.ct_nimRumAudioSourceRx_getActiveSourceId()


def c_nimRumAudioSourceRx_getActiveSourceName() -> str:
    """Get active source name."""
    raw = _lib.ct_nimRumAudioSourceRx_getActiveSourceName()
    if raw:
        return raw.decode("utf-8")
    return ""


def c_nimRumAudioSourceRx_getActiveChannelLayout() -> str:
    """Get channel layout announced by the active source."""
    raw = _lib.ct_nimRumAudioSourceRx_getActiveChannelLayout()
    if raw:
        return raw.decode("utf-8")
    return ""


def c_nimRumAudioSourceRx_setActiveSourceId(sourceId: int) -> None:
    """Force switch to a source ID."""
    _lib.ct_nimRumAudioSourceRx_setActiveSourceId(sourceId)


def c_nimRumAudioSourceRx_getKnownSources() -> List[dict]:
    """Get all known audio sources. Returns list of {id, name} dicts."""
    ids = (_c_int * 16)()
    names_buf = ctypes.create_string_buffer(16 * 32)
    count = _lib.ct_nimRumAudioSourceRx_getKnownSources(ids, names_buf, 16)
    result = []
    for i in range(count):
        name_bytes = names_buf[i * 32:(i + 1) * 32]
        name = name_bytes.split(b"\x00", 1)[0].decode("utf-8")
        result.append({"id": ids[i], "name": name})
    return result


def c_nimRumAudioSourceRx_getSourceLinkStats() -> dict:
    """Get WiFi link stats from active audio source."""
    signal = _c_int8(0)
    quality = _c_uint8(0)
    retries = _c_uint16(0)
    errors = _c_uint16(0)
    _lib.ct_nimRumAudioSourceRx_getSourceLinkStats(
        ctypes.byref(signal), ctypes.byref(quality),
        ctypes.byref(retries), ctypes.byref(errors),
    )
    return {
        "signal": int(signal.value),
        "quality": int(quality.value),
        "txRetries": int(retries.value),
        "rxErrors": int(errors.value),
    }


# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

def c_libNimRumGetVersion() -> str:
    """Get nimRumLib version string (e.g. '1.0.0')."""
    raw = _lib.ct_libNimRumGetVersion()
    return raw.decode("utf-8") if raw else "unknown"
