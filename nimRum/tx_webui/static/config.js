(function() {
    'use strict';

    var txEditor = document.getElementById('tx-config-editor');
    var txStatus = document.getElementById('tx-config-status');
    var txConfigPath = '';

    var deviceSelect = document.getElementById('device-select');
    var deviceEditor = document.getElementById('device-config-editor');
    var deviceEditorWrap = document.getElementById('device-editor-wrap');
    var deviceFilename = document.getElementById('device-filename');
    var deviceStatus = document.getElementById('device-config-status');
    var currentDeviceFilename = '';

    // --- TX Config ---
    function loadTxConfig() {
        fetch('/api/config/tx')
            .then(function(r) { return r.json(); })
            .then(function(d) {
                if (d.error) { txStatus.textContent = d.error; return; }
                txEditor.value = d.content;
                txConfigPath = d.path;
                txStatus.textContent = '';
            });
    }

    document.getElementById('tx-config-reload').addEventListener('click', function() {
        loadTxConfig();
        txStatus.textContent = 'Reloaded';
    });

    document.getElementById('tx-config-save').addEventListener('click', function() {
        fetch('/api/config/tx', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content: txEditor.value, path: txConfigPath }),
        })
        .then(function(r) { return r.json(); })
        .then(function(d) {
            txStatus.textContent = d.ok ? '✓ Saved (restart TX to apply)' : 'Error: ' + d.error;
        });
    });

    document.getElementById('tx-config-save-restart').addEventListener('click', function() {
        fetch('/api/config/tx', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content: txEditor.value, path: txConfigPath }),
        })
        .then(function(r) { return r.json(); })
        .then(function(d) {
            if (!d.ok) { txStatus.textContent = 'Error: ' + d.error; return; }
            txStatus.textContent = '✓ Saved, restarting TX...';
            fetch('/api/system/restart-tx', { method: 'POST' });
            setTimeout(function() { location.reload(); }, 8000);
        });
    });

    // Allow Tab key in textarea
    txEditor.addEventListener('keydown', function(e) {
        if (e.key === 'Tab') {
            e.preventDefault();
            var start = this.selectionStart;
            this.value = this.value.substring(0, start) + '  ' + this.value.substring(this.selectionEnd);
            this.selectionStart = this.selectionEnd = start + 2;
        }
    });

    deviceEditor.addEventListener('keydown', function(e) {
        if (e.key === 'Tab') {
            e.preventDefault();
            var start = this.selectionStart;
            this.value = this.value.substring(0, start) + '  ' + this.value.substring(this.selectionEnd);
            this.selectionStart = this.selectionEnd = start + 2;
        }
    });

    // --- Device Config ---
    function populateDevices() {
        fetch('/api/devices')
            .then(function(r) { return r.json(); })
            .then(function(d) {
                var html = '<option value="">— select —</option>';
                (d.devices || []).forEach(function(dev) {
                    // Show RX devices (exclude SRC-only devices)
                    var roles = dev.roles || [];
                    if (roles.indexOf('rx') >= 0 || roles.length === 0) {
                        html += '<option value="' + dev.hostName + '">' + dev.hostName +
                                (dev.online ? '' : ' (offline)') + '</option>';
                    }
                });
                deviceSelect.innerHTML = html;
            });
    }

    document.getElementById('device-fetch-btn').addEventListener('click', function() {
        var name = deviceSelect.value;
        if (!name) return;
        deviceStatus.textContent = 'Fetching...';
        deviceEditorWrap.style.display = '';

        fetch('/api/config/device/' + encodeURIComponent(name))
            .then(function(r) { return r.json(); })
            .then(function(d) {
                if (d.error) {
                    deviceStatus.textContent = 'Error: ' + d.error;
                    deviceEditor.value = '';
                    return;
                }
                deviceEditor.value = d.content;
                currentDeviceFilename = d.filename;
                deviceFilename.textContent = d.filename;
                deviceStatus.textContent = '';
            });
    });

    document.getElementById('device-config-save').addEventListener('click', function() {
        var name = deviceSelect.value;
        if (!name || !currentDeviceFilename) return;

        // Client-side guard for the failure that crash-loops an RX: a numeric
        // field carrying a non-integer (e.g. "staticDelay_us: 710us"). Valid
        // YAML, but ctypes rejects the str and the device restart-loops. The
        // server validates too; this just gives instant feedback.
        if (/rx/i.test(currentDeviceFilename)) {
            var intFields = ['staticDelay_us', 'outputChannelEnable',
                             'logEnable', 'forceS16'];
            var lines = deviceEditor.value.split('\n');
            var badField = null, badVal = null;
            for (var i = 0; i < lines.length && !badField; i++) {
                var m = lines[i].match(/^\s*([A-Za-z_]+)\s*:\s*(.+?)\s*$/);
                if (!m) continue;
                if (intFields.indexOf(m[1]) === -1) continue;
                var v = m[2].replace(/^["']|["']$/g, '');
                if (!/^-?\d+$/.test(v)) { badField = m[1]; badVal = v; }
            }
            if (badField) {
                deviceStatus.textContent = 'Error: ' + badField +
                    ' must be an integer, got "' + badVal +
                    '". Remove any unit suffix (write 710, not 710us).';
                return;
            }
        }

        deviceStatus.textContent = 'Saving...';

        fetch('/api/config/device/' + encodeURIComponent(name), {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                filename: currentDeviceFilename,
                content: deviceEditor.value,
                restart: true,
            }),
        })
        .then(function(r) { return r.json(); })
        .then(function(d) {
            deviceStatus.textContent = d.ok ? '✓ Saved & restarting' : 'Error: ' + d.error;
        })
        .catch(function(e) {
            deviceStatus.textContent = 'Error: ' + e;
        });
    });

    // --- SRC Config ---
    var srcSelect = document.getElementById('src-select');
    var srcEditor = document.getElementById('src-config-editor');
    var srcEditorWrap = document.getElementById('src-editor-wrap');
    var srcFilename = document.getElementById('src-filename');
    var srcStatus = document.getElementById('src-config-status');
    var currentSrcFilename = '';

    function populateSrcDevices() {
        fetch('/api/devices')
            .then(function(r) { return r.json(); })
            .then(function(d) {
                var html = '<option value="">— select —</option>';
                (d.devices || []).forEach(function(dev) {
                    var roles = dev.roles || [];
                    if (roles.indexOf('src') >= 0) {
                        html += '<option value="' + dev.hostName + '">' + dev.hostName +
                                (dev.online ? '' : ' (offline)') + '</option>';
                    }
                });
                srcSelect.innerHTML = html;
            });
    }

    document.getElementById('src-fetch-btn').addEventListener('click', function() {
        var name = srcSelect.value;
        if (!name) return;
        srcStatus.textContent = 'Fetching...';
        srcEditorWrap.style.display = '';

        fetch('/api/config/device/' + encodeURIComponent(name) + '?prefer=src')
            .then(function(r) { return r.json(); })
            .then(function(d) {
                if (d.error) {
                    srcStatus.textContent = 'Error: ' + d.error;
                    srcEditor.value = '';
                    return;
                }
                srcEditor.value = d.content;
                currentSrcFilename = d.filename;
                srcFilename.textContent = d.filename;
                srcStatus.textContent = '';
            });
    });

    document.getElementById('src-config-save').addEventListener('click', function() {
        var name = srcSelect.value;
        if (!name || !currentSrcFilename) return;
        srcStatus.textContent = 'Saving...';

        fetch('/api/config/device/' + encodeURIComponent(name), {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                filename: currentSrcFilename,
                content: srcEditor.value,
                restart: true,
            }),
        })
        .then(function(r) { return r.json(); })
        .then(function(d) {
            srcStatus.textContent = d.ok ? '✓ Saved & restarting' : 'Error: ' + d.error;
        });
    });

    srcEditor.addEventListener('keydown', function(e) {
        if (e.key === 'Tab') {
            e.preventDefault();
            var start = this.selectionStart;
            this.value = this.value.substring(0, start) + '  ' + this.value.substring(this.selectionEnd);
            this.selectionStart = this.selectionEnd = start + 2;
        }
    });

    // --- Init ---
    loadTxConfig();
    populateDevices();
    populateSrcDevices();
    loadConfigReference();

    // --- Config Reference (loaded from backend, single source of truth) ---
    function loadConfigReference() {
        fetch('/api/config/reference')
            .then(function(r) { return r.json(); })
            .then(function(ref) {
                render('ref-rx', ref.rxConfig);
                render('ref-tx', ref.txConfig);
                render('ref-src', ref.audioSourceConfig);
            });

        // Group the flat field list by the section heading it came from, so a new
        // table in the Markdown source shows up here without a JS change.
        function render(mountId, fields) {
            var mount = document.getElementById(mountId);
            if (!mount) return;
            if (!fields || fields.length === 0) {
                mount.innerHTML = '<p>No reference available.</p>';
                return;
            }
            var order = [];
            var bySection = {};
            for (var i = 0; i < fields.length; i++) {
                var s = fields[i].section || '';
                if (!bySection[s]) { bySection[s] = []; order.push(s); }
                bySection[s].push(fields[i]);
            }
            var html = '';
            for (var j = 0; j < order.length; j++) {
                var rows = bySection[order[j]];
                if (j > 0 && order[j]) html += '<h4>' + order[j] + '</h4>';
                var anyDefault = false;
                for (var k = 0; k < rows.length; k++) {
                    if (rows[k]['default']) { anyDefault = true; break; }
                }
                html += renderRefTable(rows, anyDefault);
            }
            mount.innerHTML = html;
        }
    }

    function renderRefTable(fields, showDefault) {
        var html = '<table class="ref-table"><tr><th>Key</th>';
        if (showDefault) html += '<th>Default</th>';
        html += '<th>Description</th></tr>';
        for (var i = 0; i < fields.length; i++) {
            var f = fields[i];
            html += '<tr><td>' + f.key + '</td>';
            if (showDefault) html += '<td>' + (f['default'] || '') + '</td>';
            html += '<td>' + f.desc + '</td></tr>';
        }
        html += '</table>';
        return html;
    }

    // --- Helpers: Populate unconfigured devices dropdown ---
    function populateUnconfiguredDevices() {
        fetch('/api/devices')
            .then(function(r) { return r.json(); })
            .then(function(d) {
                var select = document.getElementById('h-cli-name');
                var html = '<option value="">— select unconfigured device —</option>';
                (d.devices || []).forEach(function(dev) {
                    if (!dev.isConfigured) {
                        html += '<option value="' + dev.hostName + '">' + dev.hostName +
                                ' (' + dev.ip + ')</option>';
                    }
                });
                select.innerHTML = html;
            });
    }
    populateUnconfiguredDevices();

    // --- Helpers: Insert client ---
    document.getElementById('h-cli-insert').addEventListener('click', function() {
        var name = document.getElementById('h-cli-name').value ||
                   document.getElementById('h-cli-name-manual').value.trim();
        if (!name) { alert('Select or type a device name'); return; }
        var location = document.getElementById('h-cli-location').value.trim();
        var stereo = document.getElementById('h-cli-stereo').value.trim() || '0';
        var offset = document.getElementById('h-cli-offset').value || '0';
        var voladj = document.getElementById('h-cli-voladj').value || '0';

        var channels = stereo.split(',').map(function(s) { return s.trim(); });
        var chYaml = channels.length === 1 ? '[' + channels[0] + ']' : '[' + channels.join(', ') + ']';

        var yaml = '    - name: ' + name + '\n';
        if (location) yaml += '      location: ' + location + '\n';
        yaml += '      stereoChannel: ' + chYaml + '\n';
        yaml += '      multiChannel: ' + chYaml + '\n';
        if (offset !== '0') yaml += '      offset_us: ' + offset + '\n';
        if (voladj !== '0') yaml += '      volStereoAdj: ' + voladj + '\n';

        // Find insertion point: before "virtualChannels:" or before end of clients list
        var text = txEditor.value;
        var insertPos = -1;

        // Try to insert before virtualChannels section
        var vcIdx = text.indexOf('\n  virtualChannels:');
        if (vcIdx < 0) vcIdx = text.indexOf('\nvirtualChannels:');
        if (vcIdx >= 0) {
            // Insert before virtualChannels, with blank line
            insertPos = vcIdx;
            yaml = yaml + '\n';
        }

        if (insertPos >= 0) {
            txEditor.value = text.substring(0, insertPos) + '\n' + yaml + text.substring(insertPos);
        } else {
            // Fallback: find end of clients list (last "      name:" line)
            var lines = text.split('\n');
            var lastClientLine = -1;
            for (var i = 0; i < lines.length; i++) {
                if (lines[i].match(/^\s+name:/)) lastClientLine = i;
            }
            if (lastClientLine >= 0) {
                // Find end of this client block (next line with "    -" or less indentation)
                var insertLine = lastClientLine + 1;
                while (insertLine < lines.length &&
                       lines[insertLine].match(/^\s{6}/) &&
                       !lines[insertLine].match(/^\s{4}-/)) {
                    insertLine++;
                }
                lines.splice(insertLine, 0, yaml.trimEnd());
                txEditor.value = lines.join('\n');
            } else {
                txEditor.value += '\n' + yaml;
            }
        }

        txStatus.textContent = '← Client "' + name + '" inserted (save to apply)';
    });

    // --- Helpers: Insert virtual channel ---
    document.getElementById('h-vc-insert').addEventListener('click', function() {
        var chnum = document.getElementById('h-vc-chnum').value || '10';
        var srcA = document.getElementById('h-vc-src-a').value || '0';
        var srcB = document.getElementById('h-vc-src-b').value || '1';
        var fader = document.getElementById('h-vc-fader').value || '50';

        var yaml = '  - channelNumber: ' + chnum + '\n';
        yaml += '    crossfaderChannelA: ' + srcA + '\n';
        yaml += '    crossfaderChannelB: ' + srcB + '\n';
        yaml += '    crossfaderPosition: ' + fader + '\n';

        var text = txEditor.value;

        // Find virtualChannels section
        var vcIdx = text.indexOf('virtualChannels:');
        if (vcIdx >= 0) {
            // Append at end of file (virtualChannels is typically last section)
            txEditor.value = text.trimEnd() + '\n\n' + yaml;
        } else {
            // No virtualChannels section yet — add it
            txEditor.value = text.trimEnd() + '\n\n  virtualChannels:\n' + yaml;
        }

        txStatus.textContent = '← Virtual channel ' + chnum + ' inserted (save to apply)';
    });

    // --- Helpers: Insert tone generator virtual channel ---
    document.getElementById('h-tg-insert').addEventListener('click', function() {
        var chnum = document.getElementById('h-tg-chnum').value || '13';
        var mode = document.getElementById('h-tg-mode').value || 'sinus';
        var freq = document.getElementById('h-tg-freq').value || '1000';
        var volume = document.getElementById('h-tg-volume').value || '80';

        var yaml = '  - channelNumber: ' + chnum + '\n';
        yaml += '    toneGeneratorMode: ' + mode + '\n';
        if (mode === 'sinus') {
            yaml += '    toneGeneratorFreq: ' + freq + '\n';
        }
        yaml += '    toneGeneratorVolume: ' + volume + '\n';

        var text = txEditor.value;

        // Find virtualChannels section
        var vcIdx = text.indexOf('virtualChannels:');
        if (vcIdx >= 0) {
            // Append at end of virtualChannels section
            txEditor.value = text.trimEnd() + '\n\n' + yaml;
        } else {
            // No virtualChannels section yet — add it
            txEditor.value = text.trimEnd() + '\n\n  virtualChannels:\n' + yaml;
        }

        txStatus.textContent = '← Tone generator on ch ' + chnum + ' (' + mode +
            (mode === 'sinus' ? ' ' + freq + 'Hz' : '') + ') inserted (save to apply)';
    });
})();
