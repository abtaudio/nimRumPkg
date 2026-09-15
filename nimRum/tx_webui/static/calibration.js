(function() {
    'use strict';

    // One wire volume step in dB. Must match VOL_DB_PER_STEP in nimRumTxCfg.py
    // and LIB_PREZO_SOFT_VOL_DB_PER_STEP in libPrezoMagicPipe.h. volCal is
    // stored in steps, so it needs converting before it is shown as dB.
    var VOL_DB_PER_STEP = 0.5;

    // --- DOM elements ---
    var speakerSelect = document.getElementById('speaker-select');
    var speakerTypeSelect = document.getElementById('speaker-type-select');
    var micCalSelect = document.getElementById('mic-cal-select');
    var measureBtn = document.getElementById('measure-btn');
    var verifyBtn = document.getElementById('verify-btn');
    var deployBtn = document.getElementById('deploy-btn');
    var statusEl = document.getElementById('cal-status');
    var bandsListEl = document.getElementById('bands-list');
    var canvas = document.getElementById('freq-canvas');
    var ctx = canvas.getContext('2d');
    var overlay = document.getElementById('measure-overlay');
    var overlayStatus = document.getElementById('overlay-status');
    var overlayCancelBtn = document.getElementById('overlay-cancel-btn');
    var fleetBody = document.getElementById('fleet-body');
    var fleetSelectAll = document.getElementById('fleet-select-all');
    var fleetMeasureBtn = document.getElementById('fleet-measure-btn');
    var fleetMeasureDeployBtn = document.getElementById('fleet-measure-deploy-btn');
    var fleetStatusEl = document.getElementById('fleet-status');

    var cancelled = false;

    // ------------------------------------------------------------------
    // Core: one function to measure + save a speaker
    // ------------------------------------------------------------------

    function measureAndSave(speaker) {
        // Look up the speaker type: prefer the top-level dropdown, fall back to fleet table
        var speakerType = speakerTypeSelect.value || 'full-range';
        var typeSelect = fleetBody.querySelector('.fleet-type[data-speaker="' + speaker + '"]');
        if (typeSelect) {
            speakerType = typeSelect.value;
        }

        return new Promise(function(resolve, reject) {
            showOverlay('Measuring ' + speaker + '...');

            fetch('/api/calibration/measure', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    speaker: speaker,
                    speaker_type: speakerType,
                    mic_cal_file: micCalSelect.value || '',
                }),
            })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (data.error) { reject(data.error); return; }
                pollUntilDone(resolve, reject);
            })
            .catch(reject);
        })
        .then(function() {
            if (cancelled) return Promise.reject('Cancelled');
            showOverlay('Saving ' + speaker + '...');
            return fetch('/api/calibration/save', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({speaker: speaker, enabled: true}),
            });
        })
        .then(function(r) { return r ? r.json() : null; });
    }

    function deploySpeaker(speaker) {
        showOverlay('Deploying ' + speaker + '...');
        return fetch('/api/calibration/deploy', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({speaker: speaker}),
        }).then(function(r) { return r.json(); });
    }

    function pollUntilDone(resolve, reject) {
        var timer = setInterval(function() {
            if (cancelled) {
                clearInterval(timer);
                reject('Cancelled');
                return;
            }
            fetch('/api/calibration/status')
                .then(function(r) { return r.json(); })
                .then(function(d) {
                    overlayStatus.textContent = d.status || 'Working...';
                    if (d.state === 'done') {
                        clearInterval(timer);
                        if (d.result) {
                            drawFrequencyResponse(d.result);
                            renderBands(d.result.bands || []);
                        }
                        resolve();
                    } else if (d.state === 'error' || d.state === 'idle') {
                        clearInterval(timer);
                        reject(d.error || 'Measurement failed');
                    }
                });
        }, 1000);
    }

    // ------------------------------------------------------------------
    // Overlay
    // ------------------------------------------------------------------

    function showOverlay(msg) {
        overlayStatus.textContent = msg;
        overlay.style.display = '';
    }

    function hideOverlay() {
        overlay.style.display = 'none';
    }

    overlayCancelBtn.addEventListener('click', function() {
        cancelled = true;
        fetch('/api/calibration/cancel', {method: 'POST'}).catch(function() {});
        hideOverlay();
        setStatus('Cancelled', '');
        loadFleetStatus();
    });

    // ------------------------------------------------------------------
    // Single measurement
    // ------------------------------------------------------------------

    speakerTypeSelect.addEventListener('change', function() {
        var speaker = speakerSelect.value;
        if (!speaker) return;

        // Sync fleet table dropdown
        var typeSelect = fleetBody.querySelector('.fleet-type[data-speaker="' + speaker + '"]');
        if (typeSelect) {
            typeSelect.value = speakerTypeSelect.value;
        }

        // Persist to backend
        fetch('/api/calibration/speaker-type', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({speaker: speaker, speaker_type: speakerTypeSelect.value}),
        });
    });

    measureBtn.addEventListener('click', function() {
        var speaker = speakerSelect.value;
        if (!speaker) return;
        cancelled = false;

        measureAndSave(speaker)
            .then(function() {
                hideOverlay();
                deployBtn.disabled = false;
                setStatus('✓ Measured and saved', '');
                updateFleetAverages();
                loadFleetStatus();
            })
            .catch(function(err) {
                hideOverlay();
                if (err !== 'Cancelled') setStatus('Error: ' + err, 'error');
            });
    });

    verifyBtn.addEventListener('click', function() {
        var speaker = speakerSelect.value;
        if (!speaker) return;
        cancelled = false;

        showOverlay('Verifying ' + speaker + '...');
        fetch('/api/calibration/verify', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                speaker: speaker,
                speaker_type: speakerTypeSelect.value || 'full-range',
                mic_cal_file: micCalSelect.value || '',
            }),
        })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.error) {
                hideOverlay();
                setStatus('Error: ' + data.error, 'error');
                return;
            }
            // Poll until done
            var timer = setInterval(function() {
                if (cancelled) { clearInterval(timer); return; }
                fetch('/api/calibration/status')
                    .then(function(r) { return r.json(); })
                    .then(function(d) {
                        overlayStatus.textContent = d.status || 'Verifying...';
                        if (d.state === 'done') {
                            clearInterval(timer);
                            hideOverlay();
                            if (d.result) {
                                drawFrequencyResponse(d.result);
                                renderBands(d.result.bands || []);
                            }
                            setStatus('✓ Verification complete', '');
                        } else if (d.state === 'error') {
                            clearInterval(timer);
                            hideOverlay();
                            setStatus('Error: ' + (d.error || 'Verification failed'), 'error');
                        }
                    });
            }, 1000);
        })
        .catch(function(err) {
            hideOverlay();
            setStatus('Error: ' + err, 'error');
        });
    });

    // ------------------------------------------------------------------
    // Deploy
    // ------------------------------------------------------------------

    deployBtn.addEventListener('click', function() {
        var speaker = speakerSelect.value;
        if (!speaker) return;

        deploySpeaker(speaker)
            .then(function(data) {
                hideOverlay();
                if (data && data.error) {
                    setStatus('Deploy failed: ' + data.error, 'error');
                } else if (data && data.level_warning) {
                    // A clamped volCal means the measurement is out of range,
                    // not that the speaker needs ±10 dB. Writing it silently is
                    // what hid the "+10 on LFE, -10 on fronts" bug for weeks.
                    setStatus('✓ Deployed to ' + speaker + ' — ⚠ '
                              + data.level_warning, 'warn');
                } else {
                    setStatus('✓ Deployed to ' + speaker, '');
                }
            })
            .catch(function(err) {
                hideOverlay();
                setStatus('Deploy failed: ' + err, 'error');
            });
    });

    // ------------------------------------------------------------------
    // Fleet batch
    // ------------------------------------------------------------------

    fleetMeasureBtn.addEventListener('click', function() {
        runFleet(getSelectedSpeakers(), false);
    });

    fleetMeasureDeployBtn.addEventListener('click', function() {
        runFleet(getSelectedSpeakers(), true);
    });


    function runFleet(speakers, autoDeploy) {
        if (speakers.length === 0) return;
        cancelled = false;
        var total = speakers.length;
        var idx = 0;

        function next() {
            if (cancelled || idx >= total) {
                hideOverlay();
                fleetStatusEl.textContent = cancelled
                    ? 'Cancelled (' + idx + '/' + total + ')'
                    : '✓ Done (' + total + ' speakers)';
                setStatus(fleetStatusEl.textContent, '');
                loadFleetStatus();
                return;
            }
            var speaker = speakers[idx];
            idx++;

            speakerSelect.value = speaker;
            showOverlay('Measuring ' + speaker + ' (' + idx + '/' + total + ')...');

            measureAndSave(speaker)
                .then(function() {
                    if (autoDeploy && !cancelled) {
                        return deploySpeaker(speaker);
                    }
                })
                .then(function() { next(); })
                .catch(function(err) {
                    if (err === 'Cancelled') { next(); return; }
                    // Skip this speaker on error, continue
                    fleetStatusEl.textContent = speaker + ': ' + err + ' — skipped';
                    next();
                });
        }

        next();
    }

    // ------------------------------------------------------------------
    // Fleet table
    // ------------------------------------------------------------------

    var FLEET_CAL_CACHE_KEY = 'nimrum_cal_fleet_data';
    var fleetCalCacheStatus = document.getElementById('fleet-cal-cache-status');
    var refreshFleetCalBtn = document.getElementById('refresh-fleet-cal-btn');

    refreshFleetCalBtn.addEventListener('click', function() {
        loadFleetStatus();
    });

    function loadFleetStatus() {
        fleetCalCacheStatus.textContent = 'Loading...';
        fetch('/api/calibration/profiles')
            .then(function(r) { return r.json(); })
            .then(function(data) {
                var speakers = data.speakers || [];
                renderFleetTable(speakers);
                saveFleetCalCache(speakers);
                fleetCalCacheStatus.textContent = speakers.length + ' speakers (updated just now)';
            })
            .catch(function() {
                fleetCalCacheStatus.textContent = 'Failed to load';
            });
    }

    function loadCachedFleetCal() {
        try {
            var cached = localStorage.getItem(FLEET_CAL_CACHE_KEY);
            if (!cached) return false;
            var data = JSON.parse(cached);
            var speakers = data.speakers || [];
            if (speakers.length === 0) return false;
            renderFleetTable(speakers);
            var age = formatFleetCalCacheAge(data.timestamp);
            fleetCalCacheStatus.textContent = speakers.length + ' speakers (cached ' + age + ')';
            return true;
        } catch (e) {
            return false;
        }
    }

    function saveFleetCalCache(speakers) {
        try {
            var data = { speakers: speakers, timestamp: Date.now() };
            localStorage.setItem(FLEET_CAL_CACHE_KEY, JSON.stringify(data));
        } catch (e) {}
    }

    function formatFleetCalCacheAge(timestamp) {
        var diff = Date.now() - timestamp;
        var minutes = Math.floor(diff / 60000);
        if (minutes < 1) return 'just now';
        if (minutes < 60) return minutes + 'min ago';
        var hours = Math.floor(minutes / 60);
        if (hours < 24) return hours + 'h ago';
        var days = Math.floor(hours / 24);
        return days + 'd ago';
    }

    function renderFleetTable(speakers) {
        var html = '';

        for (var i = 0; i < speakers.length; i++) {
            var sp = speakers[i];
            var profile = '';
            var eqStatus = '';

            // Profile on TX
            if (sp.hasProfile) {
                profile = sp.bands + ' bands';
            } else {
                profile = '—';
            }

            // Type dropdown
            var spType = sp.speakerType || 'full-range';
            var typeHtml = '<select class="fleet-type" data-speaker="' + sp.name + '">'
                + '<option value="full-range"' + (spType === 'full-range' ? ' selected' : '') + '>Full-range</option>'
                + '<option value="subwoofer"' + (spType === 'subwoofer' ? ' selected' : '') + '>Subwoofer</option>'
                + '</select>';

            // EQ Status — clickable when deployed
            if (sp.deployedOnRx && sp.rxEnabled) {
                eqStatus = '<span class="eq-status eq-active eq-clickable" '
                    + 'data-speaker="' + sp.name + '" data-enable="false">'
                    + '✓ active</span>';
            } else if (sp.deployedOnRx && !sp.rxEnabled) {
                eqStatus = '<span class="eq-status eq-disabled eq-clickable" '
                    + 'data-speaker="' + sp.name + '" data-enable="true">'
                    + '✗ disabled</span>';
            } else if (sp.hasProfile) {
                eqStatus = '<span class="eq-status eq-none">not deployed</span>';
            } else {
                eqStatus = '<span class="eq-status eq-none">—</span>';
            }

            html += '<tr>';
            html += '<td><input type="checkbox" class="fleet-check" data-speaker="' + sp.name + '"></td>';
            html += '<td>' + sp.name + '</td>';
            html += '<td>' + (sp.location || '') + '</td>';
            html += '<td>' + typeHtml + '</td>';
            html += '<td>' + profile + '</td>';
            html += '<td>' + eqStatus + '</td>';

            // CPU load
            var cpuHtml = '—';
            if (sp.cpuLoad >= 0) {
                var cpuClass = sp.cpuLoad > 0.2 ? 'eq-disabled' : (sp.cpuLoad > 0.1 ? '' : 'eq-active');
                cpuHtml = '<span class="eq-status ' + cpuClass + '">' + sp.cpuLoad.toFixed(2) + '</span>';
            }
            html += '<td>' + cpuHtml + '</td>';
            html += '</tr>';
        }
        fleetBody.innerHTML = html || '<tr><td colspan="7" style="color:var(--muted)">No speakers</td></tr>';
        restoreFleetSelection();
        updateFleetButtons();
    }

    function getSelectedSpeakers() {
        var checks = document.querySelectorAll('.fleet-check:checked');
        var names = [];
        for (var i = 0; i < checks.length; i++) {
            names.push(checks[i].getAttribute('data-speaker'));
        }
        return names;
    }

    function saveFleetSelection() {
        var selected = getSelectedSpeakers();
        try {
            localStorage.setItem('nimrum_cal_fleet_selection', JSON.stringify(selected));
        } catch (e) {}
    }

    function restoreFleetSelection() {
        try {
            var saved = localStorage.getItem('nimrum_cal_fleet_selection');
            if (!saved) return;
            var selected = JSON.parse(saved);
            if (!Array.isArray(selected) || selected.length === 0) return;
            var checks = document.querySelectorAll('.fleet-check');
            for (var i = 0; i < checks.length; i++) {
                var name = checks[i].getAttribute('data-speaker');
                checks[i].checked = selected.indexOf(name) >= 0;
            }
            updateFleetButtons();
        } catch (e) {}
    }

    function updateFleetButtons() {
        var n = getSelectedSpeakers().length;
        fleetMeasureBtn.disabled = n === 0;
        fleetMeasureDeployBtn.disabled = n === 0;
    }

    fleetSelectAll.addEventListener('change', function() {
        var checks = document.querySelectorAll('.fleet-check');
        for (var i = 0; i < checks.length; i++) checks[i].checked = fleetSelectAll.checked;
        updateFleetButtons();
        saveFleetSelection();
    });

    fleetBody.addEventListener('change', function(e) {
        if (e.target.classList.contains('fleet-check')) {
            updateFleetButtons();
            saveFleetSelection();
        }
    });

    // EQ Status toggle — click active/disabled to toggle
    fleetBody.addEventListener('click', function(e) {
        var el = e.target;
        if (!el.classList.contains('eq-clickable')) return;

        var speaker = el.getAttribute('data-speaker');
        var enable = el.getAttribute('data-enable') === 'true';

        // Visual feedback while toggling
        el.textContent = '⏳ ...';
        el.className = 'eq-status eq-none';

        fetch('/api/calibration/enable', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({speaker: speaker, enable: enable}),
        })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.error) {
                setStatus('Toggle failed: ' + data.error, 'error');
            } else {
                setStatus('EQ ' + (enable ? 'enabled' : 'disabled') + ' on ' + speaker, '');
            }
            loadFleetStatus();
        })
        .catch(function(err) {
            setStatus('Toggle failed: ' + err, 'error');
            loadFleetStatus();
        });
    });

    // Speaker type dropdown — persist selection
    fleetBody.addEventListener('change', function(e) {
        var el = e.target;
        if (!el.classList.contains('fleet-type')) return;

        var speaker = el.getAttribute('data-speaker');
        var speakerType = el.value;

        fetch('/api/calibration/speaker-type', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({speaker: speaker, speaker_type: speakerType}),
        })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.error) {
                setStatus('Type set failed: ' + data.error, 'error');
            } else {
                setStatus('Type set to ' + speakerType + ' for ' + speaker, '');
            }
        })
        .catch(function(err) {
            setStatus('Type set failed: ' + err, 'error');
        });
    });

    // ------------------------------------------------------------------
    // Speaker selection
    // ------------------------------------------------------------------

    speakerSelect.addEventListener('change', function() {
        measureBtn.disabled = !speakerSelect.value;
        verifyBtn.disabled = !speakerSelect.value;
        var speaker = speakerSelect.value;
        if (!speaker) {
            drawEmptyChart();
            renderBands([]);
            deployBtn.disabled = true;
            setStatus('Idle', '');
            return;
        }
        sessionStorage.setItem('cal_speaker', speaker);

        // Sync type dropdown from fleet table
        var typeSelect = fleetBody.querySelector('.fleet-type[data-speaker="' + speaker + '"]');
        if (typeSelect) {
            speakerTypeSelect.value = typeSelect.value;
        }

        // Load saved profile from backend
        fetch('/api/calibration/profile/' + encodeURIComponent(speaker))
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (data.exists && data.bands && data.bands.length > 0) {
                    renderBands(data.bands);
                    deployBtn.disabled = false;
                    setStatus('Saved profile (' + data.bands.length + ' bands)', '');
                    if (data.plot) {
                        // Sync speaker type from stored plot data
                        if (data.plot.speaker_type) {
                            speakerTypeSelect.value = data.plot.speaker_type;
                        }
                        drawFrequencyResponse(data.plot);
                    } else {
                        drawEmptyChart();
                    }
                } else {
                    renderBands([]);
                    deployBtn.disabled = true;
                    setStatus('No profile yet', '');
                    drawEmptyChart();
                }
            })
            .catch(function() {
                drawEmptyChart();
                renderBands([]);
                deployBtn.disabled = true;
            });
    });

    // ------------------------------------------------------------------
    // Speaker list population
    // ------------------------------------------------------------------

    function fetchStatus() {
        fetch('/api/status')
            .then(function(r) { return r.json(); })
            .then(function(data) {
                var clients = data.clients || [];
                var saved = sessionStorage.getItem('cal_speaker');
                var current = speakerSelect.value || saved || '';
                var html = '<option value="">— select —</option>';
                for (var i = 0; i < clients.length; i++) {
                    var c = clients[i];
                    var name = c.name || ('Client ' + i);
                    var label = c.location ? c.location + ' (' + name + ')' : name;
                    html += '<option value="' + name + '">' + label + '</option>';
                }
                speakerSelect.innerHTML = html;
                if (current) speakerSelect.value = current;
                measureBtn.disabled = !speakerSelect.value;
                verifyBtn.disabled = !speakerSelect.value;
            })
            .catch(function() {});
    }

    // ------------------------------------------------------------------
    // Status display
    // ------------------------------------------------------------------

    function setStatus(text, cls) {
        statusEl.textContent = text;
        statusEl.className = 'cal-status' + (cls ? ' ' + cls : '');
    }

    // ------------------------------------------------------------------
    // EQ Bands display
    // ------------------------------------------------------------------

    function renderBands(bands) {
        if (!bands || bands.length === 0) {
            bandsListEl.innerHTML = '';
            return;
        }
        var html = '';
        for (var i = 0; i < bands.length; i++) {
            var b = bands[i];
            html += '<div class="band-card">';
            html += '<span class="band-type">' + b.type + '</span><br>';
            html += b.freq.toFixed(0) + ' Hz, ';
            html += (b.gain >= 0 ? '+' : '') + b.gain.toFixed(1) + ' dB, ';
            html += 'Q=' + b.q.toFixed(2);
            html += '</div>';
        }
        bandsListEl.innerHTML = html;
    }

    // ------------------------------------------------------------------
    // Frequency Response Chart
    // ------------------------------------------------------------------

    function drawEmptyChart() {
        drawFrequencyResponse({freqs: [], magnitude_db: [], corrected_db: []});
    }

    // Cache the reference the backend actually used, so the displayed number
    // matches what gets written to volCal. Deliberately not an average over
    // all speakers computed here — a band-limited speaker must not move it.
    var _fleetAvgLevel = null;
    var _volCalByName = {};
    var _clampedNames = [];

    function updateFleetAverages() {
        fetch('/api/calibration/levels')
            .then(function(r) { return r.json(); })
            .then(function(data) {
                var info = data.level_info;
                _volCalByName = data.suggested_adjustments || {};
                _clampedNames = (info && info.clamped) || [];
                _fleetAvgLevel = (info && info.reference_level != null)
                    ? info.reference_level
                    : null;
            })
            .catch(function() {});
    }

    function drawFrequencyResponse(data) {
        var dpr = window.devicePixelRatio || 1;
        var w = canvas.clientWidth;
        var h = canvas.clientHeight || 360;
        canvas.width = w * dpr;
        canvas.height = h * dpr;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

        var pad = {left: 50, right: 20, top: 20, bottom: 35};
        var plotW = w - pad.left - pad.right;
        var plotH = h - pad.top - pad.bottom;

        var fMin = 20, fMax = 20000;
        // Zoom plot to relevant range for subwoofers
        if (data.speaker_type === 'subwoofer') {
            fMin = 15;
            fMax = 250;
        }
        var logMin = Math.log10(fMin);
        var logMax = Math.log10(fMax);

        // Auto-scale Y axis (only from data within visible frequency range)
        var dbMin = -20, dbMax = 20;
        if (data.magnitude_db && data.magnitude_db.length > 0 && data.freqs) {
            var visibleMag = [];
            var visibleCorr = [];
            for (var vi = 0; vi < data.freqs.length; vi++) {
                if (data.freqs[vi] >= fMin && data.freqs[vi] <= fMax) {
                    visibleMag.push(data.magnitude_db[vi]);
                    if (data.corrected_db) visibleCorr.push(data.corrected_db[vi]);
                }
            }
            var allVals = visibleMag.concat(visibleCorr);
            if (allVals.length > 0) {
                var dMin = Math.min.apply(null, allVals);
                var dMax = Math.max.apply(null, allVals);
                dbMin = Math.floor((dMin - 3) / 5) * 5;
                dbMax = Math.ceil((dMax + 3) / 5) * 5;
                if (dbMax - dbMin < 20) { var mid = (dbMax + dbMin) / 2; dbMin = mid - 10; dbMax = mid + 10; }
            }
        }

        function freqToX(f) { return pad.left + (Math.log10(f) - logMin) / (logMax - logMin) * plotW; }
        function dbToY(db) { return pad.top + (1 - (db - dbMin) / (dbMax - dbMin)) * plotH; }

        // Background
        ctx.fillStyle = '#1e293b';
        ctx.fillRect(0, 0, w, h);

        // Grid
        ctx.strokeStyle = '#334155';
        ctx.lineWidth = 0.5;
        ctx.font = '10px sans-serif';
        ctx.fillStyle = '#94a3b8';

        // Horizontal (dB)
        ctx.textAlign = 'right';
        for (var db = dbMin; db <= dbMax; db += 5) {
            var y = dbToY(db);
            ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(pad.left + plotW, y); ctx.stroke();
            ctx.fillText(db + ' dB', pad.left - 5, y + 3);
        }

        // Vertical (freq)
        ctx.textAlign = 'center';
        var ticks = [20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000];
        if (data.speaker_type === 'subwoofer') {
            ticks = [20, 30, 40, 50, 60, 80, 100, 120, 150, 200, 250];
        }
        for (var ti = 0; ti < ticks.length; ti++) {
            if (ticks[ti] < fMin || ticks[ti] > fMax) continue;
            var fx = freqToX(ticks[ti]);
            ctx.beginPath(); ctx.moveTo(fx, pad.top); ctx.lineTo(fx, pad.top + plotH); ctx.stroke();
            ctx.fillText(ticks[ti] >= 1000 ? (ticks[ti]/1000) + 'k' : ticks[ti], fx, pad.top + plotH + 14);
        }

        // 0dB line
        ctx.setLineDash([4, 4]);
        ctx.strokeStyle = '#64748b';
        ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(pad.left, dbToY(0)); ctx.lineTo(pad.left + plotW, dbToY(0)); ctx.stroke();
        ctx.setLineDash([]);

        // Curves
        if (data.freqs && data.magnitude_db) drawCurve(data.freqs, data.magnitude_db, '#3b82f6', freqToX, dbToY, fMin, fMax, dbMin, dbMax);
        if (data.freqs && data.corrected_db) drawCurve(data.freqs, data.corrected_db, '#22c55e', freqToX, dbToY, fMin, fMax, dbMin, dbMax);
        // Expected curve (from saved profile, shown in verify mode as dashed)
        if (data.freqs && data.expected_db && data.expected_db.length > 0) {
            ctx.setLineDash([6, 4]);
            drawCurve(data.freqs, data.expected_db, '#f59e0b', freqToX, dbToY, fMin, fMax, dbMin, dbMax);
            ctx.setLineDash([]);
        }

        // Legend
        ctx.font = '11px sans-serif';
        var lx = pad.left + 10, ly = pad.top + 14;
        ctx.fillStyle = '#3b82f6'; ctx.fillRect(lx, ly - 8, 12, 3);
        ctx.fillStyle = '#94a3b8'; ctx.textAlign = 'left'; ctx.fillText('Measured', lx + 16, ly);
        ctx.fillStyle = '#22c55e'; ctx.fillRect(lx, ly + 8, 12, 3);
        ctx.fillStyle = '#94a3b8'; ctx.fillText('Corrected', lx + 16, ly + 16);
        if (data.expected_db && data.expected_db.length > 0) {
            ctx.fillStyle = '#f59e0b'; ctx.fillRect(lx, ly + 24, 12, 3);
            ctx.fillStyle = '#94a3b8'; ctx.fillText('Expected', lx + 16, ly + 32);
        }

        // Measured level (top-right), relative to the volCal reference.
        // Read-only: volCal is derived here, but sub-vs-mains level is the
        // user's per-layout slider in the Levels tab, not a control here.
        if (data.relative_level_db != null) {
            ctx.font = '11px monospace';
            ctx.textAlign = 'right';
            ctx.fillStyle = '#e2e8f0';
            var rx = pad.left + plotW - 5;
            var ry = pad.top + 14;
            var lvl = data.relative_level_db;
            var lvlRel = lvl - (_fleetAvgLevel != null ? _fleetAvgLevel : lvl);
            var sign = lvlRel >= 0 ? '+' : '';
            ctx.fillText('Level: ' + sign + lvlRel.toFixed(1) + ' dB (vs ref)', rx, ry);

            // Which band the level was measured in — the number is only
            // comparable against speakers measured in the same band.
            var isSub = data.speaker_type === 'subwoofer';
            ctx.fillStyle = '#94a3b8';
            ctx.fillText(isSub ? 'band: 30-100 Hz' : 'band: 200 Hz-8 kHz',
                         rx, ry + 14);

            var name = data.speaker || speakerSelect.value;
            if (name && _volCalByName[name] != null) {
                var vc = _volCalByName[name];
                var clamped = _clampedNames.indexOf(name) >= 0;
                // volCal is stored in wire volume steps, 0.5 dB each — same
                // unit and the same display convention as the Levels tab.
                var vcDb = vc * VOL_DB_PER_STEP;
                ctx.fillStyle = clamped ? '#f59e0b' : '#94a3b8';
                ctx.fillText('volCal: ' + (vc >= 0 ? '+' : '') + vc
                             + ' (' + (vcDb >= 0 ? '+' : '')
                             + vcDb.toFixed(1) + ' dB)'
                             + (clamped ? ' (clamped — suspect)' : ''),
                             rx, ry + 28);
            }

            if (isSub) {
                ctx.fillStyle = '#94a3b8';
                ctx.fillText('sub level: set per layout in the Levels tab',
                             rx, ry + 42);
            }
        }
    }

    function drawCurve(freqs, values, color, freqToX, dbToY, fMin, fMax, dbMin, dbMax) {
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        var started = false;
        for (var i = 0; i < freqs.length; i++) {
            if (freqs[i] < fMin || freqs[i] > fMax) continue;
            var x = freqToX(freqs[i]);
            var y = dbToY(Math.max(dbMin - 5, Math.min(dbMax + 5, values[i])));
            if (!started) { ctx.moveTo(x, y); started = true; } else { ctx.lineTo(x, y); }
        }
        ctx.stroke();
    }

    // ------------------------------------------------------------------
    // Init
    // ------------------------------------------------------------------

    function fetchMicFiles() {
        fetch('/api/calibration/mic-files')
            .then(function(r) { return r.json(); })
            .then(function(data) {
                var files = data.files || [];
                var html = '<option value="">— none —</option>';
                for (var i = 0; i < files.length; i++) {
                    html += '<option value="' + files[i] + '">' + files[i] + '</option>';
                }
                micCalSelect.innerHTML = html;
                // Restore selection
                var savedMic = sessionStorage.getItem('cal_mic');
                if (savedMic) micCalSelect.value = savedMic;
            })
            .catch(function() {});
    }

    micCalSelect.addEventListener('change', function() {
        sessionStorage.setItem('cal_mic', micCalSelect.value);
    });

    fetchStatus();
    fetchMicFiles();
    updateFleetAverages();
    setInterval(fetchStatus, 5000);

    // Load fleet table from cache immediately, then refresh from backend
    if (!loadCachedFleetCal()) {
        loadFleetStatus();
    }

    // Restore selection and show data
    var saved = sessionStorage.getItem('cal_speaker');
    if (saved) {
        setTimeout(function() {
            speakerSelect.value = saved;
            speakerSelect.dispatchEvent(new Event('change'));
        }, 1000);
    } else {
        drawEmptyChart();
    }

    // ------------------------------------------------------------------
    // Import EQ Coefficients
    // ------------------------------------------------------------------

    var importTextarea = document.getElementById('import-textarea');
    var importPreviewBtn = document.getElementById('import-preview-btn');
    var importStatusEl = document.getElementById('import-status');
    var _importedBands = null;

    function parseImportText(text) {
        var lines = text.trim().split('\n');
        var bands = [];
        for (var i = 0; i < lines.length; i++) {
            var line = lines[i].trim();
            if (!line || line[0] === '#') continue;
            var parts = line.split(/[,\t]+/).map(function(s) { return s.trim(); });
            if (parts.length < 4) continue;

            var type = parts[0];
            if (type.toLowerCase() === 'raw') {
                // Raw: b0, b1, b2, a1, a2
                if (parts.length < 6) continue;
                bands.push({
                    type: 'Raw',
                    b0: parseFloat(parts[1]),
                    b1: parseFloat(parts[2]),
                    b2: parseFloat(parts[3]),
                    a1: parseFloat(parts[4]),
                    a2: parseFloat(parts[5]),
                });
            } else {
                // Parametric: type, freq, gain, q
                bands.push({
                    type: type,
                    freq: parseFloat(parts[1]),
                    gain: parseFloat(parts[2]),
                    q: parseFloat(parts[3]),
                });
            }
        }
        return bands;
    }

    importPreviewBtn.addEventListener('click', function() {
        var text = importTextarea.value;
        var bands = parseImportText(text);
        if (bands.length === 0) {
            importStatusEl.textContent = 'No valid bands found';
            importStatusEl.className = 'cal-status error';
            return;
        }

        _importedBands = bands;
        importStatusEl.textContent = bands.length + ' band(s)...';
        importStatusEl.className = 'cal-status';

        // Send to backend for frequency response preview
        fetch('/api/calibration/preview-import', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({bands: bands}),
        })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.error) {
                importStatusEl.textContent = 'Error: ' + data.error;
                importStatusEl.className = 'cal-status error';
                return;
            }
            drawFrequencyResponse(data);
            renderBands(bands);
            deployBtn.disabled = !speakerSelect.value;
            importStatusEl.textContent = bands.length + ' band(s)';
            importStatusEl.className = 'cal-status';
        })
        .catch(function(err) {
            importStatusEl.textContent = 'Error: ' + err;
            importStatusEl.className = 'cal-status error';
        });
    });

    // Override deploy button to handle imported bands when they're active
    var _originalDeployHandler = null;

    deployBtn.addEventListener('click', function(e) {
        if (!_importedBands) return;  // Let original handler work for measured

        var speaker = speakerSelect.value;
        if (!speaker) return;

        e.stopImmediatePropagation();
        deployBtn.disabled = true;
        setStatus('Deploying imported EQ...', '');

        fetch('/api/calibration/deploy-import', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({speaker: speaker, bands: _importedBands}),
        })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.error) {
                setStatus('Deploy failed: ' + data.error, 'error');
            } else {
                setStatus('✓ Deployed to ' + speaker, '');
                _importedBands = null;
            }
            deployBtn.disabled = false;
            loadFleetStatus();
        })
        .catch(function(err) {
            setStatus('Deploy failed: ' + err, 'error');
            deployBtn.disabled = false;
        });
    }, true);  // Use capture to run before original handler

    // Clear imported state when a new measurement is started
    var _origMeasureClick = measureBtn.onclick;
    measureBtn.addEventListener('click', function() {
        _importedBands = null;
        importStatusEl.textContent = '';
    });

})();
