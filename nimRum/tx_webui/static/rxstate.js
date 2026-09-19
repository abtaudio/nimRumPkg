// Shared interpretation of one RX client entry from /api/status.
//
// Both the topology canvas and the status list have to answer the same
// questions about a client — what state is it in, is it still reporting, how
// good is its link — and they used to answer them separately. They then
// disagreed: the topology kept drawing the Locked+ marker on a device that had
// stopped replying, and the two net-grade rules differed for a client with
// packet loss but no WiFi stats. Anything derived from a client entry belongs
// here so there is one answer per question.
(function(global) {
    'use strict';

    var STATE_NAMES = {
        '-2': 'Waiting',
        '-1': 'Collecting',
        '0': 'Finetuning',
        '1': 'Locked',
        '2': 'Locked+'
    };

    var STATE_COLORS = {
        '-2': '#94a3b8',
        '-1': '#ef4444',
        '0': '#3b82f6',
        '1': '#22c55e',
        '2': '#22c55e'
    };

    var COLOR_LOST = '#475569';   // also used for paused on the canvas
    var COLOR_UNKNOWN = '#94a3b8';

    /* Network grade: ONE source for the colour, the name, the legend and the
     * per-field marking. Index 0=poor .. 3=excellent.
     *
     * Green is reserved for Excellent, and everything below it runs
     * amber -> orange -> red. That is the whole rule: **anything but green is
     * preventing best level.** Good used to be light green and was then also
     * painted red by a separate boolean, which is what a colour scheme with two
     * sources of truth does.
     */
    var NET_RING_COLORS = ['#ef4444', '#f97316', '#f59e0b', '#22c55e'];
    var NET_RING_NAMES = ['Poor', 'Marginal', 'Good', 'Excellent'];

    /* The thresholds behind the grade, in one table so a single field's grade and
     * the overall grade cannot disagree. Each entry: the value at or beyond which
     * the field alone drags the device down to that grade.
     *
     * Read the grade functions below against netRingGrade(): they are the same
     * numbers, which is the point. Adding a threshold here changes the ring, the
     * field colour and the legend together. */
    var NET_LIMITS = {
        signalDbm:  { g2: -60, g1: -70, g0: -80 },  // at or below -> that grade
        lossPermil: { g2: 1,   g1: 2,   g0: 10 },   // at or above -> that grade
        txRetries:  { g1: 50 }                      // above -> marginal
    };

    // Grade a single field on its own. Returns 3 when the field is not holding the
    // device back at all, so the caller can render it plain.
    function netFieldGrade(kind, value) {
        var L = NET_LIMITS[kind];
        if (!L || value === null || value === undefined) return 3;
        if (kind === 'signalDbm') {
            if (!value) return 3;                    // 0 = wired / not reported
            if (value <= L.g0) return 0;
            if (value <= L.g1) return 1;
            if (value <= L.g2) return 2;
            return 3;
        }
        if (kind === 'lossPermil') {
            if (value >= L.g0) return 0;
            if (value >= L.g1) return 1;
            if (value >= L.g2) return 2;
            return 3;
        }
        if (kind === 'txRetries') {
            return (value > L.g1) ? 1 : 3;
        }
        return 3;
    }

    // A client that stopped sending CLI replies keeps whatever status it last
    // reported, so the status alone cannot be trusted. A live device in
    // finetuning (status 0) has coFiltStdDevUs > 0 once its filter starts
    // producing output (~30s after boot), so all-zero metrics with status >= 0
    // means nothing is coming back from it.
    function isLost(c) {
        if (!c) return false;
        /* marginSct/marginCst used to be part of this test. They are zeroed
         * in a public build, which would have removed two of the seven guards and made
         * a live device more likely to be called Lost. Only fields that survive both
         * builds may be used here.
         *
         * This is a heuristic because there is no per-client staleness on /api/status -
         * TX serves the last reply it got, indefinitely. An age field is the real fix
         * and would replace this entirely. docs/STATE.md, known issues. */
        return (c.status >= 0 && c.coFiltStdDevUs === 0 &&
                c.avgTtaUs === 0 && c.wifiSignal === 0 &&
                c.rxMissedPkts === 0);
    }

    function stateName(c) {
        if (!c) return 'Unknown';
        if (c.paused) return 'No contact';
        if (isLost(c)) return 'Lost';
        return STATE_NAMES[String(c.status)] || 'Unknown';
    }

    function stateColor(c) {
        if (!c) return COLOR_UNKNOWN;
        if (c.paused) return COLOR_UNKNOWN;
        if (isLost(c)) return COLOR_LOST;
        return STATE_COLORS[String(c.status)] || COLOR_UNKNOWN;
    }

    // True when the client is in the Locked+ state and that state is current.
    function isLockedPlus(c) {
        return !!c && c.status === 2 && !c.paused && !isLost(c);
    }

    function netRingGrade(c) {
        if (!c) return 3;
        var sig = c.wifiSignal || 0;
        var loss = c.nqLossPermille || 0;
        var retries = c.wifiTxRetries || 0;

        // Worst of the individual field grades, from the one NET_LIMITS table.
        if (!sig && loss === 0) return 3;
        return Math.min(netFieldGrade('signalDbm', sig),
                        netFieldGrade('lossPermil', loss),
                        netFieldGrade('txRetries', retries));
    }

    global.nimRumRxState = {
        STATE_NAMES: STATE_NAMES,
        STATE_COLORS: STATE_COLORS,
        COLOR_LOST: COLOR_LOST,
        COLOR_UNKNOWN: COLOR_UNKNOWN,
        NET_RING_COLORS: NET_RING_COLORS,
        NET_RING_NAMES: NET_RING_NAMES,
        isLost: isLost,
        isLockedPlus: isLockedPlus,
        stateName: stateName,
        stateColor: stateColor,
        netRingGrade: netRingGrade,
        netFieldGrade: netFieldGrade,
        NET_LIMITS: NET_LIMITS
    };
})(window);
