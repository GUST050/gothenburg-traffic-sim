const Render = (() => {
  let _map           = null;
  let _provider      = null;
  let _normalProfile = null;
  const _edges       = {};

  const MAX_CARS      = 15;    // dot pool per sensor edge
  const CAR_SPEED_M_S = 13.9;  // 50 km/h in m/s — used for density estimate

  // ── Helpers ───────────────────────────────────────────────────────────────────

  function haversineM(a, b) {
    const R = 6371000, toR = Math.PI / 180;
    const φ1 = a[0]*toR, φ2 = b[0]*toR;
    const Δφ = (b[0]-a[0])*toR, Δλ = (b[1]-a[1])*toR;
    const x  = Math.sin(Δφ/2)**2 + Math.cos(φ1)*Math.cos(φ2)*Math.sin(Δλ/2)**2;
    return R * 2 * Math.atan2(Math.sqrt(x), Math.sqrt(1-x));
  }

  function edgeLengthM(latlngs) {
    let d = 0;
    for (let i = 0; i < latlngs.length - 1; i++) d += haversineM(latlngs[i], latlngs[i+1]);
    return Math.max(d, 10);
  }

  // Anchors: #1f9d55 → #d97706 → #dc2626 (validated against light basemap)
  function rampColor(t) {
    let r, g, b;
    if (t < 0.5) {
      const s = t * 2;
      r = Math.round(31  + (217 - 31)  * s);
      g = Math.round(157 + (119 - 157) * s);
      b = Math.round(85  + (6   - 85)  * s);
    } else {
      const s = (t - 0.5) * 2;
      r = Math.round(217 + (220 - 217) * s);
      g = Math.round(119 + (38  - 119) * s);
      b = Math.round(6   + (38  - 6)   * s);
    }
    return `rgb(${r},${g},${b})`;
  }

  function interpolate(latlngs, t) {
    const n   = latlngs.length - 1;
    const pos = t * n;
    const i   = Math.min(Math.floor(pos), n - 1);
    const f   = pos - i;
    const a   = latlngs[i], b = latlngs[i + 1];
    return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f];
  }

  // ── Per-tick update (called when QI changes) ─────────────────────────────────

  // Sets line style, colour level (e.t) and active car count for a flow value.
  // count may be an interpolated float — called both per tick (raw value)
  // and per frame (blended value) via animLoop.
  function applyFlowStyle(e, edgeId, count, qi) {
    e.count = count;

    if (count === null) {
      e.line.setStyle({ color: '#64748b', weight: 2, opacity: 0.65, dashArray: '5 8' });
      e.t = null; e.activeCars = 0;
      return;
    }
    if (count === 0) {
      e.line.setStyle({ color: '#94a3b8', weight: 2.5, opacity: 0.6, dashArray: '' });
      e.t = 0; e.activeCars = 0;
      return;
    }

    // Colour: compare against the calm September average (rush hours excluded).
    // During rush hours the calm profile has no data → fall back to normal.
    // Day-of-week is derived from the ACTIVE provider's epoch (2025 starts on
    // a Wednesday, 2027 on a Friday) — otherwise forecast mode reads the
    // wrong weekday profile.
    const dow    = (_provider.dateFromQI(qi).getUTCDay() + 6) % 7;  // 0 = Mon
    const calm   = _normalProfile ? _normalProfile.calmAt(edgeId, qi, dow)  : null;
    const normal = _normalProfile ? _normalProfile.flowAt(edgeId, qi, dow)  : null;
    let t;
    if (calm !== null && calm > 0) {
      t = Math.min(count / (2 * calm), 1);
    } else if (normal !== null && normal > 0) {
      t = Math.min(count / (2 * normal), 1);
    } else {
      t = Math.min(count / (_provider.maxFlow(edgeId) || 1), 1);
    }
    e.t = t;
    e.line.setStyle({ color: rampColor(t), weight: 4, opacity: 0.85, dashArray: '' });

    // Cars visible on the edge = flow rate × traversal time
    // Physical formula: N = (count/900 cars/sec) × (lengthM/13.9 sec)
    const traverseSec = e.lengthM / CAR_SPEED_M_S;
    e.activeCars = Math.min(MAX_CARS, Math.max(1, Math.round(count * traverseSec / 900)));
  }

  function updateEdge(edgeId, qi) {
    const e = _edges[edgeId];
    if (!e || !e.isSensor) return;
    applyFlowStyle(e, edgeId, _provider.flowAt(edgeId, qi), qi);
  }

  function redraw(qi) {
    for (const id of Object.keys(_edges)) updateEdge(id, qi);
  }

  // ── Animation loop ────────────────────────────────────────────────────────────
  // Conveyor-belt: N cars evenly spaced along the edge, moving in phase.
  // N comes from the real density estimate. Speed rises with traffic level.
  // No cars are created/destroyed per frame — only setLatLng + setStyle.

  let _lastTs = null;

  function animLoop(ts) {
    const dt = _lastTs !== null ? Math.min((ts - _lastTs) / 1000, 0.05) : 0;
    _lastTs = ts;

    // Smooth interpolation: the data is 15-min steps, but colour and dot
    // count blend linearly between quarter qi0 and qi0+1 so nothing jumps
    // visually. Quantisation (quarter vehicles) means setStyle only runs
    // when the value actually changes.
    const qiF  = typeof State !== 'undefined' ? State.qiFloat : 0;
    const qi0  = Math.floor(qiF);
    const frac = qiF - qi0;

    for (const [id, e] of Object.entries(_edges)) {
      if (!e.isSensor) continue;

      const c0 = _provider.flowAt(id, qi0);
      const c1 = _provider.flowAt(id, qi0 + 1);
      const blended = (c0 !== null && c1 !== null) ? c0 + (c1 - c0) * frac : c0;
      const styleKey = blended === null ? 'null' : Math.round(blended * 4);
      if (e._styleKey !== styleKey) {
        e._styleKey = styleKey;
        applyFlowStyle(e, id, blended, qi0);
      }

      const n = e.activeCars ?? 0;

      // No traffic or missing data — hide all dots
      if (n === 0 || e.t === null) {
        for (const d of e.dots) d.setStyle({ opacity: 0, fillOpacity: 0 });
        continue;
      }

      const col   = rampColor(e.t);
      // Low traffic → slow drift (0.06), high traffic → fast (0.20)
      const speed = 0.06 + e.t * 0.14;

      e.phase = ((e.phase ?? 0) + dt * speed) % 1;

      for (let i = 0; i < MAX_CARS; i++) {
        const d = e.dots[i];
        if (i < n) {
          // Evenly spaced + shared phase → seamless loop
          const prog = ((i / n) + e.phase) % 1;
          d.setLatLng(interpolate(e.latlngs, prog));
          d.setStyle({ fillColor: col, opacity: 1, fillOpacity: 0.95 });
        } else {
          d.setStyle({ opacity: 0, fillOpacity: 0 });
        }
      }
    }
    requestAnimationFrame(animLoop);
  }

  // ── Public API ────────────────────────────────────────────────────────────────

  return {
    async init(mapEl, provider, normalProfile = null) {
      _provider      = provider;
      _normalProfile = normalProfile;

      // Light basemap. The traffic ramp (dark green→amber→red) is validated
      // against a light surface: contrast ≥3:1, CVD separation 16.9 — do not
      // change the colours without re-running the validation.
      _map = L.map(mapEl, { zoomControl: true }).setView([57.697, 11.983], 15);
      L.tileLayer(
        'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
        { attribution: '© OSM © CARTO', subdomains: 'abcd', maxZoom: 19 }
      ).addTo(_map);

      const res = await fetch('data/network.geojson?v=' + Date.now());
      if (!res.ok) throw new Error('Could not load data/network.geojson');
      const geojson = await res.json();

      const bg = L.layerGroup().addTo(_map);
      const fg = L.layerGroup().addTo(_map);

      for (const feat of geojson.features) {
        if (feat.geometry.type !== 'LineString') continue;
        const { id, sensor_id, name, level, confidence } = feat.properties;
        const latlngs  = feat.geometry.coordinates.map(([lon, lat]) => [lat, lon]);
        const isSensor = !!sensor_id;
        const lengthM  = isSensor ? edgeLengthM(latlngs) : 0;

        // Background network is drawn in dark slate so it reads on the light
        // basemap, and faded by confidence — the network dims with distance
        // from the sensors.
        const bgOpacity = 0.25 + 0.55 * (confidence ?? 0.5);
        const line = L.polyline(latlngs, isSensor
          ? { color: '#64748b', weight: 3, opacity: 0.4 }
          : { color: '#526078', weight: 2, opacity: bgOpacity }
        ).addTo(isSensor ? fg : bg);

        // Confidence = proximity to the nearest sensor (0–1, computed offline).
        // Shown as % so users see how trustworthy a simulation is on this edge.
        const confHtml = (c) => {
          if (c === null || c === undefined) return '';
          const pct = Math.round(c * 100);
          const col = pct >= 70 ? '#16a34a' : pct >= 30 ? '#d97706' : '#dc2626';
          return `<br><span style="color:${col}">Simuleringskonfidens: ${pct} %</span>`;
        };

        if (!isSensor) {
          line.bindTooltip(
            () => `<b>${name ?? 'Okänd väg'}</b>${confHtml(confidence)}`,
            { sticky: true }
          );
        }

        const dots = [];

        if (isSensor) {
          // Create pool — MAX_CARS dots, all hidden initially
          for (let i = 0; i < MAX_CARS; i++) {
            dots.push(L.circleMarker(latlngs[0], {
              radius: 5, color: '#ffffff', fillColor: '#94a3b8',
              weight: 1.5, fillOpacity: 0, opacity: 0, interactive: false,
            }).addTo(fg));
          }

          line.bindTooltip(() => {
            const qi   = typeof State !== 'undefined' ? State.qi : 0;
            const cnt  = _provider.flowAt(id, qi);
            const dow  = (_provider.dateFromQI(qi).getUTCDay() + 6) % 7;
            const calm = _normalProfile ? _normalProfile.calmAt(id, qi, dow)  : null;
            const norm = _normalProfile ? _normalProfile.flowAt(id, qi, dow)  : null;
            let html   = `<b>${name ?? 'Okänd väg'}</b> · Sensor ${sensor_id}`;
            if (cnt !== null) {
              html += `<br><b style="font-size:1.15em">${cnt}</b> fordon / 15 min`;
              if (calm !== null && calm > 0) {
                const pct = Math.round((cnt / calm) * 100);
                const col = pct > 110 ? '#dc2626' : pct < 90 ? '#16a34a' : '#6b7280';
                html += `<br><span style="color:${col}">${pct}% av lugnt normalläge (ej rusning)</span>`;
              } else if (norm !== null && norm > 0) {
                const pct = Math.round((cnt / norm) * 100);
                const col = pct > 110 ? '#dc2626' : pct < 90 ? '#16a34a' : '#6b7280';
                html += `<br><span style="color:${col}">${pct}% av september-snitt</span>`;
              }
            } else {
              html += `<br><span style="color:#9ca3af">Data saknas</span>`;
            }
            if (level === 'Total') html += '<br><small>Summa båda riktningar</small>';
            if (level === 'S')     html += '<br><small>Singeldetektor</small>';
            html += confHtml(confidence);
            return html;
          }, { sticky: true });
        }

        _edges[id] = {
          line, isSensor, latlngs, lengthM,
          t: 0, count: 0, activeCars: 0,
          dots, phase: Math.random(), // random starting phase per edge
        };
      }

      // Frame the whole network (two cluster areas) instead of a hardcoded view
      const allLatLngs = Object.values(_edges).flatMap(e => e.latlngs);
      if (allLatLngs.length) {
        _map.fitBounds(L.latLngBounds(allLatLngs), { padding: [48, 48], maxZoom: 16 });
      }

      requestAnimationFrame(animLoop);
      redraw(0);
      window.addEventListener('tick', ({ detail: { qi } }) => redraw(qi));
    },

    setProvider(p) {
      _provider = p;
      redraw(typeof State !== 'undefined' ? State.qi : 0);
    },

    setNormalProfile(np) {
      _normalProfile = np;
      redraw(typeof State !== 'undefined' ? State.qi : 0);
    },
  };
})();
