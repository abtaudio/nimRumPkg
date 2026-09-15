(function() {
    'use strict';

    // Thin by design: the survey is done by
    // nimRum.tx_fleet.network_survey (also a CLI tool). This file only calls
    // /api/network/links and renders the rows.

    var surveyBtn = document.getElementById('survey-btn');
    var surveyStatus = document.getElementById('survey-status');
    var surveyResult = document.getElementById('survey-result');

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function(c) {
            return {'&': '&amp;', '<': '&lt;', '>': '&gt;',
                    '"': '&quot;', "'": '&#39;'}[c];
        });
    }

    function cell(text, bad) {
        var span = bad
            ? '<span class="metric-bad">' + esc(text) + '</span>'
            : esc(text);
        return '<td>' + span + '</td>';
    }

    function render(data) {
        var devices = data.devices || [];
        if (devices.length === 0) {
            surveyResult.innerHTML = '<p class="muted">No devices returned.</p>';
            return;
        }

        var html = '<table class="fleet-table"><thead><tr>'
            + '<th>Device</th><th>Link speed</th><th>Range</th><th>Spread</th>'
            + '<th>vs median</th><th>RSSI</th><th>Chan</th><th>Width</th>'
            + '<th>Note</th>'
            + '</tr></thead><tbody>';

        devices.forEach(function(d) {
            html += '<tr>' + cell(d.hostname || '?', false);

            if (d.error) {
                // A wired device legitimately has no wpa_cli. Not a fault.
                html += '<td colspan="7" class="error-text">' + esc(d.error)
                     + '</td><td></td></tr>';
                return;
            }

            var notes = [];
            if (d.slow) { notes.push('SLOW'); }
            if (d.link_unstable) { notes.push('UNSTABLE'); }
            if (d.channel_disagrees) { notes.push('stale chan report'); }

            // Show what the device itself reports. Absent is not zero — the
            // UWE5622 driver reports no WIDTH at all.
            // Chan is the device's own 20 MHz channel, which normally differs
            // from the block centre; the flag compares it against the fleet.
            var chan = d.frequency_mhz;

            // Show the spread, do not hide it behind the median. A link that
            // sits still repeats one value; one that wanders is worth a look.
            var lo = d.link_speed_min_mbps, hi = d.link_speed_max_mbps;
            var range = (lo == null || hi == null) ? 'n/a'
                      : (lo === hi ? String(lo) : lo + '-' + hi);
            var samples = d.link_speed_samples || [];

            html += cell(d.link_speed_mbps ? d.link_speed_mbps + ' Mbps' : 'n/a',
                         d.slow)
                 + '<td title="' + esc(samples.join(', ')) + '">'
                 + (d.link_unstable
                        ? '<span class="metric-bad">' + esc(range) + '</span>'
                        : esc(range))
                 + '</td>'
                 + cell(d.link_speed_spread != null
                            ? d.link_speed_spread.toFixed(2) : 'n/a',
                        d.link_unstable)
                 + cell(d.link_speed_ratio != null ? d.link_speed_ratio.toFixed(2) : 'n/a',
                        d.slow)
                 + cell(d.rssi_dbm != null ? d.rssi_dbm + ' dBm' : 'n/a', false)
                 + cell(chan ? chan + ' MHz' : 'n/a', d.channel_disagrees)
                 + cell(d.width_mhz ? d.width_mhz + ' MHz' : 'n/a', false)
                 + cell(notes.join(', '), notes.length > 0)
                 + '</tr>';
        });

        html += '</tbody></table>';

        html += '<p class="muted">Link speed is a spot reading and Range is the'
             + ' spread within this run (hover a range to see every sample).'
             + ' Run it again and expect different numbers — a healthy link'
             + ' repeats the same value, so one that wanders is worth'
             + ' investigating. Flagged UNSTABLE above '
             + (data.unstable_spread || 1.5) + '× between min and max.</p>';

        if (data.fleet_median_mbps) {
            html += '<p class="muted">Fleet median ' + data.fleet_median_mbps
                 + ' Mbps. Flagged SLOW below '
                 + Math.round(data.slow_ratio * 100) + '% of median or '
                 + data.slow_link_mbps + ' Mbps — the OPis are legitimately '
                 + 'slower, so this is relative rather than an absolute limit.'
                 + '</p>';
        }

        if (data.fleet_center_freq_mhz) {
            var stale = devices.filter(function(d) {
                return d.channel_disagrees;
            }).map(function(d) { return d.hostname; });

            html += '<p class="muted">AP block ' + data.fleet_center_freq_mhz
                 + ' MHz / ' + data.fleet_width_mhz + ' MHz wide, taken from'
                 + ' the fleet majority. Chan is each device\'s own reported'
                 + ' 20 MHz channel, which normally sits inside that block.';
            if (stale.length > 0) {
                html += ' <b>' + esc(stale.join(', ')) + '</b> report a channel'
                     + ' outside it. All devices share one AP and these are'
                     + ' associated and passing audio, so it is the driver\'s'
                     + ' <i>report</i> that is stale, not the link.';
            }
            html += '</p>';
        }

        surveyResult.innerHTML = html;
    }

    function runSurvey() {
        surveyBtn.disabled = true;
        surveyStatus.textContent = 'Polling fleet…';
        surveyResult.innerHTML = '';

        fetch('/api/network/links')
            .then(function(r) { return r.json(); })
            .then(function(data) {
                surveyStatus.textContent = 'Done — ' + (data.num_devices || 0)
                    + ' devices.';
                render(data);
            })
            .catch(function(e) {
                surveyStatus.textContent = 'Failed: ' + e;
            })
            .then(function() {
                surveyBtn.disabled = false;
            });
    }

    surveyBtn.addEventListener('click', runSurvey);
})();
