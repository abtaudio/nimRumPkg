(function() {
    'use strict';

    var txSource = document.getElementById('tx-source');
    var txVersionInput = document.getElementById('tx-version-input');
    var txUploadInput = document.getElementById('tx-upload-input');
    var txUpdateBtn = document.getElementById('tx-update-btn');
    var txUpdateStatus = document.getElementById('tx-update-status');
    var txVersionEl = document.getElementById('tx-version');

    var fleetSource = document.getElementById('fleet-source');
    var fleetUploadInput = document.getElementById('fleet-upload-input');
    var fleetUpdateBtn = document.getElementById('fleet-update-btn');
    var fleetUpdateStatus = document.getElementById('fleet-update-status');
    var fleetProgress = document.getElementById('fleet-progress');
    var fleetProgressFill = document.getElementById('fleet-progress-fill');
    var fleetResults = document.getElementById('fleet-results');

    var confirmOverlay = document.getElementById('confirm-overlay');
    var confirmText = document.getElementById('confirm-text');
    var confirmYes = document.getElementById('confirm-yes');
    var confirmNo = document.getElementById('confirm-no');
    var updatingOverlay = document.getElementById('updating-overlay');
    var reconnectCountdown = document.getElementById('reconnect-countdown');

    // --- TX Source selection ---
    txSource.addEventListener('change', function() {
        txVersionInput.style.display = txSource.value === 'pypi-version' ? '' : 'none';
        txUploadInput.style.display = txSource.value === 'upload' ? '' : 'none';
    });

    fleetSource.addEventListener('change', function() {
        fleetUploadInput.style.display = fleetSource.value === 'upload' ? '' : 'none';
    });

    // --- Fetch TX version ---
    var pypiEl = document.getElementById('pypi-latest');

    function fetchTxVersion() {
        fetch('/api/system/tx-version')
            .then(function(r) { return r.json(); })
            .then(function(d) {
                txVersionEl.textContent = d.version || '?';
                fetchPypiLatest(d.version || '');
            });
    }
    fetchTxVersion();

    // --- Fetch PyPI latest version ---
    function fetchPypiLatest(currentVersion) {
        fetch('/api/system/pypi-latest')
            .then(function(r) { return r.json(); })
            .then(function(d) {
                if (d.version) {
                    if (currentVersion && d.version !== currentVersion) {
                        pypiEl.textContent = '(latest on PyPI: ' + d.version + ')';
                        pypiEl.classList.add('update-available');
                    } else if (currentVersion && d.version === currentVersion) {
                        pypiEl.textContent = '(up to date)';
                    }
                }
            })
            .catch(function() {});
    }

    // --- TX Update ---
    txUpdateBtn.addEventListener('click', function() {
        var source = txSource.value;

        if (source === 'upload') {
            var fileInput = document.getElementById('tx-wheel-file');
            if (!fileInput.files.length) {
                txUpdateStatus.textContent = 'Select a .whl file first';
                return;
            }
            var formData = new FormData();
            formData.append('wheel', fileInput.files[0]);
            txUpdateStatus.textContent = 'Uploading...';
            txUpdateBtn.disabled = true;

            fetch('/api/system/upload-wheel', { method: 'POST', body: formData })
                .then(function(r) { return r.json(); })
                .then(function(d) {
                    if (d.error) { txUpdateStatus.textContent = d.error; txUpdateBtn.disabled = false; return; }
                    doTxUpdate({ source: 'wheel', path: d.path });
                });
        } else {
            var version = source === 'pypi-version'
                ? document.getElementById('tx-version-field').value.trim()
                : '';
            doTxUpdate({ source: 'pypi', version: version });
        }
    });

    function doTxUpdate(params) {
        txUpdateStatus.textContent = 'Updating...';
        txUpdateBtn.disabled = true;

        fetch('/api/system/tx-update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(params),
        })
        .then(function(r) { return r.json(); })
        .then(function(d) {
            if (d.error) {
                txUpdateStatus.textContent = 'Error: ' + d.error;
                txUpdateBtn.disabled = false;
            } else {
                showUpdatingOverlay();
            }
        })
        .catch(function() { showUpdatingOverlay(); });
    }

    function showUpdatingOverlay() {
        updatingOverlay.style.display = '';
        var count = 10;
        reconnectCountdown.textContent = count;
        var timer = setInterval(function() {
            count--;
            reconnectCountdown.textContent = count;
            if (count <= 0) {
                clearInterval(timer);
                tryReconnect();
            }
        }, 1000);
    }

    function tryReconnect() {
        fetch('/api/system/tx-version')
            .then(function(r) { return r.json(); })
            .then(function() { window.location.reload(); })
            .catch(function() {
                setTimeout(tryReconnect, 2000);
            });
    }

    // --- Fleet Update ---
    fleetUpdateBtn.addEventListener('click', function() {
        var source = fleetSource.value;

        if (source === 'upload') {
            var fileInput = document.getElementById('fleet-wheel-file');
            if (!fileInput.files.length) {
                fleetUpdateStatus.textContent = 'Select a .whl file first';
                return;
            }
            var formData = new FormData();
            formData.append('wheel', fileInput.files[0]);
            fleetUpdateStatus.textContent = 'Uploading...';
            fleetUpdateBtn.disabled = true;

            fetch('/api/system/upload-wheel', { method: 'POST', body: formData })
                .then(function(r) { return r.json(); })
                .then(function(d) {
                    if (d.error) { fleetUpdateStatus.textContent = d.error; fleetUpdateBtn.disabled = false; return; }
                    startFleetDeploy(d.path);
                });
        } else {
            startFleetDeploy('');
        }
    });

    function startFleetDeploy(wheelPath) {
        fleetUpdateStatus.textContent = 'Starting...';
        fleetUpdateBtn.disabled = true;
        fleetProgress.style.display = '';
        fleetResults.innerHTML = '';
        fleetProgressFill.style.width = '0%';

        fetch('/api/system/fleet-update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ wheel_path: wheelPath }),
        })
        .then(function(r) { return r.json(); })
        .then(function(d) {
            if (d.error) {
                fleetUpdateStatus.textContent = 'Error: ' + d.error;
                fleetUpdateBtn.disabled = false;
                return;
            }
            pollFleetProgress();
        });
    }

    function pollFleetProgress() {
        fetch('/api/system/fleet-update/status')
            .then(function(r) { return r.json(); })
            .then(function(d) {
                var pct = d.total > 0 ? (d.completed / d.total * 100) : 0;
                fleetProgressFill.style.width = pct + '%';
                fleetUpdateStatus.textContent = d.completed + '/' + d.total + ' devices';

                // Render results
                var html = '';
                for (var name in d.results) {
                    var r = d.results[name];
                    var cls = r.ok ? 'ok' : 'error';
                    var label = r.ok ? '✓' : '✗';
                    var title = r.error ? ' title="' + r.error.replace(/"/g, '&quot;') + '"' : '';
                    html += '<span class="device-result ' + cls + '"' + title + '>' + name + ' ' + label + '</span>';
                }
                fleetResults.innerHTML = html;

                if (d.running) {
                    setTimeout(pollFleetProgress, 1000);
                } else {
                    fleetUpdateBtn.disabled = false;
                    var ok = 0, fail = 0;
                    for (var n in d.results) {
                        if (d.results[n].ok) ok++; else fail++;
                    }
                    fleetUpdateStatus.textContent = '✓ ' + ok + ' deployed' + (fail ? ', ✗ ' + fail + ' failed' : '');
                }
            });
    }

    // --- Fleet Actions ---
    document.getElementById('restart-fleet-btn').addEventListener('click', function() {
        showConfirm('Restart all RX processes?', function() {
            fetch('/api/system/restart-fleet', { method: 'POST' });
        });
    });

    document.getElementById('reboot-fleet-btn').addEventListener('click', function() {
        showConfirm('Reboot ALL RX devices? This will cause audio interruption.', function() {
            fetch('/api/system/reboot-fleet', { method: 'POST' });
        });
    });

    document.getElementById('restart-tx-btn').addEventListener('click', function() {
        showConfirm('Restart TX + WebUI?', function() {
            fetch('/api/system/restart-tx', { method: 'POST' });
            showUpdatingOverlay();
        });
    });

    document.getElementById('reboot-tx-btn').addEventListener('click', function() {
        showConfirm('Reboot the TX device? This will cause audio interruption until it comes back up.', function() {
            fetch('/api/system/reboot-tx', { method: 'POST' });
            showUpdatingOverlay();
        });
    });

    // --- Confirm dialog ---
    var _confirmCallback = null;

    function showConfirm(text, onConfirm) {
        confirmText.textContent = text;
        _confirmCallback = onConfirm;
        confirmOverlay.style.display = '';
    }

    confirmYes.addEventListener('click', function() {
        confirmOverlay.style.display = 'none';
        if (_confirmCallback) _confirmCallback();
        _confirmCallback = null;
    });

    confirmNo.addEventListener('click', function() {
        confirmOverlay.style.display = 'none';
        _confirmCallback = null;
    });

    // --- Fleet Device Info ---
    var fleetInfoBtn = document.getElementById('refresh-fleet-info-btn');
    var fleetInfoStatus = document.getElementById('fleet-info-status');
    var fleetInfoTable = document.getElementById('fleet-info-table');
    var fleetInfoTbody = fleetInfoTable.querySelector('tbody');
    var FLEET_INFO_CACHE_KEY = 'nimrum_fleet_device_info';

    fleetInfoBtn.addEventListener('click', fetchFleetInfo);

    // Load cached data on page load
    loadCachedFleetInfo();

    function loadCachedFleetInfo() {
        try {
            var cached = localStorage.getItem(FLEET_INFO_CACHE_KEY);
            if (!cached) return;
            var data = JSON.parse(cached);
            var devices = data.devices || [];
            if (devices.length === 0) return;
            renderFleetInfo(devices);
            var age = formatCacheAge(data.timestamp);
            fleetInfoStatus.textContent = devices.length + ' devices (cached ' + age + ')';
        } catch (e) {
            // Ignore corrupt cache
        }
    }

    function saveFleetInfoCache(devices) {
        try {
            var data = { devices: devices, timestamp: Date.now() };
            localStorage.setItem(FLEET_INFO_CACHE_KEY, JSON.stringify(data));
        } catch (e) {
            // localStorage full or unavailable — ignore
        }
    }

    function formatCacheAge(timestamp) {
        var diff = Date.now() - timestamp;
        var minutes = Math.floor(diff / 60000);
        if (minutes < 1) return 'just now';
        if (minutes < 60) return minutes + 'min ago';
        var hours = Math.floor(minutes / 60);
        if (hours < 24) return hours + 'h ago';
        var days = Math.floor(hours / 24);
        return days + 'd ago';
    }

    function fetchFleetInfo() {
        fleetInfoBtn.disabled = true;
        fleetInfoStatus.textContent = 'Collecting (SSH to all devices)...';
        fleetInfoTbody.innerHTML = '';

        fetch('/api/system/device-info')
            .then(function(r) { return r.json(); })
            .then(function(d) {
                fleetInfoBtn.disabled = false;
                if (d.error) {
                    fleetInfoStatus.textContent = 'Error: ' + d.error;
                    return;
                }
                var devices = d.devices || [];
                fleetInfoStatus.textContent = devices.length + ' devices (updated just now)';
                renderFleetInfo(devices);
                saveFleetInfoCache(devices);
            })
            .catch(function(e) {
                fleetInfoBtn.disabled = false;
                fleetInfoStatus.textContent = 'Failed: ' + e;
            });
    }

    function renderFleetInfo(devices) {
        fleetInfoTbody.innerHTML = '';
        devices.forEach(function(dev) {
            var tr = document.createElement('tr');
            if (dev.error) {
                tr.innerHTML = '<td>' + esc(dev.hostname || '?') + '</td>'
                    + '<td colspan="8" class="error-text">' + esc(dev.error) + '</td>';
            } else {
                var model = dev.model || '';
                if (model.indexOf('OrangePi') >= 0 || model.indexOf('Orange Pi') >= 0) model = 'Orange Pi';
                else if (model.indexOf('Raspberry Pi 4') >= 0) model = 'RPi 4B';
                else if (model.indexOf('Raspberry Pi 3') >= 0) model = 'RPi 3B';
                else if (model.indexOf('Raspberry Pi 2') >= 0) model = 'RPi 2B';
                else if (model.indexOf('Pi Zero 2') >= 0) model = 'RPi Zero2W';
                else model = model.substring(0, 15);

                var os = dev.os || '';
                if (os.toLowerCase().indexOf('trixie') >= 0) os = 'Trixie';
                else if (os.toLowerCase().indexOf('bullseye') >= 0) os = 'Bullseye';
                else if (os.toLowerCase().indexOf('bookworm') >= 0) os = 'Bookworm';
                else os = os.substring(0, 20);

                var arch = dev.arch || '';
                if (arch === 'aarch64') arch = 'arm64';
                else if (arch.indexOf('arm') >= 0) arch = 'armv7';

                var dac = dev.hat || '';
                if (!dac) {
                    var snd = dev.sound || '';
                    if (snd.indexOf('pcm512x') >= 0) dac = 'pcm512x';
                    else if (snd.indexOf('TAS5756') >= 0) dac = 'TAS5756M';
                    else if (snd.indexOf(':') >= 0) dac = snd.split(':')[1].trim().substring(0, 25);
                    else dac = snd.substring(0, 25);
                }

                var uptime = (dev.uptime || '').replace(/^up /, '');

                tr.innerHTML = '<td>' + esc(dev.hostname || '?') + '</td>'
                    + '<td>' + esc(model) + '</td>'
                    + '<td>' + esc(dev.nimrum_version || '?') + '</td>'
                    + '<td>' + esc(arch) + '</td>'
                    + '<td>' + esc(os) + '</td>'
                    + '<td>' + esc(dev.kernel || '?') + '</td>'
                    + '<td>' + esc(dev.wifi || '?') + '</td>'
                    + '<td>' + esc(dac) + '</td>'
                    + '<td>' + esc(uptime) + '</td>';
            }
            fleetInfoTbody.appendChild(tr);
        });
    }

    function esc(s) {
        var d = document.createElement('div');
        d.textContent = s;
        return d.innerHTML;
    }

})();
