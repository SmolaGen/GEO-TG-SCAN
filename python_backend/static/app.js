/**
 * Telegram Live Geo Inspector - Static Vanilla Frontend
 * Zero client-side anomaly calculation: backend detector.py is the single source of truth.
 * Handles token extraction from URL hash, Leaflet map with gap handling,
 * AudioContext unlock, dynamic title/favicon, scrubber, and GPX/CSV exports.
 */

(function () {
  'use strict';

  // --- 1. Security & Token Handling ---
  let authToken = '';
  const hash = window.location.hash;
  if (hash && hash.includes('token=')) {
    const match = hash.match(/token=([^&]+)/);
    if (match && match[1]) {
      authToken = decodeURIComponent(match[1]);
      sessionStorage.setItem('tg_geo_token', authToken);
    }
    // Immediately strip token from address bar
    window.history.replaceState(null, '', window.location.pathname + window.location.search);
  } else {
    authToken = sessionStorage.getItem('tg_geo_token') || window.__BOOTSTRAP_AUTH_TOKEN__ || '';
    if (authToken && !sessionStorage.getItem('tg_geo_token')) {
      sessionStorage.setItem('tg_geo_token', authToken);
    }
  }

  function apiFetch(url, options = {}) {
    options.headers = options.headers || {};
    if (authToken) {
      options.headers['Authorization'] = `Bearer ${authToken}`;
    }
    return fetch(url, options);
  }

  // --- 2. Audio State & Web Audio API ---
  let audioCtx = null;
  let soundEnabled = false;

  function initAudio() {
    if (!audioCtx) {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (AudioContextClass) {
        audioCtx = new AudioContextClass();
      }
    }
    if (audioCtx && audioCtx.state === 'suspended') {
      audioCtx.resume();
    }
    soundEnabled = true;
    const banner = document.getElementById('soundBanner');
    if (banner) banner.classList.add('hidden');
  }

  function playTone(freq, type, duration, delay = 0) {
    if (!soundEnabled || !audioCtx) return;
    try {
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.type = type;
      osc.frequency.setValueAtTime(freq, audioCtx.currentTime + delay);
      gain.gain.setValueAtTime(0.12, audioCtx.currentTime + delay);
      gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + delay + duration);
      osc.connect(gain);
      gain.connect(audioCtx.destination);
      osc.start(audioCtx.currentTime + delay);
      osc.stop(audioCtx.currentTime + delay + duration);
    } catch (e) {
      // Audio error ignored
    }
  }

  function playSuspiciousAlert() {
    // Gentle warning tone
    playTone(520, 'sine', 0.2, 0);
    playTone(660, 'sine', 0.3, 0.15);
  }

  function playFlaggedAlert() {
    // Double urgent alert tone
    playTone(440, 'triangle', 0.2, 0);
    playTone(330, 'triangle', 0.3, 0.18);
  }

  // --- 3. Dynamic Title & Favicon ---
  function updateTabPresence(status, statusText) {
    const favicon = document.getElementById('favicon');
    let titlePrefix = '[✓]';
    let color = '%2310b981';

    if (status === 'flagged') {
      titlePrefix = '[!]';
      color = '%23ef4444';
    } else if (status === 'suspicious') {
      titlePrefix = '[?]';
      color = '%23f59e0b';
    }

    document.title = `${titlePrefix} ${statusText} | Telegram Geo Inspector`;
    if (favicon) {
      favicon.href = `data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><circle cx='50' cy='50' r='46' fill='${color}'/><polygon points='50,20 62,55 50,48 38,55' fill='white'/></svg>`;
    }
  }

  // --- 4. Application State ---
  let contacts = [];
  let activeContactId = '';
  let contactPoints = [];
  let contactGaps = [];
  let currentIndex = 0;
  let isPlaying = false;
  let playbackSpeed = 1;
  let playInterval = null;
  let followLive = true;
  let filterTableAnomalies = false;
  let filterOnlyCurrentPoint = false;
  let prevAlertStatus = 'clean';

  // --- 5. Leaflet Map Initialization ---
  const map = L.map('map', {
    center: [55.751244, 37.618423],
    zoom: 13,
    attributionControl: false
  });

  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
  }).addTo(map);

  let trackLayerGroup = L.layerGroup().addTo(map);
  let currentMarker = null;
  let accuracyCircle = null;

  function renderMap() {
    trackLayerGroup.clearLayers();

    if (contactPoints.length === 0) return;

    // Segmented polylines across gaps
    let currentSegment = [];
    const segments = [];
    const gapLines = [];

    for (let i = 0; i < contactPoints.length; i++) {
      const pt = contactPoints[i];
      if (pt.is_after_gap && i > 0) {
        if (currentSegment.length > 0) {
          segments.push(currentSegment);
          currentSegment = [];
        }
        // Gray dashed line to represent data gap
        const prevPt = contactPoints[i - 1];
        gapLines.push([[prevPt.lat, prevPt.lon], [pt.lat, pt.lon]]);
      }
      currentSegment.push([pt.lat, pt.lon]);

      // Static pin markers
      if (pt.point_type === 'static') {
        const staticPin = L.marker([pt.lat, pt.lon], {
          icon: L.divIcon({
            className: 'custom-static-pin',
            html: '📍',
            iconSize: [24, 24]
          })
        }).bindPopup(`<b>Статический пин</b><br>Правка #${pt.edit_number}<br>Время: ${new Date(pt.edit_date * 1000).toLocaleTimeString()}`);
        trackLayerGroup.addLayer(staticPin);
      }
    }
    if (currentSegment.length > 0) {
      segments.push(currentSegment);
    }

    // Render solid track segments
    segments.forEach(seg => {
      L.polyline(seg, {
        color: '#3b82f6',
        weight: 4,
        opacity: 0.85
      }).addTo(trackLayerGroup);
    });

    // Render gap dashed lines in neutral gray
    gapLines.forEach(gLine => {
      L.polyline(gLine, {
        color: '#94a3b8',
        weight: 2,
        dashArray: '6, 8',
        opacity: 0.7
      }).addTo(trackLayerGroup);
    });

    // Render current scrubbed point marker
    const activePt = contactPoints[currentIndex] || contactPoints[contactPoints.length - 1];
    if (activePt) {
      const headingDeg = activePt.heading || 0;
      const markerHtml = `
        <div class="custom-heading-pin" style="transform: rotate(${headingDeg}deg);">
          ▲
        </div>
      `;

      if (currentMarker) {
        currentMarker.setLatLng([activePt.lat, activePt.lon]);
        currentMarker.setIcon(L.divIcon({
          className: '',
          html: markerHtml,
          iconSize: [28, 28],
          iconAnchor: [14, 14]
        }));
      } else {
        currentMarker = L.marker([activePt.lat, activePt.lon], {
          icon: L.divIcon({
            className: '',
            html: markerHtml,
            iconSize: [28, 28],
            iconAnchor: [14, 14]
          })
        }).addTo(map);
      }

      // Accuracy circle
      const acc = activePt.accuracy || 15;
      if (accuracyCircle) {
        accuracyCircle.setLatLng([activePt.lat, activePt.lon]);
        accuracyCircle.setRadius(acc);
      } else {
        accuracyCircle = L.circle([activePt.lat, activePt.lon], {
          radius: acc,
          color: '#38bdf8',
          fillColor: '#38bdf8',
          fillOpacity: 0.15,
          weight: 1
        }).addTo(map);
      }

      if (followLive) {
        map.panTo([activePt.lat, activePt.lon]);
      }
    }
  }

  // --- 6. Telemetry & Metrics Rendering ---
  function updateUI() {
    const activePt = contactPoints[currentIndex] || contactPoints[contactPoints.length - 1];

    if (!activePt) {
      document.getElementById('statusTextVal').textContent = 'аномалий не найдено';
      document.getElementById('statusTextVal').className = 'status-clean';
      document.getElementById('statusIndicator').textContent = '🟢';
      document.getElementById('statusScoreBadge').textContent = '0 / 100';
      document.getElementById('sessionSummaryText').textContent = 'аномалий не найдено';
      document.getElementById('tablePointsCount').textContent = '0';
      document.getElementById('timelineSlider').max = 0;
      document.getElementById('timelineSlider').value = 0;
      document.getElementById('scrubTotalCount').textContent = '0 / 0';
      return;
    }

    // Determine max score and overall status across session
    let sessionMaxScore = 0;
    let worstStatus = 'clean';
    let worstStatusText = 'аномалий не найдено';

    contactPoints.forEach(p => {
      const sc = p.anomaly_score || 0;
      if (sc > sessionMaxScore) sessionMaxScore = sc;
      if (sc >= 50) {
        worstStatus = 'flagged';
        worstStatusText = p.status_text || 'найдены аномалии';
      } else if (sc >= 20 && worstStatus !== 'flagged') {
        worstStatus = 'suspicious';
        worstStatusText = p.status_text || 'есть подозрительные признаки';
      }
    });

    const statusValElem = document.getElementById('statusTextVal');
    const statusIndicator = document.getElementById('statusIndicator');
    const scoreBadge = document.getElementById('statusScoreBadge');
    const sessionSummary = document.getElementById('sessionSummaryText');

    scoreBadge.textContent = `${sessionMaxScore} / 100`;

    if (worstStatus === 'flagged') {
      statusValElem.textContent = worstStatusText;
      statusValElem.className = 'status-flagged';
      statusIndicator.textContent = '🔴';
      sessionSummary.textContent = worstStatusText;
    } else if (worstStatus === 'suspicious') {
      statusValElem.textContent = worstStatusText;
      statusValElem.className = 'status-suspicious';
      statusIndicator.textContent = '🟡';
      sessionSummary.textContent = worstStatusText;
    } else {
      statusValElem.textContent = 'аномалий не найдено';
      statusValElem.className = 'status-clean';
      statusIndicator.textContent = '🟢';
      sessionSummary.textContent = 'аномалий не найдено';
    }

    // Sound chimes on state shift
    if (worstStatus !== prevAlertStatus) {
      if (worstStatus === 'flagged') playFlaggedAlert();
      else if (worstStatus === 'suspicious') playSuspiciousAlert();
      prevAlertStatus = worstStatus;
    }

    // Dynamic browser tab title and favicon
    updateTabPresence(worstStatus, worstStatusText);

    // Freshness
    const nowSec = Math.floor(Date.now() / 1000);
    const diffSec = Math.max(0, nowSec - (activePt.edit_date || nowSec));
    const freshnessElem = document.getElementById('freshnessBadge');
    const timerElem = document.getElementById('lastUpdatedTimer');

    timerElem.textContent = diffSec === 0 ? 'только что' : `${diffSec} с назад`;

    if (activePt.point_type === 'static') {
      freshnessElem.textContent = '⏹ Статичная метка';
      freshnessElem.style.color = '#a855f7';
    } else if (diffSec < 45) {
      freshnessElem.textContent = '● В сети (Online)';
      freshnessElem.style.color = '#10b981';
    } else if (diffSec < 180) {
      freshnessElem.textContent = '○ Свежие (Recent)';
      freshnessElem.style.color = '#f59e0b';
    } else {
      freshnessElem.textContent = '◌ Устарели (Stale)';
      freshnessElem.style.color = '#ef4444';
    }

    // Telemetry
    document.getElementById('pointEditNumber').textContent = `Правка #${activePt.edit_number || 1}`;
    document.getElementById('metricSpeed').textContent = `Скорость: ${(activePt.speed_kmh || 0).toFixed(1)} км/ч`;
    document.getElementById('metricAccuracy').textContent = `Точность: ±${(activePt.accuracy || 10).toFixed(1)} м`;
    document.getElementById('metricHeading').textContent = `Курс: ${activePt.heading !== null && activePt.heading !== undefined ? activePt.heading + '°' : 'н/д'}`;

    // Scrubber elements
    const slider = document.getElementById('timelineSlider');
    slider.max = Math.max(0, contactPoints.length - 1);
    slider.value = currentIndex;
    document.getElementById('scrubTotalCount').textContent = `${currentIndex + 1} / ${contactPoints.length}`;
    document.getElementById('scrubCurrentTime').textContent = new Date(activePt.edit_date * 1000).toLocaleTimeString();

    renderAnomaliesList();
    renderHistoryTable();
    renderMap();
  }

  function renderAnomaliesList() {
    const container = document.getElementById('anomalyListContainer');
    container.innerHTML = '';

    const activePt = contactPoints[currentIndex];
    let flagsToShow = [];

    if (filterOnlyCurrentPoint && activePt) {
      flagsToShow = (activePt.anomaly_flags || []).map(f => ({ ...f, pointNum: activePt.edit_number }));
    } else {
      // All flags in current session
      contactPoints.forEach(p => {
        (p.anomaly_flags || []).forEach(f => {
          flagsToShow.push({ ...f, pointNum: p.edit_number });
        });
      });
    }

    if (flagsToShow.length === 0) {
      container.innerHTML = `
        <div style="color: var(--text-muted); font-size: 0.8rem; text-align: center; padding: 2rem 1rem;">
          Аномалий не обнаружено. Данные соответствуют физически правдоподобному перемещению.
        </div>
      `;
      return;
    }

    flagsToShow.forEach(f => {
      const item = document.createElement('div');
      let cssClass = 'flag-item';
      if (f.weight === 0) cssClass += ' flight';
      else if (f.weight < 30) cssClass += ' suspicious';

      item.className = cssClass;
      item.innerHTML = `
        <div class="flag-header">
          <span>${f.rule_name}</span>
          <span>${f.weight > 0 ? '+' + f.weight + ' б.' : '0 б.'}</span>
        </div>
        <div class="flag-expl">${f.explanation}</div>
        <div class="flag-meta">Правка #${f.pointNum} | Значение: ${f.observed_value} (Порог: ${f.threshold})</div>
      `;
      container.appendChild(item);
    });
  }

  function renderHistoryTable() {
    const tbody = document.getElementById('historyTableBody');
    tbody.innerHTML = '';
    document.getElementById('tablePointsCount').textContent = contactPoints.length;

    let pts = contactPoints.map((p, idx) => ({ ...p, originalIdx: idx }));
    if (filterTableAnomalies) {
      pts = pts.filter(p => (p.anomaly_score || 0) >= 20 || (p.anomaly_flags && p.anomaly_flags.length > 0));
    }

    if (pts.length === 0) {
      tbody.innerHTML = `<tr><td colspan="10" style="text-align: center; color: var(--text-muted);">Точек не найдено</td></tr>`;
      return;
    }

    pts.forEach(p => {
      const tr = document.createElement('tr');
      if (p.originalIdx === currentIndex) tr.classList.add('selected-row');
      if ((p.anomaly_score || 0) >= 20) tr.classList.add('anomaly-row');

      const timeStr = new Date(p.edit_date * 1000).toLocaleTimeString();
      const flagSummary = (p.anomaly_flags || []).map(f => f.rule_name).join(', ') || '—';

      tr.innerHTML = `
        <td>${p.edit_number}</td>
        <td>${timeStr}</td>
        <td>${p.point_type === 'live' ? 'Live' : 'Static'}</td>
        <td>${p.lat.toFixed(5)}, ${p.lon.toFixed(5)}</td>
        <td>${p.distance_from_prev ? p.distance_from_prev.toFixed(0) + ' м' : '0 м'}</td>
        <td>${p.speed_kmh ? p.speed_kmh.toFixed(1) + ' км/ч' : '0 км/ч'}</td>
        <td>${p.heading !== null && p.heading !== undefined ? p.heading + '°' : '—'}</td>
        <td>${p.accuracy ? '±' + p.accuracy.toFixed(0) + ' м' : '—'}</td>
        <td style="font-weight: 600; color: ${(p.anomaly_score || 0) >= 50 ? '#ef4444' : (p.anomaly_score || 0) >= 20 ? '#f59e0b' : '#10b981'}">
          ${p.anomaly_score || 0}
        </td>
        <td style="font-size: 0.75rem; color: #cbd5e1;">${flagSummary}</td>
      `;

      tr.addEventListener('click', () => {
        currentIndex = p.originalIdx;
        followLive = false;
        document.getElementById('checkFollowLive').checked = false;
        updateUI();
      });

      tbody.appendChild(tr);
    });
  }

  // --- 7. Timeline Playback & Scrubber Controls ---
  document.getElementById('timelineSlider').addEventListener('input', (e) => {
    currentIndex = parseInt(e.target.value, 10);
    followLive = false;
    document.getElementById('checkFollowLive').checked = false;
    updateUI();
  });

  document.getElementById('btnPlayPause').addEventListener('click', () => {
    isPlaying = !isPlaying;
    const btn = document.getElementById('btnPlayPause');
    if (isPlaying) {
      btn.textContent = '⏸ Пауза';
      const intervalMs = Math.max(250, 1000 / playbackSpeed);
      playInterval = setInterval(() => {
        if (currentIndex >= contactPoints.length - 1) {
          clearInterval(playInterval);
          isPlaying = false;
          btn.textContent = '▶ Воспроизвести';
          return;
        }
        currentIndex++;
        updateUI();
      }, intervalMs);
    } else {
      btn.textContent = '▶ Воспроизвести';
      clearInterval(playInterval);
    }
  });

  document.getElementById('playbackSpeedSelect').addEventListener('change', (e) => {
    playbackSpeed = parseFloat(e.target.value);
    if (isPlaying) {
      clearInterval(playInterval);
      const intervalMs = Math.max(250, 1000 / playbackSpeed);
      playInterval = setInterval(() => {
        if (currentIndex >= contactPoints.length - 1) {
          clearInterval(playInterval);
          isPlaying = false;
          document.getElementById('btnPlayPause').textContent = '▶ Воспроизвести';
          return;
        }
        currentIndex++;
        updateUI();
      }, intervalMs);
    }
  });

  document.getElementById('checkFollowLive').addEventListener('change', (e) => {
    followLive = e.target.checked;
    if (followLive && contactPoints.length > 0) {
      currentIndex = contactPoints.length - 1;
      updateUI();
    }
  });

  document.getElementById('checkOnlyCurrentPoint').addEventListener('change', (e) => {
    filterOnlyCurrentPoint = e.target.checked;
    renderAnomaliesList();
  });

  document.getElementById('checkFilterTableAnomalies').addEventListener('change', (e) => {
    filterTableAnomalies = e.target.checked;
    renderHistoryTable();
  });

  // --- 8. GPX and CSV Exporters ---
  document.getElementById('btnExportGPX').addEventListener('click', () => {
    if (contactPoints.length === 0) return alert('История пуста');
    let gpx = `<?xml version="1.0" encoding="UTF-8"?>\n<gpx version="1.1" creator="Telegram Live Geo Inspector">\n  <trk>\n    <name>${activeContactId || 'Track'}</name>\n    <trkseg>\n`;
    contactPoints.forEach(p => {
      const isoTime = new Date(p.edit_date * 1000).toISOString();
      gpx += `      <trkpt lat="${p.lat}" lon="${p.lon}"><time>${isoTime}</time></trkpt>\n`;
    });
    gpx += `    </trkseg>\n  </trk>\n</gpx>`;
    downloadBlob(gpx, `telegram_track_${activeContactId}_${Date.now()}.gpx`, 'application/gpx+xml');
  });

  document.getElementById('btnExportCSV').addEventListener('click', () => {
    if (contactPoints.length === 0) return alert('История пуста');
    let csv = `edit_number,edit_date,iso_time,point_type,lat,lon,accuracy,heading,speed_kmh,bearing_deg,anomaly_score,flags\n`;
    contactPoints.forEach(p => {
      const isoTime = new Date(p.edit_date * 1000).toISOString();
      const flagsStr = (p.anomaly_flags || []).map(f => f.rule_id).join(';');
      csv += `${p.edit_number},${p.edit_date},${isoTime},${p.point_type},${p.lat},${p.lon},${p.accuracy || ''},${p.heading || ''},${p.speed_kmh || ''},${p.bearing_deg || ''},${p.anomaly_score || 0},"${flagsStr}"\n`;
    });
    downloadBlob(csv, `telegram_telemetry_${activeContactId}_${Date.now()}.csv`, 'text/csv');
  });

  function downloadBlob(content, filename, type) {
    const blob = new Blob([content], { type: `${type};charset=utf-8;` });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }

  document.getElementById('btnDeleteHistory').addEventListener('click', async () => {
    if (!activeContactId) return;
    if (confirm(`Удалить историю координат для выбранного контакта?`)) {
      await apiFetch(`/api/history?contact_id=${encodeURIComponent(activeContactId)}`, {
        method: 'DELETE'
      });
      contactPoints = [];
      currentIndex = 0;
      updateUI();
    }
  });

  // --- 9. WebSocket Real-time Feed with Token Auth & Exponential Backoff ---
  let wsReconnectDelay = 1000;
  let wsReconnectTimer = null;

  function connectWebSocket() {
    if (wsReconnectTimer) {
      clearTimeout(wsReconnectTimer);
      wsReconnectTimer = null;
    }
    const wsProto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    // Strictly no token in query parameters - prevents token leakage in access logs
    const wsUrl = `${wsProto}//${window.location.host}/ws`;

    try {
      const ws = new WebSocket(wsUrl);

      ws.onopen = () => {
        // Send auth payload as first frame
        ws.send(JSON.stringify({ auth: authToken }));
        wsReconnectDelay = 1000; // Reset backoff on successful connect
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type === 'geo_point' && data.point) {
            const incoming = data.point;
            if (!activeContactId || incoming.contact_id === activeContactId) {
              contactPoints.push(incoming);
              if (followLive) {
                currentIndex = contactPoints.length - 1;
              }
              updateUI();
            }
          }
        } catch (e) {
          // ignore
        }
      };

      ws.onclose = (event) => {
        // Exponential backoff reconnect with jitter up to 30 seconds
        const jitter = Math.random() * 500;
        const nextDelay = Math.min(30000, wsReconnectDelay * 1.5) + jitter;
        wsReconnectDelay = nextDelay;
        wsReconnectTimer = setTimeout(connectWebSocket, nextDelay);
      };

      ws.onerror = () => {
        try { ws.close(); } catch (e) {}
      };
    } catch (e) {
      const nextDelay = Math.min(30000, wsReconnectDelay * 1.5);
      wsReconnectDelay = nextDelay;
      wsReconnectTimer = setTimeout(connectWebSocket, nextDelay);
    }
  }

  // --- 10. Modals Management ---
  function setupModal(modalId, btnOpenId) {
    const modal = document.getElementById(modalId);
    const btnOpen = document.getElementById(btnOpenId);
    if (!modal) return;

    if (btnOpen) {
      btnOpen.addEventListener('click', () => modal.classList.add('active'));
    }

    modal.querySelectorAll('.modal-close').forEach(btn => {
      btn.addEventListener('click', () => modal.classList.remove('active'));
    });

    modal.addEventListener('click', (e) => {
      if (e.target === modal) modal.classList.remove('active');
    });
  }

  setupModal('modalWhatCannotSee', 'btnWhatCannotSee');
  setupModal('modalSettings', 'btnSettings');
  setupModal('modalScenarios', 'btnScenarios');
  setupModal('modalAuth', 'btnAuthTelegram');

  // Audio Unlock Button
  document.getElementById('btnUnlockSound').addEventListener('click', initAudio);

  // Settings: Load and Save
  async function loadSettings() {
    try {
      const res = await apiFetch('/api/config');
      if (res.ok) {
        const cfg = await res.json();
        document.getElementById('cfgMaxSpeed').value = cfg.max_credible_speed_kmh;
        document.getElementById('cfgFlightSpeed').value = cfg.flight_speed_threshold_kmh;
        document.getElementById('cfgFlightPoints').value = cfg.flight_min_points;
        document.getElementById('cfgTeleportDist').value = cfg.teleport_min_distance_m;
        document.getElementById('cfgHeadingDiff').value = cfg.heading_diff_threshold_deg;
        document.getElementById('cfgSmoothnessPoints').value = cfg.smoothness_min_points;
        document.getElementById('cfgTimingWeight').value = cfg.timing_max_weight;
      }
    } catch (e) {
      // ignore
    }
  }

  document.getElementById('btnSaveSettings').addEventListener('click', async () => {
    const payload = {
      max_credible_speed_kmh: parseFloat(document.getElementById('cfgMaxSpeed').value),
      flight_speed_threshold_kmh: parseFloat(document.getElementById('cfgFlightSpeed').value),
      flight_min_points: parseInt(document.getElementById('cfgFlightPoints').value, 10),
      teleport_min_distance_m: parseFloat(document.getElementById('cfgTeleportDist').value),
      heading_diff_threshold_deg: parseFloat(document.getElementById('cfgHeadingDiff').value),
      smoothness_min_points: parseInt(document.getElementById('cfgSmoothnessPoints').value, 10),
      timing_max_weight: parseInt(document.getElementById('cfgTimingWeight').value, 10)
    };

    await apiFetch('/api/config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    document.getElementById('modalSettings').classList.remove('active');
    alert('Пороги успешно сохранены на бэкенде');
  });

  // Scenarios: List and Run
  async function loadScenarios() {
    try {
      const res = await apiFetch('/api/scenarios/list');
      if (res.ok) {
        const scenarios = await res.json();
        const container = document.getElementById('scenariosListContainer');
        container.innerHTML = '';

        scenarios.forEach(sc => {
          const item = document.createElement('div');
          item.style.background = '#1e293b';
          item.style.padding = '0.75rem';
          item.style.borderRadius = '6px';
          item.style.display = 'flex';
          item.style.justifyContent = 'space-between';
          item.style.alignItems = 'center';
          item.style.gap = '0.5rem';

          let statusBadge = '<span style="color: #10b981; font-size: 0.75rem;">Ожидается: Чисто</span>';
          if (sc.expected_status === 'flagged') {
            statusBadge = '<span style="color: #ef4444; font-size: 0.75rem;">Ожидается: Аномалия</span>';
          } else if (sc.expected_status === 'suspicious') {
            statusBadge = '<span style="color: #f59e0b; font-size: 0.75rem;">Ожидается: Подозрительно</span>';
          }

          item.innerHTML = `
            <div style="flex: 1;">
              <div style="font-weight: 600;">${sc.title}</div>
              <div style="color: var(--text-muted); font-size: 0.75rem; margin-top: 0.2rem;">${sc.description}</div>
              <div style="margin-top: 0.25rem;">${statusBadge}</div>
            </div>
            <button class="btn-primary btn-run-sc" data-id="${sc.id}">Запустить</button>
          `;
          container.appendChild(item);
        });

        container.querySelectorAll('.btn-run-sc').forEach(b => {
          b.addEventListener('click', async (e) => {
            const scId = e.currentTarget.getAttribute('data-id');
            await runScenario(scId);
            document.getElementById('modalScenarios').classList.remove('active');
          });
        });
      }
    } catch (e) {
      // ignore
    }
  }

  async function runScenario(scenarioId) {
    try {
      const res = await apiFetch(`/api/scenarios/run?id=${encodeURIComponent(scenarioId)}`, {
        method: 'POST'
      });
      if (res.ok) {
        const data = await res.json();
        activeContactId = data.scenario.id;
        contactPoints = data.points;
        contactGaps = data.gaps || [];
        currentIndex = contactPoints.length - 1;
        followLive = true;

        // Add to contacts dropdown if absent
        const select = document.getElementById('contactSelect');
        let exists = false;
        for (let i = 0; i < select.options.length; i++) {
          if (select.options[i].value === activeContactId) exists = true;
        }
        if (!exists) {
          const opt = document.createElement('option');
          opt.value = activeContactId;
          opt.textContent = data.scenario.title;
          select.appendChild(opt);
        }
        select.value = activeContactId;

        // Auto zoom map to points bounds
        if (contactPoints.length > 0) {
          const lats = contactPoints.map(p => p.lat);
          const lons = contactPoints.map(p => p.lon);
          map.fitBounds([
            [Math.min(...lats), Math.min(...lons)],
            [Math.max(...lats), Math.max(...lons)]
          ], { padding: [40, 40] });
        }

        updateUI();
      }
    } catch (e) {
      alert('Ошибка при запуске сценария');
    }
  }

  // Telegram MTProto Auth handlers
  document.getElementById('btnSubmitAuthStart').addEventListener('click', async () => {
    const apiId = parseInt(document.getElementById('authApiId').value, 10);
    const apiHash = document.getElementById('authApiHash').value.trim();
    const phone = document.getElementById('authPhone').value.trim();
    const errDiv = document.getElementById('authError1');

    errDiv.style.display = 'none';
    if (!apiId || !apiHash || !phone) {
      errDiv.textContent = 'Заполните все три поля.';
      errDiv.style.display = 'block';
      return;
    }

    try {
      const res = await apiFetch('/api/auth/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ api_id: apiId, api_hash: apiHash, phone: phone })
      });
      const data = await res.json();
      if (!res.ok || data.status === 'error') {
        errDiv.textContent = data.error || data.detail || 'Ошибка отправки кода';
        errDiv.style.display = 'block';
      } else {
        document.getElementById('authStep1').style.display = 'none';
        document.getElementById('authStep2').style.display = 'block';
      }
    } catch (e) {
      errDiv.textContent = 'Ошибка сети';
      errDiv.style.display = 'block';
    }
  });

  document.getElementById('btnSubmitAuthCode').addEventListener('click', async () => {
    const code = document.getElementById('authCode').value.trim();
    const errDiv = document.getElementById('authError2');
    errDiv.style.display = 'none';

    try {
      const res = await apiFetch('/api/auth/code', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: code })
      });
      const data = await res.json();
      if (data.status === '2fa_required') {
        document.getElementById('authStep2').style.display = 'none';
        document.getElementById('authStep3').style.display = 'block';
      } else if (data.status === 'authenticated') {
        document.getElementById('authStep2').style.display = 'none';
        document.getElementById('authStepSuccess').style.display = 'block';
      } else {
        errDiv.textContent = data.error || 'Неверный код';
        errDiv.style.display = 'block';
      }
    } catch (e) {
      errDiv.textContent = 'Ошибка соединения';
      errDiv.style.display = 'block';
    }
  });

  document.getElementById('btnSubmitAuth2FA').addEventListener('click', async () => {
    const password = document.getElementById('auth2FAPassword').value;
    const errDiv = document.getElementById('authError3');
    errDiv.style.display = 'none';

    try {
      const res = await apiFetch('/api/auth/2fa', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password: password })
      });
      const data = await res.json();
      if (data.status === 'authenticated') {
        document.getElementById('authStep3').style.display = 'none';
        document.getElementById('authStepSuccess').style.display = 'block';
      } else {
        errDiv.textContent = data.error || 'Неверный пароль 2FA';
        errDiv.style.display = 'block';
      }
    } catch (e) {
      errDiv.textContent = 'Ошибка соединения';
      errDiv.style.display = 'block';
    }
  });

  document.getElementById('btnLogout').addEventListener('click', async () => {
    await apiFetch('/api/auth/logout', { method: 'POST' });
    document.getElementById('authStepSuccess').style.display = 'none';
    document.getElementById('authStep1').style.display = 'block';
  });

  document.getElementById('btnScanBroadcasts').addEventListener('click', async () => {
    const res = await apiFetch('/api/broadcasts/scan?refresh=true');
    if (res.ok) {
      const data = await res.json();
      const streams = data.streams || [];
      const select = document.getElementById('contactSelect');
      select.innerHTML = '';
      if (streams.length === 0) {
        select.innerHTML = '<option value="">Активные трансляции не найдены</option>';
      } else {
        streams.forEach(s => {
          const opt = document.createElement('option');
          opt.value = s.contact_id;
          opt.textContent = `${s.contact_name} (${s.point_type === 'live' ? 'Live' : 'Static'})`;
          select.appendChild(opt);
        });
      }
    }
  });

  // --- 11. Initial Startup Flow ---
  async function startup() {
    loadSettings();
    loadScenarios();
    connectWebSocket();

    // Default: run normal_walk scenario so UI opens immediately interactive
    await runScenario('normal_walk');
  }

  startup();
})();
