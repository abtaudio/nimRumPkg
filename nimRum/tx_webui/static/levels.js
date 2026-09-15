(function() {
    'use strict';

    var contentEl = document.getElementById('levels-content');
    var saveBtn = document.getElementById('save-btn');
    var saveStatus = document.getElementById('save-status');

    // State
    var mainVolume = 0;      // live TX volume — changing it takes effect at once
    var startupVolume = 0;   // what TX comes up with after a restart; only
                             // written when Save to config is pressed
    var clients = [];    // from /api/status
    var groups = [];     // from /api/status
    var levelsData = {}; // from /api/levels
    var clientLevels = {}; // cliId -> {volStereoAdj, volMultiAdj, volCal, calRef}

    // The main volume can also be changed by the IR remote, LanCtrl or the
    // Topology tab, so it is re-read from /api/status. A grace period after a
    // local change keeps the poll from fighting a finger on the slider.
    var volLocalUntil = 0;
    var VOL_LOCAL_GRACE_MS = 1500;
    var VOL_POLL_MS = 1000;

    function mainVolToDb(vol) {
        if (vol === 0) return 'Mute';
        // 127 steps × 0.5 dB = 63 dB range, matching PCM5122/TAS5756M register
        var db = -63.0 + (vol - 1) * 0.5;
        return db.toFixed(1) + ' dB';
    }

    function adjToDb(val) {
        var db = val * 0.5;
        var sign = db >= 0 ? '+' : '';
        return sign + db.toFixed(1) + ' dB';
    }

    // A group slider writes the same value to every member, so config where
    // members differ cannot be represented by one knob. Show the lowest value
    // (moving the slider then never makes a speaker louder than it is now) and
    // mark it as mixed.
    function groupAdjInfo(group, key) {
        var vals = [];
        for (var i = 0; i < group.length; i++) {
            var cl = clientLevels[group[i]];
            if (cl) vals.push(cl[key]);
        }
        if (vals.length === 0) return {val: 0, mixed: false, detail: ''};

        var min = vals[0];
        var mixed = false;
        for (var i = 1; i < vals.length; i++) {
            if (vals[i] < min) min = vals[i];
            if (vals[i] !== vals[0]) mixed = true;
        }

        var detail = '';
        if (mixed) {
            var parts = [];
            for (var j = 0; j < group.length; j++) {
                var cli = clients[group[j]] || {};
                var memberLevels = clientLevels[group[j]];
                if (!memberLevels) continue;
                parts.push((cli.name || 'RX' + group[j]) + ': ' + memberLevels[key]);
            }
            detail = 'Members differ (' + parts.join(', ') +
                     '). Showing the lowest — moving this slider sets all ' +
                     'members to the same value.';
        }
        return {val: min, mixed: mixed, detail: detail};
    }

    function mixedCls(info) {
        return info.mixed ? ' mixed' : '';
    }

    function mixedBadge(info) {
        if (!info.mixed) return '';
        return '<span class="mixed-badge" title="' + escHtml(info.detail) + '">≠</span>';
    }

    // Once the user moves a group slider every member gets the same value, so
    // the mixed marking no longer applies.
    function clearMixed(row) {
        var marked = row.querySelectorAll('.mixed, .mixed-badge');
        for (var i = 0; i < marked.length; i++) {
            if (marked[i].classList.contains('mixed-badge')) {
                marked[i].parentNode.removeChild(marked[i]);
            } else {
                marked[i].classList.remove('mixed');
            }
        }
    }

    function init() {
        Promise.all([
            fetch('/api/status').then(function(r) { return r.json(); }),
            fetch('/api/levels').then(function(r) { return r.json(); })
        ]).then(function(results) {
            var status = results[0];
            var levels = results[1];

            if (!status.txOnline) {
                contentEl.innerHTML = '<p style="color:var(--muted)">TX offline</p>';
                return;
            }

            clients = status.clients || [];
            groups = status.groups || [];
            mainVolume = levels.mainVolume || 0;
            startupVolume = levels.startupVolume || 0;
            levelsData = levels;

            // Build clientLevels map
            var perClient = levels.clients || [];
            for (var i = 0; i < perClient.length; i++) {
                var cl = perClient[i];
                clientLevels[i] = {
                    volStereoAdj: cl.volStereoAdj || 0,
                    volMultiAdj: cl.volMultiAdj || 0,
                    volCal: cl.volCal || 0,
                    calRef: cl.calRef || ''
                };
            }

            render();
        }).catch(function(err) {
            contentEl.innerHTML = '<p style="color:var(--poor)">Failed to load: ' + err + '</p>';
        });
    }

    function render() {
        var html = '';

        // Main volume (rendered above toolbar)
        var mvHtml = '';
        mvHtml += '<div class="main-volume-section">';
        mvHtml += '<h2>Main Volume</h2>';
        mvHtml += '<div class="slider-row">';
        mvHtml += '<label>Volume</label>';
        mvHtml += '<input type="range" id="main-vol-slider" min="0" max="127" value="' +
                mainVolume + '">';
        mvHtml += '<span class="slider-val" id="main-vol-val">' + mainVolume + '</span>';
        mvHtml += '<span class="slider-db" id="main-vol-db">' + mainVolToDb(mainVolume) + '</span>';
        mvHtml += '</div>';
        mvHtml += '</div>';
        document.getElementById('main-volume-mount').innerHTML = mvHtml;

        // Startup volume — its own block below the save button, styled like a
        // speaker group. Local until Save to config is pressed.
        var suHtml = '';
        suHtml += '<div class="group-section startup-volume-section">';
        suHtml += '<h3>Startup volume</h3>';
        suHtml += '<div class="group-sliders">';
        suHtml += '<div class="slider-row">';
        suHtml += '<label>Volume</label>';
        suHtml += '<input type="range" id="startup-vol-slider" min="0" max="127" value="' +
                startupVolume + '">';
        suHtml += '<span class="slider-val" id="startup-vol-val">' + startupVolume + '</span>';
        suHtml += '<span class="slider-db" id="startup-vol-db">' +
                mainVolToDb(startupVolume) + '</span>';
        suHtml += '<button type="button" id="copy-cur-vol" class="btn-secondary">Copy current</button>';
        suHtml += '</div>';
        suHtml += '<p class="hint">What TX comes up with after a restart or power cut — ' +
                  'deliberately low. Changes nothing until you press Save to config.</p>';
        suHtml += '</div>';
        suHtml += '</div>';
        document.getElementById('startup-volume-mount').innerHTML = suHtml;

        // Groups
        for (var gi = 0; gi < groups.length; gi++) {
            var group = groups[gi];
            if (!group || group.length === 0) continue;

            // Group name: join all member locations with dash
            var groupName = 'Group ' + (gi + 1);
            var locations = [];
            for (var li = 0; li < group.length; li++) {
                var memberCli = clients[group[li]];
                if (memberCli && memberCli.location) {
                    locations.push(memberCli.location);
                }
            }
            if (locations.length > 0) {
                groupName = locations.join(' – ');
            }

            // Group-level stereo/multi adj. Members should share a value; if
            // they do not, show the lowest and flag it.
            var stereoInfo = groupAdjInfo(group, 'volStereoAdj');
            var multiInfo = groupAdjInfo(group, 'volMultiAdj');
            var groupStereo = stereoInfo.val;
            var groupMulti = multiInfo.val;

            html += '<div class="group-section" data-group="' + gi + '">';
            html += '<h3>' + escHtml(groupName) + '</h3>';
            html += '<div class="group-sliders">';

            // Stereo adj
            html += '<div class="slider-row">';
            html += '<label>Stereo adj</label>';
            html += '<input type="range" class="group-stereo-slider" data-group="' + gi +
                    '" min="-30" max="30" value="' + groupStereo + '">';
            html += '<span class="slider-val group-stereo-val' + mixedCls(stereoInfo) +
                    '">' + groupStereo + '</span>';
            html += '<span class="slider-db group-stereo-db' + mixedCls(stereoInfo) +
                    '">' + adjToDb(groupStereo) + '</span>';
            html += mixedBadge(stereoInfo);
            html += '</div>';

            // Multi adj
            html += '<div class="slider-row">';
            html += '<label>Multi adj</label>';
            html += '<input type="range" class="group-multi-slider" data-group="' + gi +
                    '" min="-30" max="30" value="' + groupMulti + '">';
            html += '<span class="slider-val group-multi-val' + mixedCls(multiInfo) +
                    '">' + groupMulti + '</span>';
            html += '<span class="slider-db group-multi-db' + mixedCls(multiInfo) +
                    '">' + adjToDb(groupMulti) + '</span>';
            html += mixedBadge(multiInfo);
            html += '</div>';

            html += '</div>'; // .group-sliders

            // Per-RX trim
            for (var ri = 0; ri < group.length; ri++) {
                var cliId = group[ri];
                var cli = clients[cliId] || {};
                var cl = clientLevels[cliId] || {volCal: 0, calRef: ''};

                html += '<div class="rx-trim" data-cli="' + cliId + '">';
                html += '<div class="rx-trim-header">';
                html += '<span class="rx-name">' + escHtml(cli.name || 'RX' + cliId) + '</span>';
                if (cli.location) {
                    html += '<span class="rx-location">' + escHtml(cli.location) + '</span>';
                }
                var calText = cl.calRef ? cl.calRef : 'N/A';
                html += '<span class="cal-badge">cal: ' + escHtml(calText) + '</span>';
                html += '</div>';

                html += '<div class="slider-row">';
                html += '<label>Trim</label>';
                html += '<input type="range" class="trim-slider" data-cli="' + cliId +
                        '" min="-30" max="30" value="' + cl.volCal + '">';
                html += '<span class="slider-val trim-val">' + cl.volCal + '</span>';
                html += '<span class="slider-db trim-db">' + adjToDb(cl.volCal) + '</span>';
                html += '</div>';
                html += '</div>'; // .rx-trim
            }

            html += '</div>'; // .group-section
        }

        contentEl.innerHTML = html;
        bindEvents();
    }

    function bindEvents() {
        // Main volume slider
        var mainSlider = document.getElementById('main-vol-slider');
        if (mainSlider) {
            mainSlider.addEventListener('input', function() {
                mainVolume = parseInt(this.value);
                volLocalUntil = Date.now() + VOL_LOCAL_GRACE_MS;
                document.getElementById('main-vol-val').textContent = mainVolume;
                document.getElementById('main-vol-db').textContent = mainVolToDb(mainVolume);
                fetch('/api/status', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({cmd: 'setMainVolume', val: mainVolume})
                }).catch(function() {});
            });
        }

        // Startup volume slider — local only until Save to config
        var startupSlider = document.getElementById('startup-vol-slider');
        if (startupSlider) {
            startupSlider.addEventListener('input', function() {
                startupVolume = parseInt(this.value);
                document.getElementById('startup-vol-val').textContent = startupVolume;
                document.getElementById('startup-vol-db').textContent =
                    mainVolToDb(startupVolume);
            });
        }

        var copyBtn = document.getElementById('copy-cur-vol');
        if (copyBtn) {
            copyBtn.addEventListener('click', function() {
                startupVolume = mainVolume;
                if (startupSlider) startupSlider.value = startupVolume;
                document.getElementById('startup-vol-val').textContent = startupVolume;
                document.getElementById('startup-vol-db').textContent =
                    mainVolToDb(startupVolume);
            });
        }

        // Group stereo sliders
        var stereoSliders = document.querySelectorAll('.group-stereo-slider');
        for (var i = 0; i < stereoSliders.length; i++) {
            stereoSliders[i].addEventListener('input', function() {
                var gi = parseInt(this.getAttribute('data-group'));
                var val = parseInt(this.value);
                var row = this.parentElement;
                row.querySelector('.group-stereo-val').textContent = val;
                row.querySelector('.group-stereo-db').textContent = adjToDb(val);
                clearMixed(row);

                // Update all members
                var group = groups[gi];
                for (var j = 0; j < group.length; j++) {
                    var cliId = group[j];
                    if (clientLevels[cliId]) {
                        clientLevels[cliId].volStereoAdj = val;
                    }
                    sendVolAdj(cliId);
                }
            });
        }

        // Group multi sliders
        var multiSliders = document.querySelectorAll('.group-multi-slider');
        for (var i = 0; i < multiSliders.length; i++) {
            multiSliders[i].addEventListener('input', function() {
                var gi = parseInt(this.getAttribute('data-group'));
                var val = parseInt(this.value);
                var row = this.parentElement;
                row.querySelector('.group-multi-val').textContent = val;
                row.querySelector('.group-multi-db').textContent = adjToDb(val);
                clearMixed(row);

                // Update all members
                var group = groups[gi];
                for (var j = 0; j < group.length; j++) {
                    var cliId = group[j];
                    if (clientLevels[cliId]) {
                        clientLevels[cliId].volMultiAdj = val;
                    }
                    sendVolAdj(cliId);
                }
            });
        }

        // Trim sliders
        var trimSliders = document.querySelectorAll('.trim-slider');
        for (var i = 0; i < trimSliders.length; i++) {
            trimSliders[i].addEventListener('input', function() {
                var cliId = parseInt(this.getAttribute('data-cli'));
                var val = parseInt(this.value);
                var row = this.parentElement;
                row.querySelector('.trim-val').textContent = val;
                row.querySelector('.trim-db').textContent = adjToDb(val);

                if (clientLevels[cliId]) {
                    clientLevels[cliId].volCal = val;
                }
                sendVolAdj(cliId);
            });
        }
    }

    function sendVolAdj(cliId) {
        var cl = clientLevels[cliId] || {volStereoAdj: 0, volMultiAdj: 0, volCal: 0};
        fetch('/api/status', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                cmd: 'setVolAdj',
                cliId: cliId,
                volStereoAdj: cl.volStereoAdj,
                volMultiAdj: cl.volMultiAdj,
                volCal: cl.volCal
            })
        }).catch(function() {});
    }

    function save() {
        var payload = {
            startupVolume: startupVolume,
            clients: []
        };
        for (var i = 0; i < clients.length; i++) {
            var cl = clientLevels[i] || {volStereoAdj: 0, volMultiAdj: 0, volCal: 0};
            payload.clients.push({
                name: (clients[i] || {}).name || '',
                volStereoAdj: cl.volStereoAdj,
                volMultiAdj: cl.volMultiAdj,
                volCal: cl.volCal
            });
        }

        saveStatus.textContent = 'Saving...';
        saveStatus.className = 'status-text';

        fetch('/api/levels/save', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        }).then(function(r) { return r.json(); })
        .then(function(resp) {
            if (resp.ok) {
                saveStatus.textContent = '✓ Saved';
                saveStatus.className = 'status-text success';
            } else {
                saveStatus.textContent = '✗ ' + (resp.error || 'Failed');
                saveStatus.className = 'status-text error';
            }
            setTimeout(function() { saveStatus.textContent = ''; }, 3000);
        }).catch(function(err) {
            saveStatus.textContent = '✗ ' + err;
            saveStatus.className = 'status-text error';
            setTimeout(function() { saveStatus.textContent = ''; }, 3000);
        });
    }

    function escHtml(s) {
        var div = document.createElement('div');
        div.textContent = s;
        return div.innerHTML;
    }

    function pollMainVolume() {
        fetch('/api/status')
            .then(function(r) { return r.json(); })
            .then(function(d) {
                if (!d || d.txOnline === false) return;
                if (typeof d.mainVolume !== 'number') return;
                if (Date.now() < volLocalUntil) return;
                if (d.mainVolume === mainVolume) return;

                mainVolume = d.mainVolume;
                var slider = document.getElementById('main-vol-slider');
                var valEl = document.getElementById('main-vol-val');
                var dbEl = document.getElementById('main-vol-db');
                if (slider) slider.value = mainVolume;
                if (valEl) valEl.textContent = mainVolume;
                if (dbEl) dbEl.textContent = mainVolToDb(mainVolume);
            })
            .catch(function() {});
    }

    saveBtn.addEventListener('click', save);
    init();
    setInterval(pollMainVolume, VOL_POLL_MS);
})();
