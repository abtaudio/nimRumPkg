# nimRumWebUI

## Purpose
Flask-based web interface for monitoring and controlling the nimRum audio system.
Runs on the TX device, polls TX status via localhost UDP, and serves multiple views.

## Views

### Topology View (`/`)
Canvas-based visualization:
- Centre circle — two rows of text, and pressable:
  - Upper row: active source name (e.g. "Vinyl"), or "No SRC", or "No TX".
    Long names shrink then ellipsize to stay inside the circle.
  - Lower row: "Mute" when unmuted, "Muted" when muted.
  - Colour: green when playing, red when muted or when TX has no source,
    grey when TX is offline.
  - Clicking it does **the same thing as mute on the IR remote** (temporary
    mute) — not a second mute mechanism. Cleared by any volume change, exactly
    like the remote.
- RX circles on outer ring — inner color = sync state, outer ring = network quality
  - A client that has stopped answering (`rxstate.js` `isLost`) goes slate and loses
    its Locked+ "+" marker: its last reported status is stale.
- Draggable RX positions (saved to localStorage)
- Sector indicators (subtle arcs) show each speaker's glue zone
- Group arcs at half-radius — click to toggle scene mute for that group:
  - Green: all speakers in group unmuted
  - Red: all speakers in group scene-muted
  - Amber: mixed state (some muted, some not)
- Source chips bar — click to switch active audio source
- Store Groups button — persists current grouping to TX
- Reset button — restores angles from TX groups (clustered layout)

**Visual grammar:** red marks the control that is engaged, transparency marks the
speakers that are actually silent. A scene-muted group shows a red arc and its
speakers dimmed; temporary mute shows a red centre and every speaker dimmed. The
two channels answer different questions — what did I press, and what is quiet.
The cursor becomes a pointer over the centre and over the group arcs.

### List View (`/list`)
Card-based client list:
- Per-client: name, location, state, jitter, latency
- A client that stopped answering shows "Lost" and is dimmed like a paused one;
  the same `rxstate.js` helper decides that as on the topology canvas
- TX Online/Offline badge in header
- System info (latency, connected count)

### Levels (`/levels`)
Per-speaker volume adjustment sliders (stereo/multi layout levels, volCal), plus
two main-volume controls that are deliberately different things:

- **Main Volume** (top) — live TX volume. Moving it takes effect immediately and
  is never written to disk. It also follows the volume being changed elsewhere
  (IR remote, LanCtrl, the Topology tab) via the status poll, with a short grace
  period after a local move so the poll cannot fight a finger on the slider.
  The Topology tab's slider behaves the same way.
- **Startup volume** (its own block below the *Save to config* button, styled
  like a speaker group) — `startupVolume` in `txConfig.yaml`, what TX comes up
  with after a restart or power cut. Deliberately low so the system never wakes
  up loud. Moving this slider changes nothing until *Save to config* is pressed;
  *Copy current* pulls the live value into it.

A group's *Stereo adj* / *Multi adj* slider writes the same value to every member,
so config where members differ cannot be represented by one knob. When that
happens the slider shows the **lowest** member value in amber with a `≠` badge
listing the individual values — moving it then never makes a speaker louder than
it already is, and it flattens all members to one value.

Before 2026-08-29 there was one slider and one config key (`mainVolume`) doing
both jobs, and `setMainVolume` rewrote the whole of `txConfig.yaml` on every
slider step — the sliders fire on `input`, so a single drag could rewrite the
file dozens of times. That is flash wear, and worse, a power cut mid-write could
truncate the file that holds the entire client configuration.

### Channel Map (`/channelmap`)
Per-speaker channel assignment per layout. Define channel layouts and
assign speakers to channels.

### EQ Calibration (`/calibration`)
Automated speaker measurement and EQ fitting. Chirp injection, transfer
function computation, auto-EQ, profile management, verify, import.

### System (`/system`)
Fleet software deploy, device info table, TX restart/reboot.

### Config (`/config`)
YAML editors for TX and RX/SRC device configs with field reference.

## Speaker Grouping (Sector-Based Glue)

Speakers on the circle can be grouped by dragging them together:
- Each speaker has a sector (default 15°). Drag two sectors to overlap → they glue.
- Drag a speaker > 40° away from its nearest group neighbor → unglue.
- Groups use explicit glue bonds (union-find), not just angular proximity.
- Thresholds are variables (`GLUE_THRESHOLD_DEG`, `SEPARATE_THRESHOLD_DEG`).
- Glued speakers within a group share a single mute/unmute toggle.
- TX is the source of truth for groups. Stored in `nimRumSceneState.json`.
- On restore (page load or reset), speakers in the same group are positioned close together.

