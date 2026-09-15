(function() {
    'use strict';

    var canvas = document.getElementById('topo-canvas');
    var ctx = canvas.getContext('2d');
    var sourceListEl = document.getElementById('source-list');
    var resetBtn = document.getElementById('reset-btn');
    var storeGroupsBtn = document.getElementById('store-groups-btn');

    var rxState = window.nimRumRxState;

    var clients = [];
    var angles = [];       // angle in radians for each RX on outer circle
    var groupPosition = [];  // index of each RX within its own group, for label stagger
    var compact = false;     // narrow screen: tighter ring, shorter labels
    var glueBonds = [];    // explicit glue bonds: array of [i, j] pairs (indices that are glued)
    var dragging = -1;     // index of RX being dragged
    var txOnline = false;
    var sourceActive = false;
    var sourceName = '';
    var txLinkStats = {};

    // Two-layer mute state from TX
    var temporaryMute = false;
    var sceneMute = {};    // {cliId: bool}
    var serverGroups = []; // groups from TX (source of truth)

    // Grouping thresholds (variables — may later depend on client count)
    var GLUE_THRESHOLD_DEG = 15;
    var SEPARATE_THRESHOLD_DEG = 40;

    function glueThresholdRad() {
        return GLUE_THRESHOLD_DEG * Math.PI / 180;
    }
    function separateThresholdRad() {
        return SEPARATE_THRESHOLD_DEG * Math.PI / 180;
    }

    var dpr = window.devicePixelRatio || 1;
    var W = 0, H = 0, cx = 0, cy = 0, outerR = 0, groupR = 0, txR = 0, rxR = 0;

    // Group arcs double as the mute control for that group, so they are drawn
    // thick enough to read as pressable. The hit area is a little wider again.
    var GROUP_ARC_WIDTH = 10;   // px before dpr scaling
    var GROUP_ARC_HIT = 14;

    // --- Angle utilities ---

    function normalizeAngle(a) {
        a = a % (2 * Math.PI);
        if (a < 0) a += 2 * Math.PI;
        return a;
    }

    function angularDistance(a, b) {
        // Shortest angular distance between two angles (always positive)
        var d = Math.abs(normalizeAngle(a) - normalizeAngle(b));
        if (d > Math.PI) d = 2 * Math.PI - d;
        return d;
    }

    // --- Glue bond management ---

    function hasGlueBond(i, j) {
        var a = Math.min(i, j), b = Math.max(i, j);
        for (var k = 0; k < glueBonds.length; k++) {
            if (glueBonds[k][0] === a && glueBonds[k][1] === b) return true;
        }
        return false;
    }

    function addGlueBond(i, j) {
        var a = Math.min(i, j), b = Math.max(i, j);
        if (!hasGlueBond(a, b)) {
            glueBonds.push([a, b]);
        }
    }

    function removeGlueBond(i, j) {
        var a = Math.min(i, j), b = Math.max(i, j);
        glueBonds = glueBonds.filter(function(bond) {
            return !(bond[0] === a && bond[1] === b);
        });
    }

    // Build groups from glue bonds using union-find
    function computeGroupsFromBonds() {
        var n = clients.length;
        if (n === 0) return [];

        // Union-find
        var parent = [];
        for (var i = 0; i < n; i++) parent.push(i);

        function find(x) {
            while (parent[x] !== x) { parent[x] = parent[parent[x]]; x = parent[x]; }
            return x;
        }
        function union(a, b) {
            var ra = find(a), rb = find(b);
            if (ra !== rb) parent[ra] = rb;
        }

        for (var k = 0; k < glueBonds.length; k++) {
            union(glueBonds[k][0], glueBonds[k][1]);
        }

        // Collect groups
        var groupMap = {};
        for (var i = 0; i < n; i++) {
            var root = find(i);
            if (!groupMap[root]) groupMap[root] = [];
            groupMap[root].push(i);
        }

        var groups = [];
        var keys = Object.keys(groupMap);
        for (var k = 0; k < keys.length; k++) {
            groups.push(groupMap[keys[k]]);
        }
        return groups;
    }

    // Update glue bonds based on current angles (called during drag)
    function updateGlueBonds(draggedIdx) {
        var n = clients.length;
        if (n < 2) return;

        // Check if dragged speaker should glue to any neighbor
        for (var i = 0; i < n; i++) {
            if (i === draggedIdx) continue;
            var dist = angularDistance(angles[draggedIdx], angles[i]);

            if (hasGlueBond(draggedIdx, i)) {
                // Check if should separate
                if (dist > separateThresholdRad()) {
                    removeGlueBond(draggedIdx, i);
                }
            } else {
                // Check if should glue
                if (dist < glueThresholdRad()) {
                    addGlueBond(draggedIdx, i);
                }
            }
        }
    }

    // Set glue bonds from a groups array (used on restore)
    function setBondsFromGroups(groups) {
        glueBonds = [];
        for (var g = 0; g < groups.length; g++) {
            var grp = groups[g];
            // Create chain bonds between adjacent members in the group
            // Sort by angle so bonds connect neighbors
            var sorted = grp.slice().sort(function(a, b) {
                return normalizeAngle(angles[a]) - normalizeAngle(angles[b]);
            });
            for (var j = 1; j < sorted.length; j++) {
                addGlueBond(sorted[j - 1], sorted[j]);
            }
        }
    }

    // --- Angle persistence ---

    function loadAngles(n) {
        try {
            var saved = JSON.parse(localStorage.getItem('nimrum_topo_angles'));
            if (saved && saved.length === n) return saved;
        } catch(e) {}
        return null;
    }

    function saveAngles() {
        localStorage.setItem('nimrum_topo_angles', JSON.stringify(angles));
    }

    // Distribute angles respecting groups: speakers in the same group are placed
    // close together (INTRA_GROUP_GAP), groups are separated equally around circle.
    /* Angular span of a group's arc: [start, end] with end >= start, unnormalised.
     *
     * ONE definition, used by both the renderer and the hit test. They used to compute
     * this separately and disagreed: the renderer took the smallest enclosing arc via
     * the largest-gap method, while the hit test normalised each angle independently and
     * took min/max — which for a group straddling the 0/2pi wrap yields the COMPLEMENT
     * of its real span, i.e. nearly the whole circle. That group then swallowed clicks
     * meant for every other group, and since the search returns the first match, the
     * wrong group got muted while the arcs on screen looked perfectly correct.
     *
     * With N groups round a circle, one of them straddles the wrap most of the time, so
     * this was not an edge case.
     */
    function groupArcSpan(grp, padding) {
        var a = grp.map(function(idx) { return angles[idx]; });
        a.sort(function(x, y) { return x - y; });
        var n = a.length;
        if (n === 1) return [a[0] - padding, a[0] + padding];

        // The largest gap between neighbours is the space BETWEEN groups; the arc is
        // everything else.
        var bestGap = -1, bestGapIdx = -1;
        for (var j = 0; j < n; j++) {
            var next = (j + 1) % n;
            var gap = (next === 0) ? (a[0] + 2 * Math.PI) - a[n - 1] : a[next] - a[j];
            if (gap > bestGap) { bestGap = gap; bestGapIdx = j; }
        }
        if (bestGapIdx === n - 1) {
            return [a[0] - padding, a[n - 1] + padding];
        }
        return [a[bestGapIdx + 1] - padding, a[bestGapIdx] + 2 * Math.PI + padding];
    }

    function distributeAnglesFromGroups(groups) {
        var n = clients.length;
        if (n === 0) return;

        // Angular separation within a group, derived from geometry rather than fixed.
        //
        // Members deliberately OVERLAP by INTRA_OVERLAP of their diameter: touching
        // circles are what makes a group read as one thing at a glance, which is the
        // point of grouping. Centre distance for a fraction f of overlap is
        // (1 - f) * 2 * rxR, and the arc between two nodes is outerR * theta.
        //
        // It was a fixed 8 degrees until 2026-09-15 — a constant answer to a question
        // that depends on canvas size — which collapsed a three-speaker group into a
        // single blob with three labels printed over each other. The labels were the
        // real problem, and they are now placed radially and staggered, so the nodes are
        // free to overlap on purpose. Briefly went the other way, forcing a clear gap,
        // which spread groups so wide the ring had to shrink and the whole diagram
        // looked cramped on a phone.
        var INTRA_OVERLAP = 0.4;
        var intraRad = ((1 - INTRA_OVERLAP) * 2 * rxR) / Math.max(outerR, 1);

        // Total intra-group spacing needed
        var totalIntra = 0;
        for (var g = 0; g < groups.length; g++) {
            totalIntra += (groups[g].length - 1) * intraRad;
        }

        // Remaining space distributed equally between groups
        var interSpace = 2 * Math.PI - totalIntra;
        var interGap = groups.length > 0 ? interSpace / groups.length : 2 * Math.PI;

        // Place each group sequentially around the circle
        var pos = -Math.PI / 2;
        for (var g = 0; g < groups.length; g++) {
            var grp = groups[g];
            for (var j = 0; j < grp.length; j++) {
                var idx = grp[j];
                if (idx < n) {
                    angles[idx] = pos + j * intraRad;
                    groupPosition[idx] = j;
                }
            }
            pos += (grp.length - 1) * intraRad + interGap;
        }
    }

    function resetAngles() {
        var n = clients.length || 1;
        angles = new Array(n);
        // Re-fetch groups from TX and distribute accordingly
        fetch('/api/groups')
            .then(function(r) { return r.json(); })
            .then(function(d) {
                if (d.groups && d.groups.length > 0) {
                    setBondsFromGroups(d.groups);
                    distributeAnglesFromGroups(d.groups);
                } else {
                    glueBonds = [];
                    for (var i = 0; i < n; i++) {
                        angles[i] = i * 2 * Math.PI / n - Math.PI / 2;
                    }
                }
                saveAngles();
                draw();
            })
            .catch(function() {
                glueBonds = [];
                for (var i = 0; i < n; i++) {
                    angles[i] = i * 2 * Math.PI / n - Math.PI / 2;
                }
                saveAngles();
                draw();
            });
    }

    // --- TX group sync ---

    function syncGroupsToTx() {
        var groups = computeGroupsFromBonds();
        fetch('/api/groups', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({groups: groups})
        }).catch(function() {});
    }

    function sendMuteToggle() {
        temporaryMute = !temporaryMute;   // optimistic, for an instant response
        draw();
        fetch('/api/status', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({cmd: 'muteToggle'})
        }).then(function(r) { return r.json(); })
          .then(function(data) { applyStatus(data); })
          .catch(function() {});
    }

    function sendSceneMuteToggle(cliIds) {
        // Optimistic local update: toggle immediately for responsive UI
        var anyUnmuted = false;
        for (var i = 0; i < cliIds.length; i++) {
            if (!sceneMute[cliIds[i]]) { anyUnmuted = true; break; }
        }
        var newState = anyUnmuted; // if any unmuted → mute all, else unmute all
        for (var i = 0; i < cliIds.length; i++) {
            sceneMute[cliIds[i]] = newState;
        }
        draw();

        // Send to TX — response includes full status
        fetch('/api/status', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({cmd: 'setSceneMuteToggle', cliIds: cliIds})
        }).then(function(r) { return r.json(); })
          .then(function(data) { applyStatus(data); })
          .catch(function() {});
    }

    // --- Canvas layout ---

    function resize() {
        var rect = canvas.getBoundingClientRect();

        /* Size from the PARENT's content width, not from the canvas's own rect.
         *
         * The canvas rect is whatever CSS last gave it, which on first layout can be
         * wider than the panel it sits in — so the canvas overhung to the right, its
         * centre landed right of the panel centre, and the right-hand label was clipped
         * by the panel rather than by the canvas. A label clamp in canvas coordinates
         * cannot see that, which is why the earlier one had no effect.
         *
         * Centred explicitly too: a block canvas narrower than its container sits left
         * otherwise, which would move the problem rather than fix it.
         */
        var parent = canvas.parentElement;
        var availW = (parent && parent.clientWidth) ? parent.clientWidth : rect.width;
        var size = Math.min(availW, rect.height || availW);
        if (size <= 0) size = availW;
        canvas.style.display = 'block';
        canvas.style.margin = '0 auto';
        canvas.width = size * dpr;
        canvas.height = size * dpr;
        canvas.style.width = size + 'px';
        canvas.style.height = size + 'px';
        W = canvas.width;
        H = canvas.height;
        cx = W / 2;
        cy = H / 2;
        // Labels sit radially OUTSIDE the ring, so the ring must leave room for them.
        // It was a flat 0.38 until 2026-09-15, then 0.31 once labels moved out — but a
        // fixed fraction is wrong across screen sizes, because label text is sized in
        // CSS pixels while the ring scales with the canvas. On a phone the same three
        // lines eat proportionally far more room, so the ring has to pull in and the
        // speakers sit closer together. cssW, not W: dpr must not change the decision.
        var cssW = W / dpr;
        compact = cssW < 560;
        var ringFrac = compact ? (cssW < 420 ? 0.26 : 0.28) : 0.31;
        outerR = W * ringFrac;
        groupR = outerR * 0.5;
        txR = W * 0.075;
        rxR = W * 0.04;
        draw();
    }

    // --- Drawing ---

    function draw() {
        ctx.clearRect(0, 0, W, H);
        var n = clients.length;
        if (n === 0 && !txOnline) {
            ctx.fillStyle = '#94a3b8';
            ctx.font = (14 * dpr) + 'px sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText('Waiting for TX...', cx, cy);
            return;
        }

        // Dim overlay if temporary mute active. Applied only to what is actually
        // silenced — the RX circles and the group arcs. The centre circle is the
        // mute *control*, not an output, so it stays fully opaque and signals
        // mute through its colour and its 'Muted' label instead.
        var dimAlpha = temporaryMute ? 0.4 : 1.0;

        // Draw lines from TX to each RX
        for (var i = 0; i < n; i++) {
            var x = cx + outerR * Math.cos(angles[i]);
            var y = cy + outerR * Math.sin(angles[i]);
            ctx.beginPath();
            ctx.moveTo(cx, cy);
            ctx.lineTo(x, y);
            ctx.strokeStyle = '#334155';
            ctx.lineWidth = 1 * dpr;
            ctx.globalAlpha = 1.0;
            ctx.stroke();
        }

        // Draw sector indicators (subtle arcs at outer edge showing each speaker's sector)
        var sectorHalfRad = (GLUE_THRESHOLD_DEG / 2) * Math.PI / 180;
        for (var i = 0; i < n; i++) {
            ctx.beginPath();
            ctx.arc(cx, cy, outerR + rxR + 8 * dpr, angles[i] - sectorHalfRad, angles[i] + sectorHalfRad);
            ctx.strokeStyle = '#475569';
            ctx.lineWidth = 2 * dpr;
            ctx.globalAlpha = 0.3;
            ctx.stroke();
        }
        ctx.globalAlpha = dimAlpha;

        // Draw group arcs
        var groups = computeGroupsFromBonds();
        for (var g = 0; g < groups.length; g++) {
            var grp = groups[g];
            if (grp.length < 2) continue; // Single speakers don't get an arc

            var span = groupArcSpan(grp, 0.15);
            var arcStart = span[0], arcEnd = span[1];

            // Determine group mute color
            var mutedCount = 0;
            for (var j = 0; j < grp.length; j++) {
                if (sceneMute[grp[j]]) mutedCount++;
            }

            var arcColor;
            if (mutedCount === 0) {
                arcColor = '#22c55e'; // All unmuted — green
            } else if (mutedCount === grp.length) {
                arcColor = '#ef4444'; // All muted — red
            } else {
                arcColor = '#eab308'; // Mixed — yellow/amber blend
            }

            ctx.beginPath();
            ctx.arc(cx, cy, groupR, arcStart, arcEnd);
            ctx.strokeStyle = arcColor;
            ctx.lineWidth = GROUP_ARC_WIDTH * dpr;
            ctx.lineCap = 'round';
            ctx.stroke();
        }

        // Also draw single-speaker "group" arcs (for mute toggle)
        for (var g = 0; g < groups.length; g++) {
            var grp = groups[g];
            if (grp.length !== 1) continue;
            var idx = grp[0];
            var a = normalizeAngle(angles[idx]);
            var isMuted = !!sceneMute[idx];

            ctx.beginPath();
            ctx.arc(cx, cy, groupR, a - 0.1, a + 0.1);
            ctx.strokeStyle = isMuted ? '#ef4444' : '#22c55e';
            ctx.lineWidth = GROUP_ARC_WIDTH * dpr;
            ctx.lineCap = 'round';
            ctx.stroke();
        }

        // Draw RX circles
        var netRingColorMap = rxState.NET_RING_COLORS;

        for (var i = 0; i < n; i++) {
            var x = cx + outerR * Math.cos(angles[i]);
            var y = cy + outerR * Math.sin(angles[i]);
            var c = clients[i];

            var netGrade = rxState.netRingGrade(c);
            var lost = rxState.isLost(c);

            // Transparency marks a silenced speaker, whatever the cause:
            // scene mute for its group, or temporary mute for everything.
            // Set before the ring so the whole speaker fades, and reset at the
            // end of the iteration so it does not leak into the next one.
            var silenced = temporaryMute || !!sceneMute[i];
            ctx.globalAlpha = silenced ? dimAlpha * 0.45 : dimAlpha;

            // Sync state fill colour. Paused and lost share the darker grey:
            // in both cases nothing is coming back from the device.
            var fillCol = rxState.stateColor(c);
            if (c.paused) fillCol = rxState.COLOR_LOST;

            // Outer ring (network quality)
            var ringWidth = 4 * dpr;
            ctx.beginPath();
            ctx.arc(x, y, rxR + ringWidth / 2, 0, 2 * Math.PI);
            ctx.strokeStyle = (c.status <= -2 || lost)
                            ? rxState.COLOR_LOST : netRingColorMap[netGrade];
            ctx.lineWidth = ringWidth;
            ctx.stroke();

            // Inner circle (sync state)
            ctx.beginPath();
            ctx.arc(x, y, rxR - 1, 0, 2 * Math.PI);
            ctx.fillStyle = fillCol;
            ctx.fill();

            // "+" marker for Locked+. Not drawn for a lost device: the last
            // status it reported is stale, so a grey circle must not keep
            // claiming Locked+.
            if (rxState.isLockedPlus(c)) {
                ctx.fillStyle = '#000';
                ctx.font = 'bold ' + (10 * dpr) + 'px sans-serif';
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                ctx.fillText('+', x, y);
            }

            // Label
            //
            // Placed radially OUTWARD from the centre, not simply below the node. Below
            // works at the top and bottom of the ring and fails at the sides: two group
            // members at 9 o'clock stack vertically, so the upper one's label lands on
            // the lower one's node. Outward is collision-free by construction, since the
            // labels then sit outside the ring the nodes are on.
            //
            // Alternate members of a group are pushed one block further out, because two
            // labels three lines tall are wider than the angular gap between their nodes.
            var labelStep = 12 * dpr;
            var ux = Math.cos(angles[i]), uy = Math.sin(angles[i]);
            var stagger = (groupPosition[i] % 2) ? (compact ? 1.6 : 3.2) * labelStep : 0;
            var off = rxR + ringWidth + 6 * dpr + stagger;
            var ax = x + ux * off, ay = y + uy * off;

            var label = c.location || c.name || ('RX' + i);
            var labelSub = (c.name && c.location) ? '(' + c.name + ')' : '';
            // On a narrow screen only the location survives. The IP and the hostname
            // are the two widest lines and the least useful on a phone, and at the sides
            // the label extends horizontally, so keeping them clipped "center (nimrum-"
            // against the canvas edge.
            var showIp = c.ipAddr && !compact;
            var showSub = labelSub && !compact;
            var lines = 1 + (showSub ? 1 : 0) + (showIp ? 1 : 0);
            ctx.fillStyle = '#e2e8f0';
            ctx.font = (10 * dpr) + 'px sans-serif';
            ctx.textBaseline = 'top';
            var align = (ux > 0.3) ? 'left' : (ux < -0.3) ? 'right' : 'center';
            ctx.textAlign = align;

            /* Keep the text inside the canvas.
             *
             * A node at 3 o'clock puts its label to the right of itself, and
             * "center (nimrum-rx3)" is wider than the margin the ring leaves — so it was
             * drawn clipped as "cente". Shrinking the ring until the longest label
             * happened to fit would be guessing; measuring is not. Nudging the anchor
             * inward costs a few pixels of alignment and never truncates a name.
             */
            var edgePad = 4 * dpr;
            var widest = ctx.measureText(label).width;
            if (showSub) widest = Math.max(widest, ctx.measureText(labelSub).width);
            if (align === 'left') {
                ax = Math.min(ax, W - edgePad - widest);
            } else if (align === 'right') {
                ax = Math.max(ax, edgePad + widest);
            } else {
                ax = Math.min(Math.max(ax, edgePad + widest / 2), W - edgePad - widest / 2);
            }
            // In the upper half the block grows upward, so it clears the node.
            var labelTop = (uy < -0.3) ? ay - lines * labelStep : ay;

            ctx.fillText(label, ax, labelTop);
            if (showSub) {
                ctx.fillStyle = '#94a3b8';
                ctx.fillText(labelSub, ax, labelTop + labelStep);
            }
            if (showIp) {
                ctx.fillStyle = '#64748b';
                ctx.font = (8 * dpr) + 'px monospace';
                ctx.fillText(c.ipAddr, ax, labelTop + (showSub ? 2 : 1) * labelStep);
            }

            ctx.globalAlpha = dimAlpha;
        }

        // Draw TX circle (center) — always fully opaque, see dimAlpha note above.
        ctx.globalAlpha = 1.0;

        // Grey: no TX. Red: muted, or TX up with no source. Green: playing.
        var txColor = !txOnline ? '#94a3b8'
                    : ((temporaryMute || !sourceActive) ? '#ef4444' : '#22c55e');

        // TX network ring
        var txLink = txLinkStats;
        var txSig = txLink.signal || 0;
        var txNetGrade = 3;
        if (txSig) {
            if (txSig < -80) txNetGrade = 0;
            else if (txSig < -70) txNetGrade = 1;
            else if (txSig < -60) txNetGrade = 2;
        }
        var txRingWidth = 4 * dpr;
        ctx.beginPath();
        ctx.arc(cx, cy, txR + txRingWidth / 2, 0, 2 * Math.PI);
        ctx.strokeStyle = txOnline ? netRingColorMap[txNetGrade] : '#94a3b8';
        ctx.lineWidth = txRingWidth;
        ctx.stroke();

        ctx.beginPath();
        ctx.arc(cx, cy, txR, 0, 2 * Math.PI);
        ctx.fillStyle = txColor;
        ctx.fill();
        // Two rows: what is playing, and the mute control/state. The centre is
        // pressable and does the same thing as mute on the remote, which also
        // hints that the group arcs are mute controls for their group.
        var topLabel = !txOnline ? 'No TX' : (sourceActive ? sourceName : 'No SRC');
        var botLabel = temporaryMute ? 'Muted' : 'Mute';
        ctx.fillStyle = '#000';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        // Shrink then ellipsize so a long source name cannot spill out of the circle
        var maxTextW = txR * 1.7;
        var fs = 11;
        ctx.font = 'bold ' + (fs * dpr) + 'px sans-serif';
        while (fs > 7 && ctx.measureText(topLabel).width > maxTextW) {
            fs -= 1;
            ctx.font = 'bold ' + (fs * dpr) + 'px sans-serif';
        }
        var shown = topLabel;
        while (shown.length > 2 && ctx.measureText(shown + '…').width > maxTextW) {
            shown = shown.slice(0, -1);
        }
        if (shown !== topLabel) shown += '…';
        ctx.fillText(shown, cx, cy - 7 * dpr);
        ctx.font = 'bold ' + (11 * dpr) + 'px sans-serif';
        ctx.fillText(botLabel, cx, cy + 7 * dpr);

        ctx.globalAlpha = 1.0;
    }

    // --- Hit testing ---

    function getAngleFromEvent(e) {
        var rect = canvas.getBoundingClientRect();
        var x = (e.clientX - rect.left) * dpr - cx;
        var y = (e.clientY - rect.top) * dpr - cy;
        return Math.atan2(y, x);
    }

    function hitRx(e) {
        var rect = canvas.getBoundingClientRect();
        var mx = (e.clientX - rect.left) * dpr;
        var my = (e.clientY - rect.top) * dpr;
        for (var i = 0; i < clients.length; i++) {
            var x = cx + outerR * Math.cos(angles[i]);
            var y = cy + outerR * Math.sin(angles[i]);
            var dx = mx - x, dy = my - y;
            if (dx * dx + dy * dy <= rxR * rxR * 2.5) return i;
        }
        return -1;
    }

    function hitTxCircle(e) {
        var rect = canvas.getBoundingClientRect();
        var mx = (e.clientX - rect.left) * dpr - cx;
        var my = (e.clientY - rect.top) * dpr - cy;
        return (mx * mx + my * my) <= txR * txR;
    }

    function hitGroupArc(e) {
        var rect = canvas.getBoundingClientRect();
        var mx = (e.clientX - rect.left) * dpr - cx;
        var my = (e.clientY - rect.top) * dpr - cy;
        var dist = Math.sqrt(mx * mx + my * my);
        if (Math.abs(dist - groupR) > GROUP_ARC_HIT * dpr) return -1;
        var clickAngle = normalizeAngle(Math.atan2(my, mx));
        var groups = computeGroupsFromBonds();
        for (var g = 0; g < groups.length; g++) {
            var grp = groups[g];
            // Exactly the span the arc was drawn with, so what you click is what you see.
            var span = groupArcSpan(grp, grp.length > 1 ? 0.15 : 0.1);
            // Compare in the span's own frame rather than normalising the ends, which is
            // what broke on wrap: shift the click by whole turns until it is at or after
            // the start, then a single range test is correct either way.
            var a = clickAngle;
            while (a < span[0]) a += 2 * Math.PI;
            while (a > span[0] + 2 * Math.PI) a -= 2 * Math.PI;
            if (a <= span[1]) return g;
        }
        return -1;
    }

    // --- Pointer events ---

    canvas.addEventListener('pointerdown', function(e) {
        var idx = hitRx(e);
        if (idx >= 0) {
            dragging = idx;
            canvas.setPointerCapture(e.pointerId);
            canvas.style.cursor = 'grabbing';
            e.preventDefault();
            return;
        }
        // Centre circle: same action as mute on the remote (temporary mute)
        if (hitTxCircle(e)) {
            sendMuteToggle();
            return;
        }
        // Check group arc click for scene mute toggle
        var g = hitGroupArc(e);
        if (g >= 0) {
            var groups = computeGroupsFromBonds();
            if (g < groups.length) {
                sendSceneMuteToggle(groups[g]);
            }
        }
    });

    canvas.addEventListener('pointermove', function(e) {
        if (dragging < 0) {
            if (hitRx(e) >= 0) {
                canvas.style.cursor = 'grab';
            } else if (hitTxCircle(e) || hitGroupArc(e) >= 0) {
                canvas.style.cursor = 'pointer';
            } else {
                canvas.style.cursor = 'default';
            }
            return;
        }
        angles[dragging] = getAngleFromEvent(e);
        updateGlueBonds(dragging);
        draw();
    });

    canvas.addEventListener('pointerup', function(e) {
        if (dragging >= 0) {
            dragging = -1;
            canvas.style.cursor = 'default';
            saveAngles();
            syncGroupsToTx();
        }
    });

    // Reset button
    resetBtn.addEventListener('click', function(e) { e.preventDefault(); resetAngles(); });

    // Store Groups button — save current angles + groups to TX
    storeGroupsBtn.addEventListener('click', function(e) {
        e.preventDefault();
        syncGroupsToTx();
        storeGroupsBtn.textContent = '✓ Stored';
        setTimeout(function() { storeGroupsBtn.textContent = '💾 Store Groups'; }, 2000);
    });

    // --- Source chips ---

    function renderSources(data) {
        var sources = data.sources || [];
        var activeId = data.activeSourceId;

        var html = '';
        for (var i = 0; i < sources.length; i++) {
            var s = sources[i];
            var active = (s.id === activeId) ? ' active' : '';
            var label = s.name || ('SRC ' + s.id);
            html += '<span class="source-chip' + active + '" data-src-id="' + s.id + '">' + label + '</span>';
        }
        if (sources.length === 0) {
            html = '<span class="source-chip">No sources</span>';
        }
        sourceListEl.innerHTML = html;
    }

    sourceListEl.addEventListener('click', function(e) {
        var chip = e.target.closest('.source-chip[data-src-id]');
        if (!chip) return;
        var srcId = parseInt(chip.getAttribute('data-src-id'), 10);
        fetch('/api/status', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({cmd: 'setSource', sourceId: srcId})
        });
        poll();
    });

    // --- Status polling ---

    function applyStatus(data) {
        if (!data || data.error) return;
        if (!data.clients) {
            // Not a full status response (e.g. TX offline from WebUI cache)
            if (data.txOnline === false) {
                txOnline = false;
                sourceActive = false;
                clients = [];
                temporaryMute = false;
                sceneMute = {};
                sourceListEl.innerHTML = '<span class="source-chip">No sources</span>';
                draw();
            }
            return;
        }

        txOnline = true;
        sourceActive = (data.activeSourceId >= 0);
        sourceName = sourceActive
            ? (data.activeSourceName || ('SRC ' + data.activeSourceId)) : '';
        clients = data.clients || [];
        txLinkStats = data.txLinkStats || {};
        temporaryMute = !!data.temporaryMute;
        updateVolFromStatus(data.mainVolume);

        // Update scene mute from TX
        if (data.sceneMute) {
            sceneMute = {};
            var keys = Object.keys(data.sceneMute);
            for (var k = 0; k < keys.length; k++) {
                sceneMute[parseInt(keys[k], 10)] = data.sceneMute[keys[k]];
            }
        }

        // Initialize angles if client count changed
        if (angles.length !== clients.length) {
            var saved = loadAngles(clients.length);
            if (saved) {
                angles = saved;
            } else {
                angles = new Array(clients.length);
                for (var i = 0; i < clients.length; i++) {
                    angles[i] = i * 2 * Math.PI / clients.length - Math.PI / 2;
                }
            }

            // Restore glue bonds from TX groups
            if (data.groups && data.groups.length > 0) {
                setBondsFromGroups(data.groups);
                // If no saved angles, distribute respecting groups
                if (!saved) {
                    distributeAnglesFromGroups(data.groups);
                }
            } else {
                glueBonds = [];
            }
            saveAngles();
        }

        renderSources(data);
        draw();
    }

    function poll() {
        fetch('/api/status')
            .then(function(r) { return r.json(); })
            .then(function(data) { applyStatus(data); })
            .catch(function() {
                txOnline = false;
                sourceActive = false;
                clients = [];
                draw();
            });
    }

    // --- Main Volume ---
    var volSlider = document.getElementById('topo-vol-slider');
    var volVal = document.getElementById('topo-vol-val');
    var volDb = document.getElementById('topo-vol-db');
    var mainVolume = -1;

    // The polled status must not fight a finger on the slider, so incoming
    // values are ignored for a moment after the last local change.
    var volLocalUntil = 0;
    var VOL_LOCAL_GRACE_MS = 1500;

    function volToDb(v) {
        if (v === 0) return 'Mute';
        return (-63.0 + (v - 1) * 0.5).toFixed(1) + ' dB';
    }

    function setVolUi(v) {
        mainVolume = v;
        volSlider.value = v;
        volVal.textContent = v;
        volDb.textContent = volToDb(v);
    }

    // Called from applyStatus so the slider follows volume changes made
    // anywhere else — IR remote, LanCtrl, or the Levels tab.
    function updateVolFromStatus(v) {
        if (typeof v !== 'number') return;
        if (Date.now() < volLocalUntil) return;
        if (v === mainVolume) return;
        setVolUi(v);
    }

    volSlider.addEventListener('input', function() {
        var v = parseInt(this.value);
        mainVolume = v;
        volLocalUntil = Date.now() + VOL_LOCAL_GRACE_MS;
        volVal.textContent = v;
        volDb.textContent = volToDb(v);
        fetch('/api/status', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({cmd: 'setMainVolume', val: v})
        }).catch(function() {});
    });

    /* Build the network legend from the same arrays the rings are drawn with, so the
       two cannot drift. The hardcoded version in topology.html said Good was light
       green long after the rings had moved to amber - the same defect the Status page
       had, in a second file. */
    (function buildNetLegend() {
        var el = document.getElementById('net-legend');
        if (!el) return;
        var names = rxState.NET_RING_NAMES, cols = rxState.NET_RING_COLORS;
        for (var i = 0; i < names.length; i++) {
            var item = document.createElement('span');
            item.className = 'legend-item';
            var ring = document.createElement('span');
            ring.className = 'ring';
            ring.style.borderColor = cols[i];
            item.appendChild(ring);
            item.appendChild(document.createTextNode(names[i]));
            el.appendChild(item);
        }
    })();

    window.addEventListener('resize', resize);
    resize();
    poll();
    setInterval(poll, 1000);
})();
