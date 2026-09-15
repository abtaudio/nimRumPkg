"""nimRum — synchronized multi-room audio over WiFi."""

try:
    from importlib.metadata import version
    __version__ = version("nimRum")
except Exception:
    __version__ = "unknown"

# Minimum compatible nimRumLib version (major.minor.patch)
#
# 1.3.0: libNimRumRxInit gained a `nic` parameter, second positional. An older
# .so would take the nic string as myId and shift every argument after it, so
# this gate is the only thing standing between a partial deploy and garbage
# arguments. Raise it whenever the ctypes signature changes, not just the wire
# protocol.
#
# 1.5.0: lib_nimrum_tx_status_t gained cli_co_filters_full. The ctypes signature
# is unchanged, but the struct is an out-parameter allocated on the Python side,
# so a size disagreement is a buffer overflow rather than a misread. This gate
# catches an old .so under a new wheel. The reverse — a new .so under an old
# wheel, which overflows by 32 bytes — cannot be caught here, because the old
# wheel carries the old minimum. Lib and pkg must land together.
LIB_VERSION_MIN = "3.5.0"