## Two-Layer Mute

| Layer | Source | Scope | Cleared by |
|-------|--------|-------|-----------|
| **Temporary mute** | IR remote (KEY_MUTE) | All speakers | vol+ or vol- on remote |
| **Scene mute** | UI group arc click, or centre circle for all speakers | Per-group, or all | Clicking the same control again |

Resolved: `muted = temporary_mute OR scene_mute[speaker]`

- Vol+/vol- adjusts volume for scene-muted speakers silently (they come back at new level when un-scene-muted).
- Scene mute persists across temporary mute/unmute cycles.
- Scene-muted speakers still receive audio (keeps sync filters running).

## REST API

### GET /api/status
Returns JSON with full system state:
```json
{
  "txOnline": true,
  "activeSourceId": 1,
  "activeSourceName": "TV",
  "sources": [{"id": 1, "name": "TV"}, {"id": 5, "name": "Vinyl"}],
  "clients": [
    {"status": 2, "paused": 0, "nqGrade": 0, "nqJitterP95": 150, "name": "speaker1", "location": "front-left", ...}
  ],
  "latency_us": 100000,
  "sampleRate": 48000,
  "temporaryMute": false,
  "sceneMute": {"0": false, "1": true, "2": false},
  "groups": [[0, 1], [2], [3, 4]],
  "mainVolume": 20
}
```

### POST /api/status
All commands return full status in the response (single round-trip update).

Commands:
- `{"cmd": "setVolume", "cliId": 0, "val": 80}` — Set client volume (0=mute, -1=unmute to config)
- `{"cmd": "setMainVolume", "val": 75}` — Set master volume (clears temporary mute)
- `{"cmd": "muteToggle"}` — Toggle temporary mute, same as mute on the IR remote
- `{"cmd": "setSource", "sourceId": 1}` — Switch active source
- `{"cmd": "setLatency", "val": 50000}` — Set latency in µs
- `{"cmd": "setSceneMute", "cliIds": [0,1], "mute": true}` — Explicitly set scene mute
- `{"cmd": "setSceneMuteToggle", "cliIds": [0,1]}` — Toggle scene mute (sync-then-toggle)
- `{"cmd": "setGroups", "groups": [[0,1],[2],[3,4]]}` — Update group membership

### GET /api/groups
Returns current groups from TX: `{"groups": [[0,1],[2],[3,4]]}`

### POST /api/groups
Store groups at TX: `{"groups": [[0,1],[2],[3,4]]}` → proxied to `setGroups` command.

## Files
```
nimRum/tx_webui/
├── app.py                           — Flask app, shared state, core routes
├── routes_levels.py                 — Levels tab API
├── routes_channelmap.py             — Channel map tab API
├── routes_calibration.py            — Calibration tab API + measurement workers
├── routes_system.py                 — System tab API + config API
├── nimRumWebUI.py                   — Entry point wrapper
├── static/
│   ├── topology.html / topology.js  — Topology view (grouping + mute)
│   ├── topology.css                 — Topology styles
│   ├── levels.html / levels.js      — Levels view
│   ├── channelmap.html / channelmap.js — Channel map view
│   ├── calibration.html / calibration.js — Calibration view
│   ├── system.html / system.js      — System view
│   ├── config.html / config.js      — Config editor view
│   ├── index.html / app.js          — List view
│   ├── rxstate.js                   — Shared RX-state interpretation (lost/state/net grade)
│   └── style.css                    — Shared styles (CSS variables, header, badges)
nimRum/tx/nimRumTxStatusApi.py       — UDP status server (runs in TX process)
nimRum/tx/nimRumTxCfg.py             — Config + two-layer mute state + scene persistence
```

## State Persistence
- `txConfig.yaml` — Main volume, latency, client config (YAML)
- `nimRumSceneState.json` — Groups + scene_mute per client (JSON, next to txConfig.yaml)
- `localStorage` (browser) — Speaker angles only (visual positions)

## Notes
- Status is polled from TX every 2 seconds (background thread in Python, setInterval in JS)
- POST responses update the Flask cache immediately (no stale-cache flicker)
- `activeSourceId` = -1 means no source is currently feeding audio (1s timeout in C layer)
- Web UI runs on the TX device only
- Port 80 (via Flask)
