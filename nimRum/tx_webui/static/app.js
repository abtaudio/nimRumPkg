(function() {
    'use strict';

    var ALSA_BUFFER_US = 40000; // Fallback if RX hasn't reported yet

    var clientGridEl = document.getElementById('client-grid');

    var rxState = window.nimRumRxState;

    // Network ring: 4 levels (0=poor, 1=marginal, 2=good, 3=excellent)
    var netRingColors = rxState.NET_RING_COLORS;
    var netRingNames = rxState.NET_RING_NAMES;

    // Wrap value in red if it's preventing "best" level
    // Three states, not two. `bad` is red, `warn` is amber. Red used to be the only
    // way to say anything at all, which made "just past the target" look identical to
    // "grossly out".
    function val(text, bad, warn) {
        if (bad) return '<span class="metric-val metric-bad">' + text + '</span>';
        if (warn) return '<span class="metric-val metric-warn">' + text + '</span>';
        return '<span class="metric-val">' + text + '</span>';
    }

    /* Cumulative counters need "is it happening now", not "has it ever happened".
     *
     * xferPad/haltedFill/srcUnderrun/srcOverflow are all monotonic totals that TX
     * accumulates and never clears until TX itself restarts. Colouring them on
     * `> 0` meant one transient hours ago left the field red for the life of the
     * process - which tells the reader nothing and trains them to ignore the colour.
     * Diff against the previous poll instead: amber only while the count is actually
     * moving. The total stays on screen as the record.
     */
    /* Colour a value by the grade IT ALONE implies, from rxstate's NET_LIMITS. So a
     * red Signal is exactly why the ring is red, and the legend needs no separate
     * rule. Grade 3 renders plain: the field is not holding the device back. */
    function netVal(text, kind, value) {
        var g = rxState.netFieldGrade(kind, value);
        if (g >= 3) return '<span class="metric-val">' + text + '</span>';
        return '<span class="metric-val" style="color:' + netRingColors[g]
             + ';font-weight:600">' + text + '</span>';
    }

    var prevCounters = {};
    function rising(key, value) {
        var prev = prevCounters[key];
        prevCounters[key] = value;
        return (prev !== undefined) && (value > prev);
    }

    function poll() {
        fetch('/api/status')
            .then(function(r) { return r.json(); })
            .then(render)
            .catch(function() { render({txOnline: false}); });
    }

    function render(data) {
        /* Internal build or public one. TX reports it, so the UI cannot disagree with
         * the data: a public build zeroes the internal per-client fields, and these
         * rows would otherwise render as a wall of zeros. Filter Window, Filter
         * Margin, Actuator err and Loop margin are all "for us" rather than "for a
         * listener" - nothing a user can act on. nimRumLib/inc/libNimRumPublicBuild.h.
         */
        var fullDiag = (data.fullDiagnostics !== false);
        if (!data.txOnline) {
            clientGridEl.innerHTML = '<div class="client-card tx-card paused"><div class="client-header"><span class="client-name">TX</span><span class="client-state">Offline</span></div></div>';
            return;
        }

        var latUs = data.latency_us || 0;

        var clients = data.clients || [];
        var connected = clients.filter(function(c) { return !c.paused; }).length;

        var html = '';

        // SRC cards (all known sources, active one highlighted)
        var hasSource = (data.activeSourceId >= 0);
        var sources = data.sources || [];
        var srcLink = data.srcLinkStats || {};

        for (var si = 0; si < sources.length; si++) {
            var src = sources[si];
            var isActive = (src.id === data.activeSourceId);
            var srcSig = isActive ? (srcLink.signal || 0) : 0;
            var srcNetGrade = 3;
            if (srcSig) {
                if (srcSig < -80) srcNetGrade = 0;
                else if (srcSig < -70) srcNetGrade = 1;
                else if (srcSig < -60) srcNetGrade = 2;
            }
            var srcActiveClass = isActive ? '' : ' paused';
            html += '<div class="client-card src-card' + srcActiveClass + '">';
            html += '<div class="client-header">';
            html += '<span class="client-name">SRC: ' + (src.name || 'Source ' + src.id) + '</span>';
            if (isActive) {
                html += '<span class="client-state net-ring-badge" style="background:' + netRingColors[srcNetGrade] + '">Active</span>';
            } else {
                html += '<span class="client-state">Idle</span>';
            }
            html += '</div>';
            if (isActive) {
                html += '<div class="client-metrics">';
                html += '<span>Link</span>' + val(srcSig ? 'WiFi' : 'Wired/N/A', false);
                html += '<span>Signal</span>' + val(srcSig ? srcSig + ' dBm' : 'N/A', srcSig && srcSig < -60);
                html += '<span>Quality</span>' + val(srcSig ? (srcLink.quality || 0) + '/70' : 'N/A', false);
                html += '<span>TX Retries</span>' + val(srcSig ? (srcLink.txRetries || 0) : 'N/A', (srcLink.txRetries || 0) > 0);
                html += '<span>RX Errors</span>' + val(srcSig ? (srcLink.rxErrors || 0) : 'N/A', (srcLink.rxErrors || 0) > 0);

                // Latency is shown as two legs and their sum, never folded into one.
                // The TX->RX budget is sized from how good that link is; the source
                // FIFO is additive and depends on the source's own transport. The
                // total is what a listener actually hears, and matters for lip sync.
                var srcFifoUs = data.srcFifoLatencyUs || 0;
                var totalUs = data.totalLatencyUs || (latUs + srcFifoUs);
                html += '<span>FIFO depth</span>' + val((data.srcFifoCount || 0) + ' pkt', false);
                html += '<span>SRC buffer</span>' + val((srcFifoUs / 1000).toFixed(1) + ' ms', false);
                html += '<span>TX&rarr;RX latency</span>' + val((latUs / 1000).toFixed(0) + ' ms', false);
                html += '<span>Total latency</span>' + val((totalUs / 1000).toFixed(1) + ' ms', false);

                // Underrun injects silence, overflow throws audio away, and both can
                // happen in the same second. Neither was visible anywhere before.
                /* Totals since TX started - reset only in nimRumAudioSourceRx_init(),
                 * so never during a session. Note a SOURCE SWITCH necessarily adds
                 * underruns: the switch resets the FIFO and clears the pre-fill flag,
                 * so every read until the cushion is rebuilt counts one. A non-zero
                 * total after changing source is expected, not a fault. Coloured only
                 * while still rising. */
                var srcUnder = data.srcUnderrunCount || 0;
                var srcOver = data.srcOverflowCount || 0;
                html += '<span title="Total since TX started, never reset during a session. A source switch always adds some: it resets the FIFO and the pre-fill must be rebuilt, and every read until then counts an underrun. Coloured only while still rising.">Underruns (total)</span>'
                     + val(srcUnder, false, rising('srcUnder', srcUnder));
                html += '<span title="Total since TX started, never reset during a session. Overflow means the FIFO was full and captured audio was discarded. Coloured only while still rising.">Overflows (total)</span>'
                     + val(srcOver, false, rising('srcOver', srcOver));
                html += '</div>';
            }
            html += '</div>';
        }

        // If no sources at all
        if (sources.length === 0) {
            html += '<div class="client-card src-card paused">';
            html += '<div class="client-header">';
            html += '<span class="client-name">SRC</span>';
            html += '<span class="client-state">No sources</span>';
            html += '</div></div>';
        }

        // TX card
        var txLink = data.txLinkStats || {};
        var txSig = txLink.signal || 0;
        var txNetGrade = 3;
        if (txSig) {
            if (txSig < -80) txNetGrade = 0;
            else if (txSig < -70) txNetGrade = 1;
            else if (txSig < -60) txNetGrade = 2;
        }
        var txPpm = (data.txPPM || 0).toFixed(1);
        var txPausedClass = hasSource ? '' : ' paused';

        html += '<div class="client-card tx-card' + txPausedClass + '">';
        html += '<div class="client-header">';
        html += '<span class="client-name">TX</span>';
        if (hasSource) {
            html += '<span class="client-state net-ring-badge" style="background:' + netRingColors[txNetGrade] + '">' + netRingNames[txNetGrade] + '</span>';
        } else {
            html += '<span class="client-state">No source</span>';
        }
        html += '</div>';
        html += '<div class="client-metrics">';
        html += '<span>Source</span>' + val(hasSource ? (data.activeSourceName || 'Source ' + data.activeSourceId) : 'No source', !hasSource);
        /* The nimRumPkg version each source reports in its ping.
         *
         * Listed per source rather than for the active one because a source is
         * identified on the wire by sourceName while seenDevices is keyed by hostname,
         * and the two are not the same string. Worth showing at all because it is the
         * only confirmation TX gives that it accepted a source and which build it is
         * talking to — the thing you want when the source is one you wrote yourself.
         *
         * It is the PACKAGE version, not nimRumLib: a source links no nimRumLib. A
         * source built before 2026-09-15 reports nothing and reads as "unknown".
         */
        var srcVers = (data.seenDevices || []).filter(function(d) {
            return (d.roles & 2) !== 0;
        }).map(function(d) {
            var v = d.libVersion;
            return d.hostName + ' ' + (v && v !== '0.0.0' ? v : 'unknown');
        });
        if (srcVers.length) {
            html += '<span>Source pkg</span>' + val(srcVers.join(', '), false,
                                                    srcVers.join(',').indexOf('unknown') >= 0);
        }
        html += '<span>Source PPM</span>' + val(txPpm, false);
        html += '<span>Latency</span>' + val((latUs / 1000).toFixed(0) + ' ms', false);
        html += '<span>Clients</span>' + val(connected + ' / ' + clients.length, false);
        html += '<span>Link</span>' + val(txSig ? 'WiFi' : 'Wired', false);
        html += '<span>Signal</span>' + val(txSig ? txSig + ' dBm' : 'N/A', txSig && txSig < -60);
        html += '<span>Quality</span>' + val(txSig ? (txLink.quality || 0) + '/70' : 'N/A', false);
        html += '<span>TX Retries</span>' + val(txSig ? (txLink.txRetries || 0) : 'N/A', (txLink.txRetries || 0) > 0);
        html += '<span>RX Errors</span>' + val(txSig ? (txLink.rxErrors || 0) : 'N/A', (txLink.rxErrors || 0) > 0);
        html += '</div></div>';

        // Speakers heading
        html += '<h2 class="section-heading">Speakers</h2>';

        // RX cards
        for (var i = 0; i < clients.length; i++) {
            var c = clients[i];
            var lost = rxState.isLost(c);
            // A lost device is dimmed like a paused one: in both cases nothing
            // is coming back and every metric shown is stale.
            var pausedClass = (c.paused || lost) ? ' paused' : '';
            var name = c.location || c.name || ('Client ' + i);
            if (c.name && c.location) name += ' (' + c.name + ')';
            var state = rxState.stateName(c);
            var stateColor = rxState.stateColor(c);

            var loss = c.nqLossPermille || 0;
            var burstMs = c.nqBurstMs || 0;
            var jitP95 = c.nqJitterP95 || 0;
            var jitMax = c.nqJitterMax || 0;
            var jitP95Ms = (jitP95 / 1000).toFixed(1);
            var jitMaxMs = (jitMax / 1000).toFixed(1);
            var alsaBufUs = (c.alsaBufferMs || 0) > 0 ? c.alsaBufferMs * 1000 : ALSA_BUFFER_US;
            var minPossibleUs = (c.nqRecLatency || 0) + alsaBufUs;
            var minPossibleMs = (minPossibleUs / 1000).toFixed(0);
            var latencyExceeded = (latUs > 0) && (minPossibleUs > latUs);

            // How much slack the RX main loop has before an XRUN is unavoidable.
            // Both terms are already on the wire; comparing them by eye is what
            // hid one receiver's condition — it ran a 12-17 ms loop gap against a
            // 20 ms buffer, which is what produced the glitches, and no single
            // number showed it. Negative means the loop already overran the buffer.
            var loopMaxUs = c.rxLoopMaxUs || 0;
            var haveMargin = (c.alsaBufferMs || 0) > 0 && loopMaxUs > 0;
            var marginUs = alsaBufUs - loopMaxUs;
            var marginMs = (marginUs / 1000).toFixed(1);
            // Warn below half the buffer. Calibrated against the one confirmed
            // bad case rather than picked: a receiver glitched at a 12-17 ms loop gap
            // on a 20 ms buffer, i.e. 3-8 ms margin, and half the buffer (10 ms)
            // is the lowest threshold that flags all of that range. It is also
            // comfortably clear of the healthy fleet, which sits at 33-36 ms of a
            // 42 ms buffer, so it should not cry wolf.
            var marginBad = haveMargin && (marginUs < alsaBufUs / 2);

            var wifiSig = c.wifiSignal || 0;
            var wifiQual = c.wifiQuality || 0;
            var wifiRetries = c.wifiTxRetries || 0;
            var wifiErrors = c.wifiRxErrors || 0;
            var linkType = wifiSig ? 'WiFi' : 'Wired/N/A';
            var netGrade = rxState.netRingGrade(c);

            var coFiltStdDev = c.coFiltStdDevUs || 0;
            var avgTta = c.avgTtaUs || 0;
            var coStable = c.coStable || 0;
            var coFiltersFull = !!c.coFiltersFull;

            // Degrade sync color if latency exceeded. Not for a lost device:
            // its metrics are stale, so grey ("Lost") is the honest colour.
            if (latencyExceeded && !c.paused && !lost) {
                stateColor = '#ef4444';
            }

            // "Red" = anything preventing best level
            // Only mark coFiltStdDev/avgTta red if stable (past the filter gate)
            /* Two levels, matching the two lock limits, so the colour means something
             * a listener would recognise:
             *   <= 35 us  inside the design target (50 us pair over sqrt(2)) - plain
             *   35..70    amber: past the target, still comfortably inaudible. A pair
             *             within ~100 us is fine for most listening, which is why
             *             this is not red.
             *   > 70 us   red: outside the locked limit as well.
             * From nimRumLib 3.2.0 this number includes the gate band width and so
             * tracks the real acoustic error (~16-115 us across the fleet). Amber was
             * briefly 25, which made a device at 32 - inside the target - look alarming.
             * docs/sync-accuracy-investigation.md. */
            var coFiltStdDevBad  = (coStable >= 2) && coFiltStdDev > 70;
            var coFiltStdDevWarn = (coStable >= 2) && coFiltStdDev > 35;
            var avgTtaBad = (coStable >= 2) && avgTta > 35;
            // Filter status is red if not stable and RX is actively trying.
            // A lost device is not trying — it is not answering at all.
            var filterBad = (c.status >= 0 && coStable < 2 && !c.paused && !lost);

            // Signal, loss and retries are now coloured by netVal() from the single
            // NET_LIMITS table, so their thresholds live in rxstate.js and cannot
            // drift from the grade. Only these two remain local, because neither
            // feeds the grade: amber when non-zero, worth noticing but not a grade.
            var burstBad = burstMs > 0;
            var errBad = wifiErrors > 0;

            html += '<div class="client-card' + pausedClass + '">';
            html += '<div class="client-header">';
            html += '<span class="client-name">' + name + '</span>';
            html += '<span class="client-state">' + state + '</span>';
            html += '</div>';
            if (c.ipAddr) {
                html += '<div class="client-ip">' + c.ipAddr + '</div>';
            }

            // Sync section
            html += '<div class="section-label sync-label" style="border-top-color:' + stateColor + '">Sync</div>';
            html += '<div class="client-metrics">';
            var filterText = coStable >= 2 ? 'Stable' : (coStable === 1 ? 'PPM converging...' : 'Filters filling...');
            html += '<span>Filter</span>' + val(filterText, filterBad);
            // Separate from the line above. coStable says the filter is usable and
            // gates frame stretch; this says the regression window is completely
            // full (~4 min), which is when the drift estimate is as good as it gets.
            // Not marked red while filling — nothing is wrong, it is just early.
            if (fullDiag) {
                html += '<span>Filter Window</span>' + val(coFiltersFull ? 'Full' : 'Filling...', false);
            }
            // Named for the quantity, not the code that produces it, and the title
            // says what it is not. Measured 2026-09-15 on an identical pair: this
            // number ranks the acoustic error correctly (corr +0.39, and the
            // measured pair spread grows monotonically with it) but its SCALE is
            // wrong by ~30x - 2 µs here was ~51 µs of real spread. It is an
            // estimator self-assessment. It is not measured playback sync, and a
            // µs figure on screen invites exactly that misreading.
            html += '<span title="Standard deviation of the receiver\'s own clock-offset estimate. Estimator self-assessment - NOT measured playback sync.">CO est stdDev</span>'
                 + val(coFiltStdDev + ' µs', coFiltStdDevBad, coFiltStdDevWarn);
            // avgTta showed NO correlation with the acoustic error (-0.01) - it is
            // control error against the RX's own target, so it inherits the same
            // belief the estimate does. "Timing Error" alone read as the real thing.
            if (fullDiag) html += '<span title="Moving average of |timeToAlter|: how far the playout actuator is from the receiver\'s own target. Estimator self-assessment - NOT measured playback sync.">Actuator err (est)</span>'
                 + val(avgTta + ' µs', avgTtaBad);
            if (fullDiag) {
                html += '<span>Filter Margin</span>' + val((c.marginSct || 0) + '/' + (c.marginCst || 0) + ' µs', false);
            }
            html += '<span>Source PPM</span>' + val((c.ppmTx || 0).toFixed(1), false);
            html += '<span>CPU PPM</span>' + val((c.ppmCpu || 0).toFixed(1), false);
            html += '<span>PCM PPM</span>' + val((c.ppmPcm || 0).toFixed(1), false);
            var totalPpm = ((c.ppmTx || 0) + (c.ppmCpu || 0) + (c.ppmPcm || 0)).toFixed(1);
            html += '<span>Total PPM</span>' + val(totalPpm, false);
            /* One row instead of two. The breakdown is the point: the network term is
             * what the link forces on us (jitter + burst + 2 ms margin, from
             * libPrezoNetQuality) and the PCM term is the output buffer this device
             * opened. Seeing which half dominates is the whole diagnostic value, and it
             * is what "Loop margin" was being read for. */
            html += '<span>Min possible latency</span>'
                 + val(minPossibleMs + ' ms (net ' + ((c.nqRecLatency || 0) / 1000).toFixed(1)
                       + ' + PCM buf ' + (alsaBufUs / 1000).toFixed(0) + ' ms)',
                       latencyExceeded);
            // Leading indicator: buffer time left after the worst observed loop
            // gap. Shows the numerator/denominator so a bad value is diagnosable
            // without opening two other fields.
            // Kept for us only: the buffer size it compares against is now on the
            // latency row above, and what it adds is the worst loop pass - a number
            // about our scheduling, not about the listening.
            if (haveMargin && fullDiag) {
                html += '<span>Loop margin</span>' + val(
                    marginMs + ' ms (' + (loopMaxUs / 1000).toFixed(1) + '/'
                    + (alsaBufUs / 1000).toFixed(0) + ' ms)', marginBad);
            }
            // Running minimum output buffer fill. -1 means the backend does not
            // report it, which is not the same as an empty buffer, so it is shown
            // as n/a rather than 0. Shown as a percentage of the buffer because
            // the raw frame count means nothing without the depth.
            var minDly = (c.minDlyFrames === undefined) ? -1 : c.minDlyFrames;
            if (minDly >= 0) {
                // bufferSize in frames, from the reported ms at the RX sample rate.
                var bufFrames = Math.round(((c.alsaBufferMs || 0) / 1000) * 48000);
                var pct = bufFrames > 0 ? (minDly / bufFrames * 100) : 0;
                // 25% of the buffer: the bad case sat at 0.1-1.8% while glitching.
                var minDlyBad = bufFrames > 0 && pct < 25;
                html += '<span>Min buffer fill</span>' + val(
                    minDly + ' fr' + (bufFrames > 0 ? ' (' + pct.toFixed(0) + '%)' : ''),
                    minDlyBad);
            }
            // The two paths that emit silence. Only shown once non-zero — a clean
            // device should not carry two permanently-zero rows.
            var padCnt = c.xferPadCnt || 0;
            var hltCnt = c.haltedFillCnt || 0;
            if (padCnt > 0 || hltCnt > 0) {
                var silRising = rising(c.name + ':pad', padCnt)
                             || rising(c.name + ':hlt', hltCnt);
                html += '<span title="Times this receiver output silence instead of audio, as a running total since TX started - it is never reset, not even when the receiver restarts. pad = tail padding when the queue held fewer frames than the DMA needed; halt = top-up while the output was halted. The number is coloured only while it is still rising.">'
                     + 'Silence inserted (total)</span>'
                     + val('pad ' + padCnt + '/' + (c.xferPadFrames || 0) + 'fr, '
                           + 'halt ' + hltCnt + '/' + (c.haltedFillFrames || 0) + 'fr',
                           false, silRising);
            }
            html += '</div>';

            // Network section
            html += '<div class="section-label net-label" style="border-top-color:' + netRingColors[netGrade] + '">Network</div>';
            html += '<div class="client-metrics">';
            /* Colour the grade with the grade's OWN legend colour, so the value and
             * the legend at the bottom cannot disagree. It used to be
             * val(name, netGrade < 3), which painted "Good" red - Good is grade 2 and
             * only Excellent is 3 - and red is not even a colour the legend assigns to
             * Good. Two sources of truth for one thing; now there is one. */
            html += '<span>Quality</span>'
                 + '<span class="metric-val" style="color:' + netRingColors[netGrade]
                 + ';font-weight:600">' + netRingNames[netGrade] + '</span>';
            html += '<span>Jitter P95</span>' + val(jitP95Ms + ' ms', false);
            html += '<span>Jitter Max</span>' + val(jitMaxMs + ' ms', false);
            html += '<span>Loss</span>' + netVal(loss + ' ‰', 'lossPermil', loss);
            // Loss Length does not feed the grade at all, so it cannot be coloured
            // from NET_LIMITS. Amber when non-zero: worth noticing, not a grade.
            html += '<span>Loss Length</span>' + val(burstMs + ' ms', false, burstBad);
            html += '<span>Link</span>' + val(linkType, false);
            html += '<span>Signal</span>'
                 + netVal(wifiSig ? wifiSig + ' dBm' : 'N/A', 'signalDbm', wifiSig);
            html += '<span>WiFi Quality</span>' + val(wifiSig ? wifiQual + '/70' : 'N/A', false);
            html += '<span>TX Retries</span>'
                 + netVal(wifiSig ? wifiRetries : 'N/A', 'txRetries', wifiRetries);
            // RX errors are not in the grade either.
            html += '<span>RX Errors</span>' + val(wifiSig ? wifiErrors : 'N/A', false, errBad);
            html += '</div></div>';
        }

        // Legend
        html += '<div class="legend">';
        html += '<div class="legend-title">Legend</div>';
        html += '<div class="legend-row"><b>Sync:</b> ';
        html += '<span style="color:#94a3b8">■</span> Waiting &nbsp;';
        html += '<span style="color:#ef4444">■</span> Collecting &nbsp;';
        html += '<span style="color:#3b82f6">■</span> Finetuning &nbsp;';
        html += '<span style="color:#22c55e">■</span> Locked/+ &nbsp;';
        html += '<span style="color:' + rxState.COLOR_LOST + '">■</span> Lost/No contact</div>';
        // Generated from NET_RING_NAMES/COLORS, so the legend cannot drift from what
        // the cards actually render - which is exactly how "Good" came to be shown in
        // a colour the legend did not even list.
        html += '<div class="legend-row"><b>Network:</b> ';
        for (var gi = 0; gi < netRingNames.length; gi++) {
            html += '<span style="color:' + netRingColors[gi] + '">■</span> '
                 + netRingNames[gi] + (gi < netRingNames.length - 1 ? ' &nbsp;' : '');
        }
        html += '</div>';
        html += '<div class="legend-row">Green is best level. <b>Any other colour on a '
             + 'value is what is preventing it</b>, in that value\'s own grade colour.</div>';
        /* Reset the accumulating counters. Only these are resettable, because they are
         * the only status fields TX sums rather than mirrors: one transient leaves a
         * non-zero total for the life of the TX process, so without this the Status tab
         * can only ever say "has happened at some point". Everything else is mirrored
         * from the RX and would reappear on its next reply. */
        html += '<div class="legend-row">'
             + '<button id="reset-counters-btn" class="reset-counters-btn">'
             + 'Reset counters</button>'
             + ' <span class="legend-note">Zeroes missed packets, starve, XRUN and the '
             + 'silence totals, so what appears next is what is happening now. '
             + 'Does not touch anything mirrored live from a receiver.</span></div>';
        html += '</div>';

        clientGridEl.innerHTML = html;

        var resetBtn = document.getElementById('reset-counters-btn');
        if (resetBtn) {
            resetBtn.addEventListener('click', function() {
                resetBtn.disabled = true;
                resetBtn.textContent = 'Resetting...';
                fetch('/api/status', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({cmd: 'resetCounters'})
                }).then(function() {
                    // Drop the client-side "is it rising" baselines too, or every
                    // counter would look like it jumped on the next poll.
                    prevCounters = {};
                    poll();
                }).catch(function() {
                    resetBtn.disabled = false;
                    resetBtn.textContent = 'Reset counters';
                });
            });
        }
    }

    poll();
    setInterval(poll, 1000);
})();
