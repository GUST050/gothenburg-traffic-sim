const Render = (() => {
  let _map           = null;
  let _provider      = null;
  let _normalProfile = null;
  // flowAt() is a deliberately small seam, but calling it twice for every
  // edge on every animation frame repeats the same array lookup while the
  // current quarter is unchanged.  Cache only the two source buckets for the
  // current provider/quarter; interpolation still happens every frame, so
  // smooth playback fidelity is unchanged.
  let _flowCacheProvider = null;
  let _flowCacheQI = null;
  const _flowCache = Object.create(null);
  let _onEdgeClick   = null;   // closure-mode callback (set by index.html)
  const _edges       = {};
  const _pending     = new Set();   // edges selected for closure, not yet simulated

  const PENDING_STYLE = { color: '#b91c1c', weight: 5, opacity: 0.9, dashArray: '2 6' };

  // ── Vehicle playback state (every dot = one simulated car with real OD) ──
  let _traj        = null;   // {edges:[ids], vehicles:[{d,e,x,p?,a?}]} sorted by depart
  let _vehicleMode = false;
  let _vehCanvas   = null, _vehCtx = null;
  let _vehCursor   = 0;      // sliding window into the depart-sorted list
  let _vehActive   = [];     // [{v, j}] currently driving (j = edge index hint)
  let _vehLastT    = -1;
  const _geomCache = {};     // edgeId → {pts, cum, total} for along-line interp

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

  // Diverging ramp for the COMPARISON view: green = less traffic than the
  // baseline, red = more. Deliberately a separate function from rampColor(),
  // which means "how busy is this street" — the two must never be confused,
  // because green means "quiet" in one and "improved" in the other.
  //
  // Endpoints reuse the validated palette (#1f9d55 green, #dc2626 red) so the
  // colours stay legible on the light basemap. The midpoint is a neutral
  // slate rather than amber: amber reads as "moderately busy" from the other
  // ramp, while no change should read as no signal at all.
  //   r = -1 → all traffic gone, 0 → unchanged, +1 → doubled or more
  function deltaColor(r) {
    const MID = [100, 116, 139];   // slate-500, the "no change" anchor
    const DOWN = [31, 157, 85];    // #1f9d55
    const UP   = [220, 38, 38];    // #dc2626
    const s = Math.min(Math.abs(r), 1);
    const to = r < 0 ? DOWN : UP;
    const c = MID.map((m, i) => Math.round(m + (to[i] - m) * s));
    return `rgb(${c[0]},${c[1]},${c[2]})`;
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

    // Selected for closure (not yet simulated) — must win over per-frame
    // flow styling or the selection highlight is overwritten instantly
    if (_pending.has(edgeId)) {
      e.line.setStyle(PENDING_STYLE);
      e.t = null; e.activeCars = 0;
      return;
    }

    // Closed edge in a scenario — draw as blocked, no cars
    if (_provider.isEdgeClosed && _provider.isEdgeClosed(edgeId, qi)) {
      e.line.setStyle({ color: '#0f172a', weight: 5, opacity: 0.85, dashArray: '4 7' });
      e.t = null; e.activeCars = 0;
      return;
    }

    if (count === null) {
      if (!e.isSensor) {   // background edge without data — keep base look
        e.line.setStyle(e.baseStyle);
        e.t = null; e.activeCars = 0;
        return;
      }
      e.line.setStyle({ color: '#64748b', weight: 2, opacity: 0.65, dashArray: '5 8' });
      e.t = null; e.activeCars = 0;
      return;
    }
    if (count === 0) {
      e.line.setStyle({ color: '#94a3b8', weight: 2.5, opacity: 0.6, dashArray: '' });
      e.t = 0; e.activeCars = 0;
      return;
    }

    // Simulering: road colour shows CERTAINTY (proximity to a sensor), not
    // traffic level — the animated vehicles ARE the traffic display there
    // (Gustav 2026-07-17: one colour for used roads, shifting with
    // uncertainty the further from the sensors). Green = measured/near a
    // sensor, red = extrapolation far away — the SAME semantics the
    // confidence tooltip has always used, so the two can never disagree.
    // Because confidence = f(distance to nearest sensor), this renders as
    // visible certainty zones around the sensor clusters. Per-scenario
    // confidence (Monte Carlo spread) beats the static prior when present.
    // Comparison view: colour by CHANGE against the baseline, not by volume.
    // Placed before the scenario branch because a DeltaProvider is a scenario
    // comparison, not a confidence display.
    if (_provider.isDelta) {
      const cmp = _provider.compare(edgeId, qi);
      e.t = null; e.activeCars = 0;
      e.cmp = cmp;
      if (cmp.status === 'closed') {
        e.line.setStyle({
          color: '#111827', weight: 5, opacity: 0.95, dashArray: '6 5',
        });
        return;
      }
      if (cmp.status === 'no_data') {
        // A gap is drawn as a gap — never as "no change".
        e.line.setStyle({
          color: '#cbd5e1', weight: 2, opacity: 0.25, dashArray: '2 4',
        });
        return;
      }
      // Unclaimable edges are shown but visibly weaker: the redistribution is
      // real, the confidence to assert its size is not.
      const claimable = cmp.status !== 'unclaimable';
      e.line.setStyle({
        color: deltaColor(cmp.rel ?? 0),
        weight: e.isSensor ? 4 : 3,
        opacity: cmp.status === 'within_noise' ? 0.35
               : (claimable ? 0.9 : 0.5),
        dashArray: claimable ? '' : '4 3',
      });
      return;
    }

    if (_provider.isScenario) {
      const conf = (_provider.confidence && _provider.confidence[edgeId] != null)
        ? _provider.confidence[edgeId] : (e.conf ?? 0);
      // Display transform: the empirical sigma (127.5 m) makes raw
      // confidence collapse within ~300 m, which paints the whole city one
      // flat red and hides the very zone structure the colouring should
      // show. conf^(1/8) is monotone in the SAME confidence value (order
      // and endpoints preserved — equivalent to drawing the gradient with
      // a ~2.8x wider visual sigma) so the transition stays visible for a
      // few blocks. The tooltip keeps the exact untransformed percentage.
      const confV = Math.pow(Math.max(conf, 0), 0.125);
      e.t = null; e.activeCars = 0;
      e.line.setStyle({
        color: rampColor(1 - confV),
        weight: e.isSensor ? 4 : 3,
        opacity: 0.30 + 0.55 * confV,
        dashArray: '',
      });
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
    // Simulated background streets have no measured September profile; use
    // the provider's own calm midday mean so the colour keeps the same
    // meaning ("relative to calm daytime traffic") on every edge.  The old
    // fallback — this edge's own maximum — made every street reach full
    // red at its own peak by construction, so at rush hour the whole city
    // lit up red regardless of how much traffic actually flowed.
    const calmSim = _provider.calmFlow ? _provider.calmFlow(edgeId) : null;
    let t;
    if (calm !== null && calm > 0) {
      t = Math.min(count / (2 * calm), 1);
    } else if (normal !== null && normal > 0) {
      t = Math.min(count / (2 * normal), 1);
    } else if (calmSim !== null && calmSim > 0) {
      // Absolute floor: below ~20 veh/15 min a street is genuinely light —
      // ratio noise on a near-empty street must not paint it alarm red.
      t = Math.min(count / (2 * calmSim), count / 20, 1);
    } else {
      t = Math.min(count / (_provider.maxFlow(edgeId) || 1), count / 20, 1);
    }
    e.t = t;
    e.line.setStyle({
      color: rampColor(t),
      weight: e.isSensor ? 4 : 3,
      opacity: 0.85, dashArray: '',
    });

    // Colour-only edges (scenario background streets) have no dot pool
    if (!e.dots.length) { e.activeCars = 0; return; }

    // Cars visible on the edge = flow rate × traversal time
    // Physical formula: N = (count/900 cars/sec) × (lengthM/13.9 sec)
    const traverseSec = e.lengthM / CAR_SPEED_M_S;
    e.activeCars = Math.min(MAX_CARS, Math.max(1, Math.round(count * traverseSec / 900)));
  }

  function updateEdge(edgeId, qi) {
    const e = _edges[edgeId];
    if (!e) return;
    if (e.isSensor || _provider.hasEdge(edgeId)) {
      e.hadData = true;
      applyFlowStyle(e, edgeId, _provider.flowAt(edgeId, qi), qi);
    } else if (e.hadData) {
      // Provider switched away from a scenario — restore the base look once
      e.hadData = false;
      e._styleKey = undefined;
      e.line.setStyle(e.baseStyle);
      e.t = null; e.activeCars = 0;
    }
  }

  function redraw(qi) {
    for (const id of Object.keys(_edges)) updateEdge(id, qi);
  }

  // ── Vehicle playback helpers ──────────────────────────────────────────────────

  function edgeGeom(id) {
    let g = _geomCache[id];
    if (!g) {
      const ll = _edges[id]?.latlngs;
      if (!ll || ll.length < 2) return null;
      const cum = [0];
      for (let i = 1; i < ll.length; i++) {
        cum.push(cum[i - 1] + haversineM(ll[i - 1], ll[i]));
      }
      g = _geomCache[id] = { pts: ll, cum, total: Math.max(cum[cum.length - 1], 1) };
    }
    return g;
  }

  function pointAlong(g, frac) {
    const target = frac * g.total;
    let i = 1;
    while (i < g.cum.length - 1 && g.cum[i] < target) i++;
    const seg = g.cum[i] - g.cum[i - 1] || 1;
    const f = (target - g.cum[i - 1]) / seg;
    const a = g.pts[i - 1], b = g.pts[i];
    return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f];
  }

  function drawVehicles(tSec) {
    if (!_traj || !_vehCtx) return;
    const vs = _traj.vehicles;

    if (tSec < _vehLastT) {           // scrubbed backwards — rebuild window
      _vehCursor = 0;
      _vehActive = [];
    }
    _vehLastT = tSec;
    while (_vehCursor < vs.length && vs[_vehCursor].d <= tSec) {
      _vehActive.push({ v: vs[_vehCursor], j: 0 });
      _vehCursor++;
    }

    const size = _map.getSize();
    // Setting canvas.width/height ALWAYS clears the backing store and
    // resets context state, even when assigned the same value it already
    // has — a real per-frame cost (this runs at animation-frame rate
    // during vehicle playback) for something that only actually needs to
    // happen on a genuine map resize. clearRect below already erases the
    // previous frame's dots, so skipping the resize when unchanged loses
    // nothing. Found in a review 2026-07-10.
    if (_vehCanvas.width !== size.x || _vehCanvas.height !== size.y) {
      _vehCanvas.width = size.x; _vehCanvas.height = size.y;
    }
    const ctx = _vehCtx;
    ctx.clearRect(0, 0, size.x, size.y);
    ctx.fillStyle = '#1d4ed8';
    ctx.strokeStyle = 'rgba(255,255,255,0.9)';
    ctx.lineWidth = 1;

    const alive = [];
    for (const a of _vehActive) {
      const { v } = a;
      const arrival = v.x[v.x.length - 1];
      // F2: an UNFINISHED vehicle (v.u — still driving/queued when the
      // simulation ended) must not vanish at its last recorded exit time:
      // it is drawn PARKED at the end of its last known edge for the rest
      // of the scenario — the same honesty rule as closure truncation
      // (erasing it would erase its real contribution to the picture).
      if (arrival <= tSec && !v.u) continue;   // reached its destination
      alive.push(a);
      let ll;
      if (arrival <= tSec && v.u) {
        const g = edgeGeom(_traj.edges[v.e[v.e.length - 1]]);
        if (!g) continue;
        ll = pointAlong(g, 1);
      } else {
        while (a.j < v.x.length - 1 && v.x[a.j] <= tSec) a.j++;
        const enter = a.j === 0 ? v.d : v.x[a.j - 1];
        const exit  = v.x[a.j];
        const g = edgeGeom(_traj.edges[v.e[a.j]]);
        if (!g || exit <= enter) continue;
        // SUMO may start/end a route part-way along its first/last edge.
        // The compact trajectory sidecar carries those metre offsets as p/a;
        // without them the browser would draw every car at the upstream
        // junction even though SUMO itself started it at a building/POI access.
        const startM = a.j === 0 && Number.isFinite(v.p) ? v.p : 0;
        const endM = a.j === v.e.length - 1 && Number.isFinite(v.a) ? v.a : g.total;
        const startFrac = Math.min(1, Math.max(0, startM / g.total));
        const endFrac = Math.min(1, Math.max(startFrac, endM / g.total));
        const progress = Math.min(1, Math.max(0, (tSec - enter) / (exit - enter)));
        const frac = startFrac + progress * (endFrac - startFrac);
        ll = pointAlong(g, frac);
      }
      const p = _map.latLngToContainerPoint(ll);
      if (p.x < -10 || p.y < -10 || p.x > size.x + 10 || p.y > size.y + 10) continue;
      const parked = arrival <= tSec && v.u;
      if (parked) {
        // hollow amber dot: present but no longer moving
        ctx.beginPath();
        ctx.arc(p.x, p.y, 2.6, 0, 2 * Math.PI);
        ctx.save();
        ctx.fillStyle = '#fff7ed';
        ctx.strokeStyle = '#d97706';
        ctx.fill();
        ctx.stroke();
        ctx.restore();
      } else {
        ctx.beginPath();
        ctx.arc(p.x, p.y, 2.6, 0, 2 * Math.PI);
        ctx.fill();
        ctx.stroke();
      }
    }
    _vehActive = alive;
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
    if (_flowCacheProvider !== _provider || _flowCacheQI !== qi0) {
      _flowCacheProvider = _provider;
      _flowCacheQI = qi0;
      for (const key of Object.keys(_flowCache)) delete _flowCache[key];
    }

    for (const [id, e] of Object.entries(_edges)) {
      if (!e.isSensor && !_provider.hasEdge(id)) continue;

      let pair = _flowCache[id];
      if (!pair) {
        pair = _flowCache[id] = [_provider.flowAt(id, qi0),
                                 _provider.flowAt(id, qi0 + 1)];
      }
      const c0 = pair[0];
      const c1 = pair[1];
      const blended = (c0 !== null && c1 !== null) ? c0 + (c1 - c0) * frac : c0;
      const styleKey = blended === null ? 'null' : Math.round(blended * 4);
      if (e._styleKey !== styleKey) {
        e._styleKey = styleKey;
        e.hadData = true;
        applyFlowStyle(e, id, blended, qi0);
      }
      if (!e.dots.length) continue;   // colour-only edge — no dots to move

      const n = _vehicleMode ? 0 : (e.activeCars ?? 0);

      // No traffic or missing data — hide all dots
      // (vehicle mode replaces the conveyor illustration with REAL cars)
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
    if (_vehicleMode) {
      drawVehicles(qiF * 900);   // sim seconds since the scenario epoch
    }

    requestAnimationFrame(animLoop);
  }

  // ── Public API ────────────────────────────────────────────────────────────────

  return {
    async init(mapEl, provider, normalProfile = null, networkPayload = null) {
      _provider      = provider;
      _normalProfile = normalProfile;

      // Light basemap. The traffic ramp (dark green→amber→red) is validated
      // against a light surface: contrast ≥3:1, CVD separation 16.9 — do not
      // change the colours without re-running the validation.
      // preferCanvas: the inner-city network is ~7 000 edges — SVG DOM nodes
      // would crawl; canvas renders and hit-tests them smoothly.
      _map = L.map(mapEl, { zoomControl: true, preferCanvas: true })
        .setView([57.697, 11.983], 14);
      // Use OSM's documented browser tile endpoint directly.  The previous
      // CARTO URL now returns "API KEY REQUIRED" watermark tiles.  This app
      // requests only the currently visible viewport; the browser supplies a
      // normal Referer and honours the tile server's HTTP cache headers.
      L.tileLayer(
        'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
        {
          attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
          // Preserve the subdued grey hierarchy of the former CARTO light
          // basemap without depending on CARTO's now-keyed endpoint. This
          // class affects only raster tiles, not the semantic overlays.
          className: 'basemap-grayscale',
          maxZoom: 19,
          // Standard OSM repeats much of the app's own road network. Fade the
          // raster detail so the interactive road and traffic layers remain
          // the primary visual hierarchy, like on the former light basemap.
          opacity: 0.52,
        }
      ).addTo(_map);

      let geojson = networkPayload;
      if (!geojson) {
        const res = await fetch('data/network.geojson?v=' + Date.now());
        if (!res.ok) throw new Error('Could not load data/network.geojson');
        geojson = await res.json();
      }

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
        // City-scale canvas: the confidence gradient IS the base layer —
        // near-sensor streets read clearly, the far background stays airy.
        const bgOpacity  = 0.10 + 0.65 * (confidence ?? 0.3);
        const baseStyle  = isSensor
          ? { color: '#64748b', weight: 3,   opacity: 0.45,      dashArray: '' }
          : { color: '#526078', weight: 1.5, opacity: bgOpacity, dashArray: '' };
        const line = L.polyline(latlngs, baseStyle).addTo(isSensor ? fg : bg);
        line.on('click', () => { if (_onEdgeClick) _onEdgeClick(id); });

        // Confidence = proximity to the nearest sensor (0–1, computed offline).
        // Shown as % so users see how trustworthy a simulation is on this edge.
        const confHtml = (c) => {
          if (c === null || c === undefined) return '';
          const pct = Math.round(c * 100);
          const col = pct >= 70 ? '#16a34a' : pct >= 30 ? '#d97706' : '#dc2626';
          return `<br><span style="color:${col}">Simuleringskonfidens: ${pct} %</span>`;
        };

        // Per-scenario confidence (from the provider) beats the static prior
        const edgeConf = () =>
          (_provider.confidence && _provider.confidence[id] != null)
            ? _provider.confidence[id] : confidence;

        // Comparison tooltip: the numbers behind the colour. Always states
        // WHY an edge is neutral, so "grey" is never ambiguous between
        // "no change", "too little traffic to tell" and "no evidence".
        const deltaHtml = () => {
          if (!_provider.isDelta) return null;
          const qi = typeof State !== 'undefined' ? State.qi : 0;
          const cmp = _provider.compare(id, qi);
          if (cmp.status === 'closed') {
            return `<br><span style="color:#dc2626;font-weight:600">AVSTÄNGD i scenariot</span>`;
          }
          if (cmp.status === 'no_data') return `<br><small>Ingen data</small>`;
          const before = _provider.baselineAt(id, qi);
          const after  = _provider.flowAt(id, qi);
          const sign   = cmp.delta > 0 ? '+' : '';
          const col    = cmp.status === 'changed'
            ? (cmp.delta < 0 ? '#1f9d55' : '#dc2626') : '#64748b';
          let html = `<br>${before} → ${after} fordon / 15 min`
            + `<br><b style="color:${col};font-size:1.15em">${sign}${cmp.delta}</b>`;
          if (cmp.status === 'within_noise') {
            html += `<br><small>Inom seed-bruset — ingen påvisbar förändring</small>`;
          } else if (cmp.status === 'unclaimable') {
            html += `<br><small>För långt från sensor för att styrka storleken</small>`;
          } else if (cmp.cv !== null) {
            html += ` <small>(brusgräns ±${Math.round(2 * cmp.cv * 100)}%)</small>`;
          }
          const tot = _provider.totals(id);
          if (tot) {
            const ts = tot.delta > 0 ? '+' : '';
            html += `<br><small>Hela dygnet: ${ts}${Math.round(tot.delta)} fordon</small>`;
          }
          return html;
        };

        if (!isSensor) {
          line.bindTooltip(() => {
            const qi = typeof State !== 'undefined' ? State.qi : 0;
            let html = `<b>${name ?? 'Okänd väg'}</b>`;
            const cmpHtml = deltaHtml();
            if (cmpHtml !== null) {
              return html + cmpHtml + confHtml(edgeConf());
            }
            if (_provider.hasEdge(id)) {
              const cnt = _provider.flowAt(id, qi);
              if (_provider.isEdgeClosed && _provider.isEdgeClosed(id, qi)) {
                html += `<br><span style="color:#dc2626;font-weight:600">AVSTÄNGD i scenariot</span>`;
              } else if (cnt !== null) {
                html += `<br><b style="font-size:1.15em">${cnt}</b> fordon / 15 min <small>(simulerat)</small>`;
              }
              const window = _provider.closureWindowText?.(id);
              if (window) html += `<br><small>Avstängning: ${window}</small>`;
            }
            return html + confHtml(edgeConf());
          }, { sticky: true });
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
            if (level === 'Total')  html += '<br><small>Summa båda riktningar</small>';
            else if (level)         html += `<br><small>Enkelriktad mätning (${level})</small>`;
            if (_provider.isEdgeClosed && _provider.isEdgeClosed(id, qi)) {
              html += `<br><span style="color:#dc2626;font-weight:600">AVSTÄNGD i scenariot</span>`;
            } else if (_provider.isScenario) {
              html += '<br><small>Simulerat (SUMO)</small>';
            }
            const window = _provider.closureWindowText?.(id);
            if (window) html += `<br><small>Avstängning: ${window}</small>`;
            html += confHtml(edgeConf());
            return html;
          }, { sticky: true });
        }

        _edges[id] = {
          line, isSensor, latlngs, lengthM, baseStyle,
          conf: confidence ?? null,   // static prior for certainty colouring
          t: 0, count: 0, activeCars: 0, hadData: false,
          dots, phase: Math.random(), // random starting phase per edge
        };
      }

      // Vehicle-playback overlay canvas (above tiles/paths, below the panels)
      _vehCanvas = document.createElement('canvas');
      _vehCanvas.style.cssText =
        'position:absolute;inset:0;pointer-events:none;z-index:600';
      mapEl.appendChild(_vehCanvas);
      _vehCtx = _vehCanvas.getContext('2d');

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
      _flowCacheProvider = null;
      _flowCacheQI = null;
      for (const key of Object.keys(_flowCache)) delete _flowCache[key];
      // Invalidate the per-frame style cache so every edge restyles (or
      // resets to base look) under the new provider
      for (const e of Object.values(_edges)) e._styleKey = undefined;
      redraw(typeof State !== 'undefined' ? State.qi : 0);
    },

    // Call after the control panel's height changes (e.g. the Simulering
    // panel showing/hiding) — the map container is flex-sized, so Leaflet
    // needs to be told its box changed or tiles/clicks misalign until the
    // next manual pan/zoom.
    invalidateSize() {
      if (_map) _map.invalidateSize();
    },

    setNormalProfile(np) {
      _normalProfile = np;
      redraw(typeof State !== 'undefined' ? State.qi : 0);
    },

    onEdgeClick(fn) {
      _onEdgeClick = fn;
    },

    setTrajectories(data) {
      _traj = data;                 // null clears
      _vehCursor = 0; _vehActive = []; _vehLastT = -1;
    },

    setVehicleMode(on) {
      _vehicleMode = on;
      _vehCursor = 0; _vehActive = []; _vehLastT = -1;
      if (_vehCtx && !on) {
        _vehCtx.clearRect(0, 0, _vehCanvas.width, _vehCanvas.height);
      }
    },

    // Highlight edges selected for closure; pass [] to clear
    setPending(ids) {
      _pending.clear();
      for (const id of ids) _pending.add(id);
      for (const [id, e] of Object.entries(_edges)) {
        e._styleKey = undefined;   // force per-frame restyle
        if (_pending.has(id)) {
          e.line.setStyle(PENDING_STYLE);
        } else if (!e.isSensor && !_provider.hasEdge(id)) {
          e.line.setStyle(e.baseStyle);   // background edge: restore base look
        }
      }
      redraw(typeof State !== 'undefined' ? State.qi : 0);
    },
  };
})();
