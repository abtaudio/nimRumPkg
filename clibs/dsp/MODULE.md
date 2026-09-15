# nimRumDSP — Audio Processing Plugin for nimRum RX

## Purpose
Optional DSP plugin that processes audio in-place on the RX path, before
ALSA playback. Provides parametric EQ (biquad filters) for room correction
and enables user-defined audio processing.

## Architecture
```
libNimRumRx (closed source)
    │
    │ dlopen("libnimRumDSP.so") at startup
    │ calls nimRumDSP_process() before magicPipe → ALSA
    │
    ▼
libnimRumDSP.so (this module, open source)
    ├── nimRumDSP.c        — Plugin API (init, process, reload, enable, close)
    ├── nimRumDSP_biquad.c — Biquad filter bank (Audio EQ Cookbook)
    └── nimRumDSP_config.c — YAML config loader (CamillaDSP-compatible format)
```

## Public API (nimRumDSP.h)
- `nimRumDSP_init(config_path, sample_rate, num_channels)` — Initialize plugin
- `nimRumDSP_process(data[], num_channels, num_frames)` — Process audio in-place
- `nimRumDSP_reload(config_path)` — Hot-reload config without glitches
- `nimRumDSP_setEnable(enable)` — Enable/bypass toggle
- `nimRumDSP_getEnabled()` — Query enabled state
- `nimRumDSP_close()` — Shutdown
- `nimRumDSP_version()` — Version string

## Configuration Format (CamillaDSP-compatible YAML)
```yaml
enabled: true
sample_rate: 48000
filters:
  # Parametric EQ — computed from freq/gain/Q using Audio EQ Cookbook
  - type: Peaking
    freq: 250.0
    gain: -4.2
    q: 2.1
  - type: Highshelf
    freq: 8000.0
    gain: -2.0
    q: 0.7
  # Raw biquad coefficients — imported from external tools (REW, EQ APO, AutoEQ)
  - type: Raw
    b0: 1.002346
    b1: -1.984521
    b2: 0.982301
    a1: -1.984521
    a2: 0.984647
```

Supported filter types:
- **Peaking** — parametric bell (freq, gain, Q)
- **Lowshelf** — low shelf (freq, gain, Q)
- **Highshelf** — high shelf (freq, gain, Q)
- **Lowpass** — 2nd order low-pass (freq, Q)
- **Highpass** — 2nd order high-pass (freq, Q)
- **Raw** — direct biquad coefficients (b0, b1, b2, a1, a2, normalized with a0=1)

The Raw type allows importing filter designs from any external tool that exports
standard biquad coefficients. Parametric and raw bands can be mixed freely.

Maximum 32 bands per config.

## Importing External Filters

### From WebUI
The Calibration tab has an "Import EQ Coefficients" section. Paste one filter
per line in CSV format:
```
Peaking, 250, -4.2, 2.1
Highshelf, 8000, -2.0, 0.7
Raw, 1.0023, -1.9845, 0.9823, -1.9845, 0.9846
```
Click "Preview" to see the combined response curve, then "Deploy" to push
to the selected speaker.

### From REW (Room EQ Wizard)
1. Run measurement in REW → EQ → Auto EQ
2. Export: File → Export filter settings as text
3. Convert to our format (type, freq, gain, Q per line)
4. Paste in WebUI or write to `eq_config.yaml` directly

### From EQ APO / AutoEQ
These tools export parametric EQ as `Filter N: ON PK Fc 250 Hz Gain -4.2 dB Q 2.1`.
Convert to our CSV format: `Peaking, 250, -4.2, 2.1`

## Config File Location
Relative to RX working directory: `./calibration/eq_config.yaml`

## Integration with libNimRumRx
libNimRumRx needs this minimal hook (dlopen-based):
```c
// At init time:
void *dsp_handle = dlopen("libnimRumDSP.so", RTLD_NOW);
if (dsp_handle) {
    dsp_init = dlsym(dsp_handle, "nimRumDSP_init");
    dsp_process = dlsym(dsp_handle, "nimRumDSP_process");
    // ... etc
    dsp_init(NULL, sample_rate, num_channels);
}

// In audio loop, before magicPipe write:
if (dsp_process) {
    dsp_process(data, num_channels, num_frames);
}

// At shutdown:
if (dsp_close) dsp_close();
if (dsp_handle) dlclose(dsp_handle);
```

## Performance
- 10 biquad bands, stereo, 384 frames: ~15µs per call on Pi 3B+
- Zero added latency (in-place processing)
- No memory allocation in process path

## Building
```sh
# Cross-compile for both architectures (from nimRumPkg root):
./clibs/build.sh

# Or rebuild Docker images first:
./clibs/docker/rebuildDocker.sh
```
Output: `nimRum/aarch64/libnimRumDSP.so`, `nimRum/armv7l/libnimRumDSP.so`

## Dependencies
- Standard C library (math.h, stdio.h, string.h)
- No external libraries

## License
GPL-3.0 with nimRumLib linking exception — see LICENSE.txt in the project root.
