(function() {
    'use strict';

    var statusEl = document.getElementById('channelmap-status');
    var tabsEl = document.getElementById('layout-tabs');
    var infoEl = document.getElementById('layout-info');
    var tableEl = document.getElementById('channelmap-table');

    var selectedLayout = null;
    var cachedData = null;
    var dirty = false;

    function poll() {
        fetch('/api/channelmap')
            .then(function(r) { return r.json(); })
            .then(function(data) {
                cachedData = data;
                render();
            })
            .catch(function() {
                statusEl.innerHTML = 'Failed to load channel map data.';
            });
    }

    function render() {
        var data = cachedData;
        if (!data) return;

        var activeLayout = data.activeLayout || '';
        var defaultLayout = data.defaultLayout || 'stereo';
        var channelLayouts = data.channelLayouts || {};
        var clients = data.clients || [];
        var virtualChannels = data.virtualChannels || [];

        // Collect all known layout names
        var layoutNames = new Set();
        if (activeLayout) layoutNames.add(activeLayout);
        if (defaultLayout) layoutNames.add(defaultLayout);
        Object.keys(channelLayouts).forEach(function(k) { layoutNames.add(k); });
        clients.forEach(function(cli) {
            if (cli.layouts) {
                Object.keys(cli.layouts).forEach(function(k) {
                    if (k !== 'default') layoutNames.add(k);
                });
            }
        });
        var layoutList = Array.from(layoutNames).sort();

        if (!selectedLayout || !layoutNames.has(selectedLayout)) {
            selectedLayout = activeLayout || defaultLayout;
        }

        // Status line
        var dirtyTag = dirty ? ' <span style="color:#eab308">(unsaved changes)</span>' : '';
        statusEl.innerHTML = 'Active layout: <span class="active-layout">' +
            (activeLayout || 'none') + '</span> &nbsp;|&nbsp; default_layout: ' +
            defaultLayout + dirtyTag;

        // Layout tabs
        var tabHtml = '';
        layoutList.forEach(function(name) {
            var cls = 'tab';
            if (name === selectedLayout) cls += ' active';
            if (name === activeLayout) cls += ' current';
            tabHtml += '<div class="' + cls + '" data-layout="' + name + '">' +
                name + '</div>';
        });
        tabHtml += '<div class="tab tab-add">+ Add</div>';
        tabsEl.innerHTML = tabHtml;

        tabsEl.querySelectorAll('.tab').forEach(function(tab) {
            tab.addEventListener('click', function() {
                if (tab.classList.contains('tab-add')) {
                    var name = prompt('New layout name (e.g. "5.1"):');
                    if (name && name.trim()) {
                        name = name.trim();
                        if (!cachedData.channelLayouts) cachedData.channelLayouts = {};
                        cachedData.channelLayouts[name] = {channels: {}};
                        selectedLayout = name;
                        dirty = true;
                        render();
                    }
                } else {
                    selectedLayout = tab.getAttribute('data-layout');
                    render();
                }
            });
        });

        // Layout info
        var layoutDef = channelLayouts[selectedLayout];
        var chDefs = '';
        if (layoutDef && layoutDef.channels) {
            var parts = [];
            Object.keys(layoutDef.channels).forEach(function(k) {
                parts.push(k + ':' + layoutDef.channels[k]);
            });
            chDefs = parts.join(', ');
        }
        infoEl.innerHTML = 'Channel defs: ' + (chDefs || '<em>none</em>') +
            ' &nbsp;<button id="edit-ch-defs-btn" class="btn-sm">Edit</button>';
        document.getElementById('edit-ch-defs-btn').addEventListener('click', function() {
            var current = chDefs || '0:FL, 1:FR';
            var input = prompt('Channel definitions (e.g. "0:FL, 1:FR, 2:FC"):', current);
            if (input !== null) {
                var channels = {};
                input.split(',').forEach(function(pair) {
                    var kv = pair.trim().split(':');
                    if (kv.length === 2) {
                        channels[kv[0].trim()] = kv[1].trim();
                    }
                });
                if (!cachedData.channelLayouts) cachedData.channelLayouts = {};
                if (!cachedData.channelLayouts[selectedLayout]) {
                    cachedData.channelLayouts[selectedLayout] = {};
                }
                cachedData.channelLayouts[selectedLayout].channels = channels;
                dirty = true;
                render();
            }
        });

        // Role lookup
        var roleMap = {};
        if (layoutDef && layoutDef.channels) {
            Object.keys(layoutDef.channels).forEach(function(k) {
                roleMap[parseInt(k)] = layoutDef.channels[k];
            });
        }

        // Virtual channel lookup
        var virtualMap = {};
        virtualChannels.forEach(function(vc) {
            var desc = '';
            if (vc.crossfaderPosition !== undefined) {
                desc = 'xfade(' + vc.crossfaderChannelA + ',' +
                    vc.crossfaderChannelB + ')@' + vc.crossfaderPosition + '%';
            } else if (vc.toneGeneratorMode) {
                desc = 'tone:' + vc.toneGeneratorMode;
            }
            virtualMap[vc.channelNumber] = desc;
        });

        // Editable table
        var html = '<table>';
        html += '<tr><th>Device</th><th>Location</th><th>Channel</th>' +
            '<th>Role</th></tr>';

        clients.forEach(function(cli, idx) {
            var layouts = cli.layouts || {};
            var entry = layouts[selectedLayout];
            if (entry === undefined) entry = layouts['default'];

            var ch = (entry && entry !== null) ? entry.channel : null;
            var isSilent = (ch === null || ch === undefined);

            // Channel select
            var chSelect = '<select data-idx="' + idx + '" data-field="channel">';
            chSelect += '<option value=""' + (isSilent ? ' selected' : '') + '>— (silent)</option>';
            for (var c = 0; c <= 15; c++) {
                var label = String(c);
                if (roleMap[c]) label += ' (' + roleMap[c] + ')';
                chSelect += '<option value="' + c + '"' + (ch === c ? ' selected' : '') + '>' + label + '</option>';
            }
            for (var v = 16; v <= 31; v++) {
                var vlabel = v + ' (virt)';
                if (virtualMap[v]) vlabel = v + ' ' + virtualMap[v];
                chSelect += '<option value="' + v + '"' + (ch === v ? ' selected' : '') + '>' + vlabel + '</option>';
            }
            chSelect += '</select>';

            // Role display
            var roleText = '';
            if (!isSilent) {
                if (ch >= 16) {
                    roleText = '<span class="virtual">(virt)</span>';
                } else if (roleMap[ch]) {
                    roleText = '<span class="role">' + roleMap[ch] + '</span>';
                }
            }

            var rowClass = isSilent ? ' class="silent"' : '';
            html += '<tr' + rowClass + '>';
            html += '<td>' + cli.name + '</td>';
            html += '<td>' + (cli.location || '') + '</td>';
            html += '<td>' + chSelect + '</td>';
            html += '<td>' + roleText + '</td>';
            html += '</tr>';
        });

        html += '</table>';
        html += '<div class="toolbar">';
        html += '<button id="save-btn"' + (dirty ? '' : ' disabled') + '>Save</button>';
        html += '<button id="revert-btn" class="btn-secondary">Revert</button>';
        html += '</div>';
        tableEl.innerHTML = html;

        // Event: channel dropdown change
        tableEl.querySelectorAll('select[data-field="channel"]').forEach(function(sel) {
            sel.addEventListener('change', function() {
                var idx = parseInt(sel.getAttribute('data-idx'));
                var val = sel.value;
                applyChange(idx, val === '' ? null : parseInt(val));
            });
        });

        // Save button
        var saveBtn = document.getElementById('save-btn');
        if (saveBtn) {
            saveBtn.addEventListener('click', doSave);
        }
        // Revert button
        var revertBtn = document.getElementById('revert-btn');
        if (revertBtn) {
            revertBtn.addEventListener('click', function() {
                dirty = false;
                poll();
            });
        }
    }

    function applyChange(clientIdx, channelVal) {
        var cli = cachedData.clients[clientIdx];
        if (!cli.layouts) cli.layouts = {};

        if (channelVal === null) {
            cli.layouts[selectedLayout] = null;
        } else {
            var existing = cli.layouts[selectedLayout];
            if (!existing || existing === null) {
                cli.layouts[selectedLayout] = {channel: channelVal, level: 0};
            } else {
                existing.channel = channelVal;
            }
        }
        dirty = true;
        render();
    }

    function doSave() {
        var payload = {
            channelLayouts: cachedData.channelLayouts || {},
            default_layout: cachedData.defaultLayout || 'stereo',
            clients: cachedData.clients.map(function(cli) {
                return {name: cli.name, layouts: cli.layouts || {}};
            }),
        };

        fetch('/api/channelmap/save', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload),
        })
        .then(function(r) { return r.json(); })
        .then(function(resp) {
            if (resp.ok) {
                dirty = false;
                statusEl.innerHTML += ' <span style="color:#22c55e">✓ Saved. Restart TX to apply.</span>';
                setTimeout(poll, 2000);
            } else {
                alert('Save failed: ' + (resp.error || 'unknown'));
            }
        })
        .catch(function(err) {
            alert('Save failed: ' + err);
        });
    }

    poll();
    setInterval(function() { if (!dirty) poll(); }, 2000);
})();
