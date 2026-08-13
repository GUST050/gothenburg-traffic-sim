// Main application wiring — moved out of index.html 2026-07-14
// (IMPROVEMENT_PLAN.md J: a real Content-Security-Policy needs script-src 'self',
// which an inline <script> block violates). Same code, same load order
// (after state/provider/render/controls/clock).
    (async () => {
      try {
        // These assets are independent. Start all requests together so the
        // largest network JSON is not delayed behind flow/profile parsing.
        const networkPromise = fetch('data/network.geojson?v=' + Date.now())
          .then(res => {
            if (!res.ok) throw new Error('Could not load data/network.geojson');
            return res.json();
          });
        const histPromise = new HistoricalProvider().load('data/flows.json');
        const normalPromise = new NormalProfile().load('data/normal_profile.json')
          .catch(e => {
            console.warn('normal_profile.json not found — run build_features.py to enable ratio colouring');
            return null;
          });
        const [histProvider, normalProfile, networkPayload] = await Promise.all([
          histPromise, normalPromise, networkPromise,
        ]);

        await Render.init(document.getElementById('map'), histProvider,
                          normalProfile, networkPayload);
        Controls.init(histProvider);
        Clock.start();
        State.setQI(0);

        // ── Mode toggle ───────────────────────────────────────────────────────
        let foreProvider = null;
        let scenIndex    = null;
        const scenProviders = {};
        const btnHist    = document.getElementById('btn-hist');
        const btnFore    = document.getElementById('btn-fore');
        const btnScen    = document.getElementById('btn-scen');
        const modeToggle = document.getElementById('mode-toggle');
        const scenSelect = document.getElementById('scen-select');
        const simPanel   = document.getElementById('sim-panel');
        const dayGroup   = document.getElementById('day-group');
        const dayHint    = document.getElementById('day-hint');
        const daySlider  = document.getElementById('day-slider');
        const YEAR_DAY_MAX = 364;   // day-slider's original range, for Historisk/Prognos
        const taskHome = document.getElementById('task-home');
        const taskTitle = document.getElementById('workspace-task-title');
        const taskNames = {
          traffic: 'Historisk trafik', forecast: 'Prognos', scenario: 'Scenario',
          demand: 'Simulera datum', closure: 'Vägavstängning',
          signals: 'Optimera signaler',
          history: 'Körhistorik',
        };
        let workspaceTask = 'home';
        const historyPanel = document.getElementById('history-panel');
        const historyBody = document.getElementById('history-body');
        const historyDetail = document.getElementById('history-detail');
        const historyDetailTitle = document.getElementById('history-detail-title');
        const historyDetailBody = document.getElementById('history-detail-body');

        // Phase 6 acceptance gate: from a result, the operator must reach the
        // exact ScenarioSpec, build ID, job record and validation report that
        // produced it — without hand-querying the API or reading server logs.
        // Everything below builds the DOM with textContent, never innerHTML:
        // job args echo user-supplied edge IDs and error strings.
        function appendDefinition(list, term, value) {
          if (value === null || value === undefined || value === '') return;
          const dt = document.createElement('dt');
          dt.textContent = term;
          const dd = document.createElement('dd');
          dd.textContent = String(value);
          list.append(dt, dd);
        }

        async function showJobDetail(jobId) {
          historyDetail.hidden = false;
          historyDetailTitle.textContent = jobId;
          historyDetailBody.textContent = 'Läser jobbposten…';
          // The job list scrolls; without this a row near the top opens the
          // detail below the fold and the click looks like it did nothing.
          historyDetail.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
          let record;
          try {
            const response = await fetch(
              `/api/jobs/${encodeURIComponent(jobId)}?t=` + Date.now());
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            record = await response.json();
          } catch (error) {
            historyDetailBody.textContent =
              `Jobbposten kunde inte läsas: ${error.message}`;
            return;
          }
          const args = record.args || {};
          const result = record.result || {};
          const spec = args.scenario_spec || result.scenario_spec
            || args.closure_search_spec || null;
          const list = document.createElement('dl');
          appendDefinition(list, 'Typ', record.kind);
          appendDefinition(list, 'Status', record.status);
          appendDefinition(list, 'Startad', record.started_at
            ? new Date(record.started_at * 1000).toLocaleString('sv-SE') : null);
          appendDefinition(list, 'Avslutad', record.finished_at
            ? new Date(record.finished_at * 1000).toLocaleString('sv-SE') : null);
          appendDefinition(list, 'Fel', record.error || result.error);
          if (Array.isArray(args.edges) && args.edges.length) {
            appendDefinition(list, 'Kanter', args.edges.join(', '));
          }
          appendDefinition(list, 'Datum', args.date || args.start_date);
          appendDefinition(list, 'Källa', args.source);
          if (spec) {
            appendDefinition(list, 'ScenarioSpec-id',
                             spec.scenario_id || spec.search_id);
            appendDefinition(list, 'Byggnads-id (demand)', spec.demand_build_id);
            appendDefinition(list, 'Nät-id', spec.network_build_id);
            appendDefinition(list, 'Fönster', spec.start_time
              ? `${spec.start_time} → ${spec.end_time}` : null);
            appendDefinition(list, 'Läge', spec.simulation_mode);
            if (Array.isArray(spec.seed_set) && spec.seed_set.length) {
              appendDefinition(list, 'Frön', spec.seed_set.join(', '));
            }
            if (Array.isArray(spec.closures) && spec.closures.length) {
              appendDefinition(list, 'Avstängningar', spec.closures
                .map(c => `${c.edge_id} ${c.start_time}–${c.end_time}`)
                .join(' · '));
            }
          }
          historyDetailBody.replaceChildren(list);

          // Does the active validation report describe THIS job's build?
          // Same honesty rule as the shield panel: state it, never imply it.
          if (spec?.demand_build_id) {
            const note = document.createElement('div');
            note.style.marginTop = '8px';
            try {
              const report = await (await fetch(
                'data/validation.json?t=' + Date.now())).json();
              if (!report.demand_build_id) {
                note.textContent = 'Valideringsrapporten anger inget bygge-id '
                  + '— den kan inte kopplas till detta jobb.';
              } else if (report.demand_build_id === spec.demand_build_id) {
                note.textContent = 'Valideringsrapporten gäller detta bygge '
                  + `(${report.demand_build_id.slice(0, 12)}, `
                  + `${report.overall}).`;
              } else {
                note.textContent = '⚠ Valideringsrapporten gäller ETT ANNAT '
                  + `bygge (${report.demand_build_id.slice(0, 12)}) än detta `
                  + 'jobb — dess grindar säger inget om denna körning.';
                note.style.color = '#f59e0b';
              }
            } catch {
              note.textContent = 'Valideringsrapporten kunde inte läsas.';
            }
            historyDetailBody.append(note);
          }

          if (spec) {
            const heading = document.createElement('div');
            heading.style.marginTop = '8px';
            heading.textContent = 'Exakt spec som kördes:';
            const pre = document.createElement('pre');
            pre.textContent = JSON.stringify(spec, null, 1);
            historyDetailBody.append(heading, pre);
          }
        }

        document.getElementById('history-detail-close')
          .addEventListener('click', () => { historyDetail.hidden = true; });

        async function loadHistory() {
          historyBody.textContent = 'Läser körhistorik…';
          try {
            const response = await fetch('/api/jobs?t=' + Date.now());
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const records = (await response.json()).jobs || [];
            if (!records.length) {
              historyBody.textContent = 'Inga körningar är registrerade ännu.';
              return;
            }
            const table = document.createElement('table');
            for (const record of records) {
              const row = document.createElement('tr');
              row.className = 'history-row';
              row.title = 'Visa exakt spec, bygge och jobbpost';
              if (record.id) {
                row.addEventListener('click', () => showJobDetail(record.id));
              }
              const status = document.createElement('td');
              status.textContent = record.status || 'okänd';
              status.className = `history-${record.status || 'unknown'}`;
              row.append(status);
              const kind = document.createElement('td');
              kind.textContent = record.kind || '–';
              row.append(kind);
              const time = document.createElement('td');
              time.textContent = record.started_at
                ? new Date(record.started_at * 1000).toLocaleString('sv-SE') : '–';
              row.append(time);
              const id = document.createElement('td');
              id.textContent = record.id || '–';
              id.title = record.error || '';
              row.append(id);
              table.append(row);
            }
            historyBody.replaceChildren(table);
          } catch (error) {
            historyBody.textContent = `Körhistorik kunde inte läsas: ${error.message}`;
          }
        }

        const trajCache = {};

        // Guards loadVehiclesFor against a stale fetch resolving after the
        // user has already switched to a different mode/scenario — without
        // this, rapid switching (e.g. Simulering A -> Historisk before A's
        // trajectory fetch resolves) could re-enable the vehicle overlay on
        // top of Historisk/Prognos, contradicting "vehicles are ALWAYS the
        // Simulering view". Found in review 2026-07-07.
        let modeToken = 0;
        // Aborts the in-flight trajectory fetch itself on a newer mode
        // switch, instead of letting it run to completion and only
        // discarding the result via the modeToken check below — a rapid
        // switch away no longer wastes the full download (found in review
        // 2026-07-10).
        let vehicleAbort = null;

        // Vehicles ARE the simulation now — no separate toggle. Whenever a
        // scenario provider has trajectories, they're loaded and shown
        // automatically; leaving Simulering turns them off automatically.
        async function loadVehiclesFor(provider, token) {
          // Cancel whatever trajectory fetch this call supersedes
          // UNCONDITIONALLY — including the "this provider has none, or it's
          // already cached" cases, where a still-running earlier fetch is
          // now pointless but this function would otherwise never touch it.
          vehicleAbort?.abort();
          vehicleAbort = null;
          if (!provider.trajectories) {
            Render.setVehicleMode(false);
            document.getElementById('traj-provenance').hidden = true;
            return;
          }
          if (!trajCache[provider.trajectories]) {
            vehicleAbort = new AbortController();
            let res;
            try {
              res = await fetch('data/scenarios/' + provider.trajectories
                                + '?t=' + Date.now(), { signal: vehicleAbort.signal });
            } catch (e) {
              if (e.name === 'AbortError') return;   // superseded — not a real failure
              throw e;
            }
            // Recalibration deletes scenario/trajectory files before
            // rebuilding them — a 404 here is a real, reachable state, and
            // parsing the server's HTML error page as JSON would surface
            // as a cryptic SyntaxError with no filename.
            if (!res.ok) {
              throw new Error(`trajectories ${provider.trajectories}: HTTP ${res.status}`);
            }
            trajCache[provider.trajectories] = await res.json();
          }
          if (token !== modeToken) return;   // a newer mode switch happened meanwhile
          const traj = trajCache[provider.trajectories];
          Render.setTrajectories(traj);
          Render.setVehicleMode(true);
          // F1 provenance: label the vehicles as the single run they are.
          // Older artifacts without the fields keep an unlabelled display
          // rather than a made-up claim.
          const provEl = document.getElementById('traj-provenance');
          if (traj?.seed != null) {
            const variantLabel = { 'calibrated.rou.xml': 'q50',
                                   'calibrated_v1.rou.xml': 'q10',
                                   'calibrated_v2.rou.xml': 'q90' }[traj.variant] || traj.variant;
            const sampled = traj.sampling?.enabled === true;
            provEl.textContent = `🚗 representativ körning (frö ${traj.seed}, ${variantLabel})`;
            provEl.title = 'Vägfärgerna är medelvärdet av 3 Monte Carlo-körningar; '
              + 'de animerade bilarna är EN av dessa körningar — representativ, '
              + 'inte identisk med medelvärdet.'
              + (sampled
                 ? ` Ett deterministiskt urval på ${traj.n_vehicles} av `
                   + `${traj.inserted_in_run} riktiga fordon ritas `
                   + `(${traj.sampling.max_vehicles_per_day} högst per dag); `
                   + 'flöden och sensorresultat använder fortfarande alla fordon.'
                 : traj.displayed_share != null
                 ? ` ${traj.n_vehicles} av ${traj.inserted_in_run} fordon ritas `
                   + '(resten kör delvis utanför kartans vägnät).'
                 : '')
              + (traj.n_unfinished
                 ? ` ${traj.n_unfinished} fordon hann inte fram före `
                   + 'scenariots slut och visas parkerade (ihåliga orange '
                   + 'prickar) vid sin sista kända position.'
                 : '');
            provEl.hidden = false;
          } else {
            provEl.hidden = true;
          }
        }

        let currentProvider = histProvider;

        function setMode(activeBtn, provider) {
          const token = ++modeToken;
          currentProvider = provider;
          for (const b of [btnHist, btnFore, btnScen]) {
            b.classList.toggle('active', b === activeBtn);
          }
          const isSim = activeBtn === btnScen;
          // The legend swaps meaning with the mode: Simulering colours roads
          // by certainty (near-sensor zones), Historisk/Prognos by traffic.
          document.body.dataset.viewmode = isSim ? 'sim' : 'flow';
          const panelWasShown = simPanel.classList.contains('show');
          simPanel.classList.toggle('show', isSim);
          // "Dag" only makes sense as a scrubber when there's more than one
          // day to scrub — a single-day scenario has a fixed date (as
          // before); a multi-day scenario (B3) gets a real day slider
          // ranging over ITS OWN days, not the full year.
          const simDays = isSim ? Math.ceil(provider.numQuarters / 96) : 1;
          dayGroup.classList.toggle('disabled', isSim && simDays <= 1);
          if (!isSim) {
            daySlider.max = YEAR_DAY_MAX;
            dayHint.textContent = '1 jan — 31 dec';
          } else if (simDays <= 1) {
            dayHint.textContent = '(scenariots datum är fast)';
          } else {
            daySlider.max = simDays - 1;
            const first = provider.epoch.toISOString().slice(0, 10);
            const last  = provider.dateFromQI((simDays - 1) * 96)
                                  .toISOString().slice(0, 10);
            dayHint.textContent = `${first} — ${last} (${simDays} dagar)`;
          }
          if (isSim !== panelWasShown) {
            // panel row appeared/disappeared → map's flex box resized
            requestAnimationFrame(() => Render.invalidateSize());
          }
          // BUG FIX: playback used to advance/wrap against the whole
          // YEAR's length (35 040 quarters) even for a one-day scenario
          // (96 quarters) — past quarter 96 the scenario has no data, so
          // at normal speeds the map would sit on "ingen data" for the
          // remaining ~99% of a fake extra year before ever looping back
          // to the start. Vehicles looked like they "never went away"
          // because entering/leaving them is driven by the same clock.
          State.setMaxQI(isSim ? provider.numQuarters : null);
          Controls.setSpeedMode(isSim);   // real-time seconds vs fast quarter-scrubbing
          Render.setProvider(provider);
          Controls.setProvider(provider);
          if (isSim) {
            loadVehiclesFor(provider, token);
          } else {
            Render.setVehicleMode(false);
            document.getElementById('traj-provenance').hidden = true;
          }
          // Entering a scenario always starts at its own beginning (qi=0
          // in the OLD provider could be past the scenario's own length).
          // Switching between Historisk <-> Prognos keeps the scrubbed
          // position instead — comparing the same day/time is useful.
          State.setQI(isSim ? 0 : State.qi);
          setSensorAuditAvailability(isSim ? provider : null);
          // Read the ACTUAL calibrated date off the scenario's own epoch —
          // not a variable that only updated when "Byt dag" was clicked
          // (otherwise a page reload after a day-change showed the old
          // hardcoded date even though the backend had a new one deployed).
          if (isSim && provider.epoch) {
            currentSimDate = provider.epoch.toISOString().slice(0, 10);
            currentSimSource = provider.source || 'historical';
            const srcLabel = currentSimSource === 'forecast' ? 'Prognos' : 'Historik';
            simDayHint.textContent = `${srcLabel}: ${currentSimDate}`;
            renderAgentDemand(provider, State.qi);
          } else if (!isSim) {
            simAgentHint.hidden = true;
          }
        }

        btnHist.addEventListener('click', () => setMode(btnHist, histProvider));

        async function openForecast() {
          // Lazy-load forecast on first click
          if (!foreProvider) {
            btnFore.textContent = 'Laddar…';
            btnFore.disabled = true;
            try {
              foreProvider = await new HistoricalProvider().load('data/flows_forecast.json');
            } catch (e) {
              console.error('Could not load flows_forecast.json — run build_agent1_flows.py', e);
              return;
            } finally {
              btnFore.textContent = 'Prognos 2027';
              btnFore.disabled = false;
            }
          }
          setMode(btnFore, foreProvider);
        }
        btnFore.addEventListener('click', openForecast);

        // ── Simulering (SUMO output — run run_scenario.py) ────────────────────
        // Shareable URLs: ?mode=scenario&file=<name>.json&qi=6, ?mode=forecast
        const params = new URLSearchParams(location.search);

        async function loadScenIndex(force = false) {
          if (scenIndex && !force) return;
          // no-store: re-runs overwrite index.json and scenario files in place
          const res = await fetch('data/scenarios/index.json', { cache: 'no-store' });
          if (!res.ok) throw new Error(`${res.status}`);
          scenIndex = await res.json();
          const current = scenSelect.value;
          // Build <option> elements directly instead of innerHTML template
          // strings — s.label includes OSM street names (run_scenario.py:
          // "Avstängning: " + street names), which are semi-trusted, user-
          // editable data, not literal app-authored content. textContent
          // escapes it automatically; the old template-string interpolation
          // did not. Found in a review 2026-07-10.
          scenSelect.replaceChildren(...scenIndex.scenarios.map(s => {
            const opt = document.createElement('option');
            opt.value = s.file;
            opt.textContent = `${s.label} · ${s.window}`;
            return opt;
          }));
          if ([...scenSelect.options].some(o => o.value === current)) {
            scenSelect.value = current;
          }
        }

        // ── Comparison view (baseline vs closure) ─────────────────────────
        // Colours the map by CHANGE rather than volume. It wraps the two
        // already-loaded providers instead of fetching anything, so it costs
        // no extra request and works for any closure the user simulates.
        const BASELINE_FILE = 'baseline.json';
        let activeScenarioFile = null;
        // Static per-edge spatial prior from network.geojson. DeltaProvider
        // needs it to invert confidence = prior * exp(-CV) and recover the
        // seed noise, which is what decides whether a change is claimable.
        const spatialPrior = {};
        for (const f of (networkPayload.features || [])) {
          if (f.geometry?.type !== 'LineString') continue;
          const p = f.properties || {};
          if (typeof p.confidence === 'number') spatialPrior[p.id] = p.confidence;
        }

        const deltaToggle = document.getElementById('delta-toggle');
        const deltaToggleWrap = document.getElementById('delta-toggle-wrap');
        const deltaLegend = document.getElementById('delta-legend');

        // A comparison needs a closure to compare AND a loaded baseline.
        function comparisonAvailable() {
          return !!(activeScenarioFile
                    && activeScenarioFile !== BASELINE_FILE
                    && scenProviders[BASELINE_FILE]
                    && scenProviders[activeScenarioFile]);
        }

        function wrapForComparison(provider) {
          if (!deltaToggle?.checked || !comparisonAvailable()) return provider;
          return new DeltaProvider(
            scenProviders[BASELINE_FILE], provider, spatialPrior);
        }

        function updateDeltaToggleVisibility() {
          const usable = comparisonAvailable();
          if (deltaToggleWrap) deltaToggleWrap.hidden = !usable;
          if (!usable && deltaToggle) deltaToggle.checked = false;
          if (deltaLegend) {
            const on = usable && deltaToggle?.checked;
            deltaLegend.hidden = !on;
            if (on) {
              deltaLegend.innerHTML =
                '<span style="color:#1f9d55;font-weight:600">grön = mindre trafik</span> · ' +
                '<span style="color:#64748b">grå = inom bruset</span> · ' +
                '<span style="color:#dc2626;font-weight:600">röd = mer trafik</span>';
            }
          }
        }

        // Ensure the baseline is in memory so a comparison is possible without
        // the user having to visit it first. Failure is non-fatal: the toggle
        // simply stays hidden rather than the app breaking.
        async function ensureBaselineLoaded() {
          if (scenProviders[BASELINE_FILE]) return;
          try {
            scenProviders[BASELINE_FILE] = await new HistoricalProvider()
              .load('data/scenarios/' + BASELINE_FILE + '?t=' + Date.now());
          } catch (e) {
            console.warn('baseline.json could not be loaded — comparison view unavailable', e);
          }
          updateDeltaToggleVisibility();
        }

        deltaToggle?.addEventListener('change', () => {
          if (!activeScenarioFile || !scenProviders[activeScenarioFile]) return;
          setMode(btnScen, wrapForComparison(scenProviders[activeScenarioFile]));
          updateDeltaToggleVisibility();
        });

        // Guards against rapid scenario-dropdown switching: an earlier,
        // slower fetch resolving after a later one would otherwise silently
        // revert the map to the previously-selected scenario even though
        // <select> already shows the new one. Found in review 2026-07-07.
        let scenarioToken = 0;
        // Same real-cancellation upgrade as vehicleAbort above, applied to
        // the scenario JSON fetch itself.
        let scenarioAbort = null;

        async function activateScenario(file, { fresh = false } = {}) {
          const token = ++scenarioToken;
          // Cancel whatever scenario fetch this call supersedes UNCONDITIONALLY
          // — even when this call's own file is already cached and needs no
          // fetch of its own, a still-running earlier fetch is now pointless
          // and should stop consuming bandwidth, not just have its result
          // discarded once it eventually lands.
          scenarioAbort?.abort();
          scenarioAbort = null;
          if (fresh) delete scenProviders[file];   // file was overwritten on disk
          if (!scenProviders[file]) {
            scenarioAbort = new AbortController();
            try {
              scenProviders[file] = await new HistoricalProvider()
                .load('data/scenarios/' + file + '?t=' + Date.now(),
                      { signal: scenarioAbort.signal });
            } catch (e) {
              if (e.name === 'AbortError') return;   // superseded — not a real failure
              throw e;
            }
          }
          if (token !== scenarioToken) return;   // a newer scenario switch happened meanwhile
          activeScenarioFile = file;
          // Load the baseline alongside, so the comparison toggle is usable
          // immediately instead of only after the user visits the baseline.
          if (file !== BASELINE_FILE && !scenProviders[BASELINE_FILE]) {
            await ensureBaselineLoaded();
            if (token !== scenarioToken) return;   // superseded while loading
          }
          showClosureImpact(scenProviders[file]);
          setMode(btnScen, wrapForComparison(scenProviders[file]));
          updateDeltaToggleVisibility();
          // Scenario files start at their own epoch (window start)
          State.setQI(params.get('qi') ? Number(params.get('qi')) : 0);
        }

        // What a closure costs: how many vehicles it displaces and how far
        // it pushes them. A count and a detour — never a delay: congestion
        // feedback on this network converged at 0.0% change, so simulated
        // delay is noise, while displaced vehicles and detour distance are
        // properties of the measured demand and the network.
        function showClosureImpact(provider) {
          const el = document.getElementById('closure-impact');
          const d = provider?.disruption;
          if (!d || !provider.closedEdges?.length) { el.hidden = true; return; }
          const sv = (n) => n.toLocaleString('sv-SE');
          const parts = [`🚧 ${sv(d.vehicles_affected)} fordon berörs`];
          if (d.added_vehicle_hours != null) {
            parts.push(`+${d.added_vehicle_hours.toLocaleString('sv-SE',
                        { maximumFractionDigits: 1 })} fordonstimmar omväg`);
          }
          if (d.added_metres_median) {
            parts.push(`median +${sv(Math.round(d.added_metres_median))} m`);
          }
          if (d.vehicles_no_detour) {
            parts.push(`⚠ ${sv(d.vehicles_no_detour)} utan omväg`);
          }
          el.textContent = parts.join(' · ');
          el.title = 'Antal kalibrerade fordon vars rutt korsar den avstängda '
            + 'sträckan medan den är stängd, och hur mycket längre de tvingas '
            + 'köra (billigaste lagliga väg med avstängningen jämfört med utan). '
            + 'Detta är en RÄKNING och en OMVÄG, inte en försening: nätet kör i '
            + 'friflöde (återkopplingen konvergerade på 0,0 % förändring), så '
            + 'simulerad försening bär nästan ingen signal. '
            + 'Medianen är ofta 0 även för en hårt trafikerad gata — de flesta '
            + 'får en gratis parallellgata, kostnaden ligger i svansen, och '
            + 'därför är summan (fordonstimmar) huvudtalet. '
            + '"Utan omväg" = destinationen går inte längre att nå med bil.';
          el.hidden = false;
        }

        async function openScenario() {
          try {
            await loadScenIndex();
            const wanted = params.get('file');
            if (wanted && [...scenSelect.options].some(o => o.value === wanted)) {
              scenSelect.value = wanted;
            }
            await activateScenario(scenSelect.value);
          } catch (e) {
            console.error('Inga scenarier — kör run_scenario.py först', e);
          }
        }
        btnScen.addEventListener('click', openScenario);

        scenSelect.addEventListener('change', e => activateScenario(e.target.value));

        // ── Klicka-och-stäng vägar — nu en del av Simulering-panelen ──────────
        const btnClose   = document.getElementById('close-mode-btn');
        const btnRun     = document.getElementById('close-run-btn');
        const btnCancel  = document.getElementById('close-cancel-btn');
        const pickBanner = document.getElementById('pick-banner');
        const pickCount  = document.getElementById('pick-count');
        const selected = new Set();
        // One road-closure workspace, one active tool. The three values are
        // different scopes of the same operation, not separate app modes.
        let closureTool = null; // null | 'simulate' | 'suggest' | 'monthly'
        let closureJobRunning = false;
        let apiAvailable = false;

        // ── Byt simuleringsdag — den andra halvan av "vilka dagar vill jag
        // simulera": en långsammare, uppenbart annorlunda åtgärd (~6 min,
        // kalibrerar om HELA dygnets efterfrågan) än att bara stänga en väg.
        const btnDay     = document.getElementById('change-day-btn');
        const dayBanner  = document.getElementById('day-banner');
        const dateInput  = document.getElementById('new-date-input');
        const daysInput  = document.getElementById('new-days-input');
        const btnDayRun  = document.getElementById('day-run-btn');
        const btnDayCancel = document.getElementById('day-cancel-btn');
        const recalProgress = document.getElementById('recal-progress');
        const recalProgressFill = document.getElementById('recal-progress-fill');
        const recalProgressLabel = document.getElementById('recal-progress-label');
        const simDayHint = document.getElementById('sim-day-hint');
        const simAgentHint = document.getElementById('sim-agent-hint');
        let dayPickMode = false;
        let recalibrationJobRunning = false;
        let currentSimDate = '2025-09-16';
        let currentSimSource = 'historical';

        function renderAgentDemand(provider, qi) {
          const demand = provider.agentDemand;
          if (!demand?.n_agents || !demand.purpose_counts) {
            simAgentHint.hidden = true;
            return;
          }
          const counts = demand.purpose_counts_by_quarter?.[qi] ?? demand.purpose_counts;
          const total = Object.values(counts).reduce((sum, value) => sum + value, 0);
          if (!total) {
            simAgentHint.textContent = 'Resor nu: inga avgångar';
            simAgentHint.title = 'Inga kalibrerade fordonsavgångar i denna 15-minutersruta';
            simAgentHint.hidden = false;
            return;
          }
          // "through" är en GEOGRAFISK kategori (start OCH mål utanför
          // kartområdet), inte ett ärende — en pendlare som kör genom
          // området till ett jobb utanför räknas hit. Den gamla etiketten
          // "genomfart" lästes som "saknar ärende" (Gustav 2026-07-17,
          // 07:54-skärmbilden: "varför så lite arbete vid 8-tiden?" — svaret
          // var till stor del att rusningens pendlare låg i denna kategori).
          const labels = { work: 'arbete', arbete: 'arbete', service: 'service',
                           leisure: 'fritid', fritid: 'fritid',
                           through: 'passerar området',
                           external: 'extern', unknown: 'okänd' };
          const parts = Object.entries(counts).map(([purpose, count]) =>
            `${labels[purpose] || purpose} ${Math.round(100 * count / total)}%`);
          simAgentHint.textContent = `Resor nu: ${parts.join(' · ')}`;
          simAgentHint.title =
            `${total} individuella kalibrerade avgångar i denna 15-minutersruta.\n` +
            'passerar området = både start och mål utanför kartområdet ' +
            '(inkluderar t.ex. pendlare som kör igenom) · extern = ena änden ' +
            'utanför området · arbete/service/fritid = resans ärende inne i ' +
            'området. Andelarna beskriver den sensor-förklarade simulerade ' +
            'trafiken, inte all verklig trafik.';
          simAgentHint.hidden = false;
        }

        window.addEventListener('tick', event => {
          if (currentProvider?.isScenario) {
            renderAgentDemand(currentProvider, event.detail.qi);
            renderSensorAudit(currentProvider, event.detail.qi);
          }
        });

        // ── G3: assembled validation report (static file — works even
        //    without the API server). Button shows only when the file
        //    exists; panel renders every gate with its status.
        const validationBtn = document.getElementById('validation-btn');
        const validationPanel = document.getElementById('validation-panel');
        const validationBody = document.getElementById('validation-body');
        const V_LABELS = {
          counts_fit: 'Sensorräkningar', structure: 'Strukturrealism',
          purposes: 'Ärenden & längder', simulation: 'Simuleringshälsa',
          sensor_output: 'SUMO sensorutdata', multi_day: 'Flerdagskontinuitet',
          held_out: 'Utelämnad-station (LOSO)',
        };
        const V_MARK = { pass: ['✓', 'v-pass'], warn: ['⚠', 'v-warn'],
                         missing: ['–', 'v-missing'], info: ['ℹ', 'v-info'] };

        function describeSection(name, s) {
          if (s.status === 'missing') return s.reason || 'saknas';
          switch (name) {
            case 'counts_fit':
              return `GEH<5 på ${s.geh_pct}% av intervallen, `
                + `${s.infeasible_intervals} olösliga kvartar, ${s.vehicles} fordon`;
            case 'structure':
              return `destinationer <200 m från sensor ${s.dest_within_200m_pct}% `
                + `(nätbaslinje ${s.dest_baseline_pct}%) · L1 mot RVU `
                + `${s.trip_length_l1_vs_rvu} · fortsättning efter sensor `
                + `median ${Math.round(s.onward_after_sensor_median_m)} m`
                + (s.flags?.length ? ` · FLAGGOR: ${s.flags.join('; ')}` : '');
            case 'purposes': {
              const l = s.purpose_length_km || {};
              const rows = ['arbete', 'service', 'fritid']
                .filter(p => l[p]).map(p => `${p} ${l[p].median_km} km`);
              return `medianlängd: ${rows.join(' · ')}`
                + (s.ordering_violated ? ' · ORDNING BRUTEN (fritid ska vara längst)' : '');
            }
            case 'simulation':
              return (s.seeds || []).map(x =>
                `frö ${x.seed}: ${x.inserted}/${x.loaded} insatta, `
                + `${x.unfinished} ofullbordade, ${x.teleports} teleport`).join(' · ')
                + (s.flags?.length ? ` · FLAGGOR: ${s.flags.join('; ')}` : '');
            case 'sensor_output':
              return `${s.edge_quarters ?? '–'} riktade `
                + `${s.aggregation_minutes === 60 ? 'sensor-timmar' : 'intervall'} · `
                + `GEH<5 ${s.geh_lt_5_pct ?? '–'}% · `
                + (s.reason || 'rå SUMO edgeData och stationsaggregering');
            case 'multi_day':
              return s.not_applicable ? 'inte tillämpligt för en dag' :
                `${s.days ?? '–'} dagar · `
                + (s.errors?.length ? s.errors.join('; ') : 'komplett boundary-bevis');
            case 'held_out':
              return `medianåterskapande ${s.median_ratio} — ${s.note}`;
          }
          return '';
        }

        // ── "Which study is actually in force" (Phase 6 acceptance gate) ──
        // A cancelled or failed study leaves the PREVIOUS published study on
        // screen. A transient alert() is not a label: once dismissed, the map
        // shows the old study with nothing saying so. This banner persists
        // until the operator acknowledges it, and always names both the study
        // that failed and the one still being displayed.
        const studyState = document.getElementById('study-state');
        const studyStateText = document.getElementById('study-state-text');
        document.getElementById('study-state-dismiss')
          .addEventListener('click', () => { studyState.hidden = true; });

        function describeActiveStudy() {
          const srcLabel = currentSimSource === 'forecast' ? 'prognos' : 'historik';
          const build = activeStudyBuildId();
          return `${currentSimDate} (${srcLabel})`
            + (build ? `, bygge ${build.slice(0, 12)}` : '');
        }

        function announceStudyOutcome(kind, outcome, attempted, detail = '') {
          const verb = outcome === 'cancelled' ? 'avbröts' : 'misslyckades';
          studyStateText.textContent =
            `${kind} för ${attempted} ${verb}. Kartan visar fortfarande den `
            + `föregående studien: ${describeActiveStudy()}.`
            + (detail ? ` (${detail})` : '');
          studyState.hidden = false;
        }

        function clearStudyOutcome() {
          studyState.hidden = true;
        }

        // The build the ACTIVE scenario was calibrated from — the identity
        // the validation report must match to be about what you are
        // looking at. Null in Historisk/Prognos mode (no scenario study).
        function activeStudyBuildId() {
          const active = scenIndex?.scenarios.find(s => s.file === scenSelect.value);
          return active?.scenario_spec?.demand_build_id
            || active?.build_id || active?.demand_signature || null;
        }

        async function loadValidation() {
          let res;
          try {
            res = await fetch('data/validation.json?t=' + Date.now());
          } catch { return; }
          if (!res.ok) return;
          const r = await res.json();
          // Phase 6 acceptance gate: this panel must reflect the ACTIVE
          // study's build. validation.json is ONE global artifact for
          // whichever demand was calibrated when it ran — when the loaded
          // scenario comes from a different build, its gates say nothing
          // about what is on screen, so the shield must not read "pass".
          const studyBuild = activeStudyBuildId();
          const stale = Boolean(studyBuild && r.demand_build_id &&
                                studyBuild !== r.demand_build_id);
          const [mark, cls] = stale
            ? V_MARK.missing
            : (V_MARK[r.overall] || V_MARK.missing);
          validationBtn.hidden = false;
          validationBtn.innerHTML = `Validering <span class="${cls}">${mark}</span>`;
          const rows = Object.entries(r.sections).map(([name, s]) => {
            const [m, c] = V_MARK[s.status] || V_MARK.missing;
            return `<tr><td class="${c}">${m}</td>`
              + `<td><strong>${V_LABELS[name] || name}</strong><br>`
              + `<span class="v-detail">${describeSection(name, s)}</span><br>`
              + (s.gate ? `<span class="v-detail" style="opacity:.7">grind: ${s.gate}</span>` : '')
              + `</td></tr>`;
          }).join('');
          const provenance = `bygge ${r.demand_window} (${r.demand_source})`
            + (r.demand_build_id ? ` · id ${r.demand_build_id.slice(0, 12)}` : '')
            + ` · genererad ${r.generated_at}`;
          validationBody.innerHTML =
            (stale
              ? `<div class="v-detail" id="validation-stale">⚠ Rapporten gäller ` +
                `ett ANNAT bygge än det laddade scenariot ` +
                `(scenario ${studyBuild.slice(0, 12)}). Grindarna nedan säger ` +
                `ingenting om det du ser på kartan — kör om valideringen för ` +
                `det aktiva bygget.</div>`
              : '')
            + `<table${stale ? ' class="v-stale"' : ''}>${rows}</table>`
            + `<div class="v-detail" style="margin-top:4px">${provenance}</div>`;
        }
        validationBtn.addEventListener('click', () => {
          validationPanel.hidden = !validationPanel.hidden;
          if (!validationPanel.hidden) loadValidation();
          Render.invalidateSize?.();
        });
        loadValidation();

        // ── Sensor audit ─────────────────────────────────────────────────────
        // The map's road colour is an ensemble mean while the vehicle overlay
        // is one seed.  Keep all source/target/result values in one compact
        // table so users never have to infer count delivery from dot density.
        const sensorAuditBtn = document.getElementById('sensor-audit-btn');
        const sensorAuditPanel = document.getElementById('sensor-audit-panel');
        const sensorAuditHeading = document.getElementById('sensor-audit-heading');
        const sensorAuditMeta = document.getElementById('sensor-audit-meta');
        const sensorAuditSourceLabel = document.getElementById('sensor-audit-source-label');
        const sensorAuditBody = document.getElementById('sensor-audit-body');
        const AUDIT_VARIANT_LABEL = {
          edge_shares: 'q50', edge_shares_q10: 'q10', edge_shares_q90: 'q90',
        };
        let renderedSensorAuditProvider = null;
        let renderedSensorAuditQI = null;

        function auditValue(value) {
          if (value === null || value === undefined || !Number.isFinite(Number(value))) return '–';
          // Source counts and SUMO seed entries are integers. Targets and the
          // raw (pre-rounding) ensemble mean may be fractional; display at
          // millivehicle precision and keep the exact value in the title.
          return String(Math.round(Number(value) * 1000) / 1000);
        }

        function auditSum(values) {
          // Physical-station sum with null propagation: a missing directed
          // value must make the station value unknown, never silently zero.
          let total = 0;
          for (const value of values) {
            if (value === null || value === undefined ||
                !Number.isFinite(Number(value))) return null;
            total += Number(value);
          }
          return Math.round(total * 1000) / 1000;
        }

        function auditSimMean(entry, interval) {
          // Prefer the pre-rounding ensemble mean; older payloads only have
          // the map's rounded integer.
          return entry.simulated_mean_raw?.[interval]
            ?? entry.simulated_mean?.[interval];
        }

        function appendAuditValue(row, value, className = '') {
          const cell = document.createElement('td');
          cell.textContent = auditValue(value);
          if (value !== null && value !== undefined) cell.title = String(value);
          if (className) cell.className = className;
          row.append(cell);
        }

        function sensorAuditInterval(provider, qi) {
          const start = provider.dateFromQI(qi).toISOString().slice(11, 16);
          const end = provider.dateFromQI(qi + 1).toISOString().slice(11, 16);
          return `${start}–${end}`;
        }

        function renderSensorAudit(provider, qi, force = false) {
          if (sensorAuditPanel.hidden) return;
          const audit = provider?.sensorAudit;
          if (!audit?.directions?.length) return;
          const interval = Math.max(0, Math.min(
            provider.numQuarters - 1, Math.floor(Number(qi) || 0)));
          // Values only change per 15-minute interval. Avoid rebuilding a
          // table on every animation frame during vehicle playback.
          if (!force && renderedSensorAuditProvider === provider &&
              renderedSensorAuditQI === interval) return;
          renderedSensorAuditProvider = provider;
          renderedSensorAuditQI = interval;

          const variant = AUDIT_VARIANT_LABEL[audit.representative?.variant]
            || audit.representative?.variant || 'okänd variant';
          const seed = audit.representative?.seed;
          sensorAuditHeading.textContent =
            `Sensorflöden per riktning · ${sensorAuditInterval(provider, interval)} · `
            + `visad körning ${variant}${seed == null ? '' : ` (frö ${seed})`}`;
          sensorAuditSourceLabel.textContent = audit.source === 'forecast'
            ? 'Prognosvärde' : 'Sensorvärde';

          const sourceText = audit.source === 'forecast'
            ? 'Prognosvärde är simuleringsunderlaget, inte en uppmätt räkning.'
            : 'Sensorvärde är den levererade räkningen.';
          const totalText = 'En tvåvägs-total visas en gång per fysisk station; riktningsraderna under är modellens fördelning av samma mätning.';
          const ensembleFit = audit.fit?.ensemble;
          const representativeFit = audit.fit?.representative;
          const fitText = audit.comparison === 'calibration' && ensembleFit?.available
            ? ` Medel: ${ensembleFit.geh_lt_5_pct}% GEH<5; visad ${variant}: `
              + `${representativeFit?.geh_lt_5_pct ?? '–'}% GEH<5.`
            : ' Scenarioflöden kan avvika från baslinjemålet när vägar är stängda.';
          const targetWasRebuilt = audit.provenance?.targets === 'reconstructed_current_inputs';
          sensorAuditMeta.textContent = sourceText + ' ' + totalText + fitText
            + (targetWasRebuilt
              ? ' Kontrollmålen är återbyggda från nuvarande indata; nästa omkalibrering låser dem i bygget.'
              : '');

          function sourceCell(value, kindText) {
            const source = document.createElement('td');
            const sourceNumber = document.createElement('strong');
            sourceNumber.textContent = auditValue(value);
            if (value != null) source.title = String(value);
            const sourceKind = document.createElement('span');
            sourceKind.className = 'audit-source-kind';
            sourceKind.textContent = kindText;
            source.append(sourceNumber, sourceKind);
            return source;
          }

          function directedRow(entry) {
            const row = document.createElement('tr');
            const sensor = document.createElement('td');
            sensor.textContent = `${entry.sensor_id} · ${entry.name}`;
            row.append(sensor);
            const direction = document.createElement('td');
            direction.textContent = entry.direction || '–';
            row.append(direction);
            row.append(sourceCell(entry.source_value?.[interval], 'riktad'));
            appendAuditValue(row, entry.target_representative?.[interval]);
            appendAuditValue(row, entry.simulated_representative?.[interval], 'audit-sim');
            appendAuditValue(row, entry.target_mean?.[interval]);
            appendAuditValue(row, auditSimMean(entry, interval), 'audit-sim');
            return row;
          }

          // A two-way Total station is ONE physical observation delivered as
          // the same summed count on both directed edges. Render it as one
          // station row plus indented modelled-direction rows, so the raw
          // Total is never repeated as though it were two measurements.
          function stationRows(entries) {
            const rows = [];
            const parent = document.createElement('tr');
            parent.className = 'audit-station';
            const sensor = document.createElement('td');
            sensor.textContent = `${entries[0].sensor_id} · ${entries[0].name}`;
            parent.append(sensor);
            const direction = document.createElement('td');
            direction.textContent = entries.map(e => e.direction || '?').join('+');
            parent.append(direction);
            parent.append(sourceCell(entries[0].source_value?.[interval],
                                     'tvåvägs-total (en mätning)'));
            appendAuditValue(parent,
              auditSum(entries.map(e => e.target_representative?.[interval])));
            appendAuditValue(parent,
              auditSum(entries.map(e => e.simulated_representative?.[interval])),
              'audit-sim');
            appendAuditValue(parent,
              auditSum(entries.map(e => e.target_mean?.[interval])));
            appendAuditValue(parent,
              auditSum(entries.map(e => auditSimMean(e, interval))), 'audit-sim');
            rows.push(parent);
            for (const entry of entries) {
              const row = document.createElement('tr');
              row.className = 'audit-child';
              const child = document.createElement('td');
              child.textContent = '↳ riktning';
              row.append(child);
              const childDirection = document.createElement('td');
              childDirection.textContent = entry.direction || '–';
              row.append(childDirection);
              const childSource = document.createElement('td');
              childSource.textContent = 'modellandel av total';
              childSource.className = 'audit-source-kind';
              row.append(childSource);
              appendAuditValue(row, entry.target_representative?.[interval]);
              appendAuditValue(row, entry.simulated_representative?.[interval], 'audit-sim');
              appendAuditValue(row, entry.target_mean?.[interval]);
              appendAuditValue(row, auditSimMean(entry, interval), 'audit-sim');
              rows.push(row);
            }
            return rows;
          }

          const groups = new Map();
          for (const entry of audit.directions) {
            if (!groups.has(entry.sensor_id)) groups.set(entry.sensor_id, []);
            groups.get(entry.sensor_id).push(entry);
          }
          const rows = [];
          for (const entries of groups.values()) {
            const isStationTotal = entries.length > 1 &&
              entries.every(e => e.measurement === 'two_way_total');
            if (isStationTotal) rows.push(...stationRows(entries));
            else rows.push(...entries.map(directedRow));
          }
          sensorAuditBody.replaceChildren(...rows);
        }

        function setSensorAuditAvailability(provider) {
          const available = Boolean(provider?.isScenario && provider.sensorAudit?.directions?.length);
          sensorAuditBtn.hidden = !available;
          if (!available) {
            sensorAuditPanel.hidden = true;
            sensorAuditBtn.classList.remove('active');
            renderedSensorAuditProvider = null;
            renderedSensorAuditQI = null;
            return;
          }
          if (!sensorAuditPanel.hidden) renderSensorAudit(provider, State.qi, true);
        }

        sensorAuditBtn.addEventListener('click', () => {
          sensorAuditPanel.hidden = !sensorAuditPanel.hidden;
          sensorAuditBtn.classList.toggle('active', !sensorAuditPanel.hidden);
          if (!sensorAuditPanel.hidden) renderSensorAudit(currentProvider, State.qi, true);
          Render.invalidateSize?.();
        });

        // Scopes the "recover a recalibration on page load" check (below)
        // to THIS tab's own session, not every future visitor forever.
        // /api/recalibrate/status is server-global — without this, a
        // recalibration anyone ever ran stays "done" in server memory
        // indefinitely, so a brand-new visitor with no query params would
        // silently get switched out of the default Historisk 2025 view
        // into whatever Simulering scenario the LAST recalibration (by
        // anyone, at any point) produced. Found via browser testing
        // 2026-07-12: a completely fresh page load landed in Simulering
        // mode on a 2027 forecast scenario nobody in that session asked
        // for. sessionStorage survives a reload within the same tab (the
        // original incident this code protects against — CLAUDE.md's
        // 2026-07-06 production incident) but not a new tab/visitor.
        const RECAL_SESSION_KEY = 'pendingRecal';
        function rememberPendingRecal(date, source, days) {
          try {
            sessionStorage.setItem(RECAL_SESSION_KEY, JSON.stringify({ date, source, days }));
          } catch (_) { /* sessionStorage unavailable (private mode etc.) — degrade to no recovery */ }
        }
        function forgetPendingRecal() {
          try { sessionStorage.removeItem(RECAL_SESSION_KEY); } catch (_) {}
        }
        function pendingRecalMatches(status) {
          let pending;
          try { pending = JSON.parse(sessionStorage.getItem(RECAL_SESSION_KEY) || 'null'); }
          catch (_) { return false; }
          return !!pending && pending.date === status.date &&
                 pending.source === status.source &&
                 Number(pending.days || 1) === Number(status.days || 1);
        }

        // Linear interpolation between the two measured/documented anchor
        // points (B3, IMPROVEMENT_PLAN.md): 1 day ~6 min, a full week ~45 min.
        function estimatedMinutes(days) {
          return Math.round(6 + (45 - 6) / 6 * (Math.max(1, days) - 1));
        }
        function showJobProgress(label, elapsedS = 0, expectedS = 90) {
          // This is deliberately an estimate, capped below 100 until the
          // server reports completion. It communicates work without claiming
          // an exact percentage for a multi-stage simulation job.
          const pct = Math.min(92, Math.max(4, 4 + elapsedS / expectedS * 88));
          recalProgress.classList.add('show');
          recalProgressFill.style.width = `${pct.toFixed(0)}%`;
          recalProgressLabel.textContent = `${label} · ${elapsedS}s`;
        }
        function showRecalibrationProgress(elapsedS = 0) {
          showJobProgress('Bygger simulering', elapsedS,
                          estimatedMinutes(Number(daysInput.value) || 1) * 60);
        }
        function hideRecalibrationProgress() {
          recalProgress.classList.remove('show');
          recalProgressFill.style.width = '4%';
        }
        function updateDayRunLabel() {
          btnDayRun.textContent = `Räkna om (~${estimatedMinutes(Number(daysInput.value))} min)`;
        }
        function selectedDemandDate(source) {
          const range = DATE_RANGES[source];
          let date = String(dateInput.value || '').trim();
          const valid = /^\d{4}-\d{2}-\d{2}$/.test(date);
          if (!valid) {
            // Safari and restored form state can expose an empty value even
            // though the date control is visible. Never send an empty legacy
            // query/body value; use the active scenario date or the source's
            // documented fallback and make the value visible to the user.
            date = (currentSimSource === source &&
                    /^\d{4}-\d{2}-\d{2}$/.test(currentSimDate))
              ? currentSimDate : range.fallback;
            dateInput.value = date;
          }
          if (date < range.min || date > range.max) {
            date = range.fallback;
            dateInput.value = date;
          }
          return date;
        }
        async function requestRecalibration(date, source, days, demand_spec) {
          // New servers receive one versioned JSON contract. During local
          // development a stale serve.py can still be running and only know
          // the legacy query form; retry that form only for its specific date
          // validation response so other API errors remain visible.
          const parseResponse = async response => {
            const body = await response.text();
            let data;
            try {
              data = body ? JSON.parse(body) : {};
            } catch (_) {
              throw new Error('servern gav ett ofullständigt startsvar');
            }
            return { response, data };
          };
          let result = await parseResponse(await fetch('/api/recalibrate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ demand_spec })
          }));
          const errorText = String(result.data.error || '');
          if (!result.response.ok && result.response.status === 400 &&
              /datum(?: saknas)? .*YYYY-MM-DD/i.test(errorText)) {
            const query = new URLSearchParams({
              date, source, days: String(days)
            });
            result = await parseResponse(await fetch(
              `/api/recalibrate?${query.toString()}`, { method: 'POST' }));
          }
          if (!result.response.ok) {
            throw new Error(result.data.error || `HTTP ${result.response.status}`);
          }
          return result.data;
        }
        daysInput.addEventListener('input', () => {
          const v = Math.max(1, Math.min(7, Math.round(Number(daysInput.value)) || 1));
          daysInput.value = v;
          updateDayRunLabel();
        });

        // ── Föreslå stängningstid (IMPROVEMENT_PLAN.md Phase C5) — reuses the SAME
        // edge click-picking as the plain closure flow above (`selected`),
        // just with a different meaning: which road(s) to search AROUND,
        // not close outright.
        const btnSuggest       = document.getElementById('suggest-mode-btn');
        const suggestBanner    = document.getElementById('suggest-banner');
        const suggestPickCount = document.getElementById('suggest-pick-count');
        const durationInput    = document.getElementById('suggest-duration-input');
        const btnSuggestRun    = document.getElementById('suggest-run-btn');
        const btnSuggestCancel = document.getElementById('suggest-cancel-btn');
        const suggestResults      = document.getElementById('suggest-results');
        const suggestResultsTitle = document.getElementById('suggest-results-title');
        const suggestResultsMeta  = document.getElementById('suggest-results-meta');
        const suggestResultsBody  = document.getElementById('suggest-results-tbody');
        const btnSuggestResultsClose = document.getElementById('suggest-results-close');
        let suggestJobRunning = false;

        // ── Bästa arbetsperiod (Phase 4 step 7) — the resumable monthly
        // closure search. Reuses the same edge picking (`selected`); every
        // candidate the server ranks is SUMO-verified. A held-out-validated
        // proxy may screen the legal calendar first; without that gate the
        // server falls back to a bounded exhaustive search.
        const btnMonthly        = document.getElementById('monthly-mode-btn');
        const monthlyBanner     = document.getElementById('monthly-banner');
        const monthlyPickCount  = document.getElementById('monthly-pick-count');
        const monthlySourceSeg  = document.getElementById('monthly-source-seg');
        const monthlyDateStart  = document.getElementById('monthly-date-start');
        const monthlyDateEnd    = document.getElementById('monthly-date-end');
        const monthlyBandStart  = document.getElementById('monthly-band-start');
        const monthlyBandEnd    = document.getElementById('monthly-band-end');
        const monthlyFullday    = document.getElementById('monthly-fullday');
        const monthlyWorkHours  = document.getElementById('monthly-work-hours');
        const monthlyMaxDays    = document.getElementById('monthly-max-days');
        const monthlyPeriodMode = document.getElementById('monthly-period-mode');
        const monthlyWeekdays   = document.getElementById('monthly-weekdays');
        const monthlyProgress   = document.getElementById('monthly-progress-hint');
        const monthlyPreflight  = document.getElementById('monthly-preflight');
        const btnMonthlyRun     = document.getElementById('monthly-run-btn');
        const btnMonthlyCancel  = document.getElementById('monthly-cancel-btn');
        const monthlyResults      = document.getElementById('monthly-results');
        const monthlyResultsTitle = document.getElementById('monthly-results-title');
        const monthlyResultsMeta  = document.getElementById('monthly-results-meta');
        const monthlyResultsBody  = document.getElementById('monthly-results-tbody');
        const monthlyColPeriod    = document.getElementById('monthly-col-period');
        const monthlyColPrimary   = document.getElementById('monthly-col-primary');
        const monthlyColSecondary = document.getElementById('monthly-col-secondary');
        const btnMonthlyResultsClose = document.getElementById('monthly-results-close');
        let monthlyJobRunning = false;

        // ── Optimera signaler (IMPROVEMENT_PLAN.md Phase D5) — no picking mode:
        // acts on the currently loaded scenario and sends its full
        // ScenarioSpec, including the exact closure/window/seed identity.
        const btnOptimize            = document.getElementById('optimize-signals-btn');
        const optimizeResults        = document.getElementById('optimize-results');
        const optimizeResultsTitle   = document.getElementById('optimize-results-title');
        const optimizeResultsProv    = document.getElementById('optimize-results-provenance');
        const optimizeResultsCard    = document.getElementById('optimize-results-card');
        const optimizeResultsMeta    = document.getElementById('optimize-results-meta');
        const optimizeResultsClose   = document.getElementById('optimize-results-close');
        const optimizePlanBody       = document.getElementById('optimize-plan-tbody');

        function refreshCloseUI() {
          const showNormal = !closureTool && !dayPickMode;
          const showApiActions = showNormal && apiAvailable;
          const showScenarioPicker = ['scenario', 'demand', 'closure', 'signals'].includes(workspaceTask);
          scenSelect.style.display   = showNormal && showScenarioPicker ? '' : 'none';
          simDayHint.style.display   = showNormal ? '' : 'none';
          btnClose.style.display     = showApiActions && workspaceTask === 'closure' ? '' : 'none';
          btnDay.style.display       = showApiActions && workspaceTask === 'demand' ? '' : 'none';
          btnSuggest.style.display   = showApiActions && workspaceTask === 'closure' ? '' : 'none';
          btnMonthly.style.display   = showApiActions && workspaceTask === 'closure' ? '' : 'none';
          btnOptimize.style.display  = showApiActions && workspaceTask === 'signals' ? '' : 'none';
          pickBanner.classList.toggle('show', closureTool === 'simulate');
          dayBanner.classList.toggle('show', dayPickMode);
          suggestBanner.classList.toggle('show', closureTool === 'suggest');
          monthlyBanner.classList.toggle('show', closureTool === 'monthly');
          pickCount.textContent = `${selected.size} vald${selected.size === 1 ? '' : 'a'}`;
          btnRun.disabled = selected.size === 0;
          if (closureTool === 'suggest') {
            suggestPickCount.textContent =
              `${selected.size} vald${selected.size === 1 ? '' : 'a'}`;
            btnSuggestRun.disabled = suggestJobRunning || selected.size === 0;
          }
          if (closureTool === 'monthly') {
            monthlyPickCount.textContent =
              `${selected.size} vald${selected.size === 1 ? '' : 'a'}`;
            btnMonthlyRun.disabled = monthlyJobRunning || selected.size === 0;
          }
          Render.setPending([...selected]);
        }

        async function openWorkspace(task) {
          workspaceTask = task;
          document.body.dataset.task = task;
          taskHome.hidden = task !== 'history';
          historyPanel.hidden = task !== 'history';
          modeToggle.style.display = 'none';
          taskTitle.textContent = taskNames[task] || 'Göteborg Trafik';
          setClosureTool(null);
          dayPickMode = false;
          if (task === 'history') {
            await loadHistory();
            return;
          } else if (task === 'traffic') {
            setMode(btnHist, histProvider);
          } else if (task === 'forecast') {
            await openForecast();
          } else {
            await openScenario();
            if (task === 'demand') {
              dayPickMode = true;
            }
          }
          refreshCloseUI();
          requestAnimationFrame(() => Render.invalidateSize());
        }

        function showTaskHome() {
          workspaceTask = 'home';
          document.body.dataset.task = 'home';
          taskHome.hidden = false;
          historyPanel.hidden = true;
          setClosureTool(null);
          dayPickMode = false;
          refreshCloseUI();
          requestAnimationFrame(() => Render.invalidateSize());
        }

        function setClosureTool(tool) {
          if (monthlyJobRunning && tool !== 'monthly') return;
          if (suggestJobRunning && tool !== 'suggest') return;
          closureTool = tool;
          if (!monthlyJobRunning && !suggestJobRunning) selected.clear();
          if (tool === 'suggest') suggestResults.classList.remove('show');
          if (tool === 'monthly') monthlyResults.classList.remove('show');
          if (tool) optimizeResults.classList.remove('show');
          refreshCloseUI();
        }

        // Feature-detect the API — the "+ Ny avstängning" / "Byt dag" /
        // "Föreslå tid" entry points are hidden entirely when served
        // statically (no server to simulate with)
        fetch('/api/ping').then(r => {
          apiAvailable = r.ok;
          refreshCloseUI();
        }).catch(() => {
          apiAvailable = false;
          refreshCloseUI();
        });

        btnClose.addEventListener('click', () => setClosureTool('simulate'));
        btnCancel.addEventListener('click', async () => {
          if (!closureJobRunning) return setClosureTool(null);
          btnCancel.disabled = true;
          btnCancel.textContent = 'Avbryter…';
          try {
            const res = await fetch('/api/cancel?kind=close', { method: 'POST' });
            if (!res.ok) throw new Error((await res.json()).error || `HTTP ${res.status}`);
          } catch (e) {
            alert('Kunde inte avbryta simuleringen: ' + e.message);
            btnCancel.disabled = false;
            btnCancel.textContent = 'Avbryt';
          }
        });
        btnSuggest.addEventListener('click', () => setClosureTool('suggest'));
        btnSuggestCancel.addEventListener('click', async () => {
          if (!suggestJobRunning) return setClosureTool(null);
          btnSuggestCancel.disabled = true;
          btnSuggestCancel.textContent = 'Avbryter…';
          try {
            const res = await fetch('/api/cancel?kind=suggest', { method: 'POST' });
            if (!res.ok) throw new Error((await res.json()).error || `HTTP ${res.status}`);
          } catch (e) {
            alert('Kunde inte avbryta sökningen: ' + e.message);
            btnSuggestCancel.disabled = false;
            btnSuggestCancel.textContent = 'Avbryt';
          }
        });
        btnSuggestResultsClose.addEventListener('click', () => {
          suggestResults.classList.remove('show');
        });
        document.getElementById('workspace-menu-btn').addEventListener('click', showTaskHome);
        document.getElementById('history-back').addEventListener('click', showTaskHome);
        document.querySelectorAll('#task-grid [data-task]').forEach(button => {
          button.addEventListener('click', () => openWorkspace(button.dataset.task));
        });

        // ── Optimera signaler (IMPROVEMENT_PLAN.md Phase D5) — async start/poll.
        // The active scenario's full
        // ScenarioSpec tells the server whether D2 or D4 applies; the web app
        // only consumes the uniform summary shape.
        async function pollOptimize(onProgress) {
          for (;;) {
            await new Promise(r => setTimeout(r, 2000));
            let status;
            try {
              status = await (await fetch('/api/optimize_signals/status')).json();
            } catch (e) {
              continue;
            }
            if (status.status === 'running' || status.status === 'cancelling') {
              onProgress?.(status.elapsed_s);
              continue;
            }
            return status;
          }
        }

        function optMetricBox(label, valueHTML, subHTML) {
          const div = document.createElement('div');
          div.className = 'opt-metric';
          div.innerHTML = `<div class="opt-metric-label">${label}</div>` +
                          `<div class="opt-metric-value">${valueHTML}</div>` +
                          (subHTML ? `<div class="opt-metric-sub">${subHTML}</div>` : '');
          return div;
        }

        function renderOptimizeResults(summary) {
          const closureLabel = summary.closure
            ? `avstängning: ${summary.streets.join(', ')}`
            : 'ingen avstängning aktiv';
          optimizeResultsTitle.textContent =
            `Signaloptimering · ${closureLabel} · ${summary.window_start}–` +
            `${summary.window_end} · ${summary.n_junctions} korsningar`;

          // Rendered ALWAYS, not just on a bad result — IMPROVEMENT_PLAN.md D5's own
          // requirement: a signal-provenance label wherever signal results
          // are shown, so nobody reads a percentage without the caveat that
          // produced it.
          optimizeResultsProv.textContent = summary.tls_provenance === 'synthetic'
            ? `⚠ SYNTETISKA ljusprogram (gissade 90 s-cykler, inte Göteborgs ` +
              `verkliga signalplaner) — ${summary.caveat}`
            : `Proveniens: ${summary.tls_provenance} — ${summary.caveat}`;

          optimizeResultsCard.replaceChildren();
          optimizeResultsCard.appendChild(optMetricBox('Före',
            `${Math.round(summary.before.total_time_loss_s / 60)} min`,
            `${summary.before.trip_count} resor` +
            (summary.before_disqualified ? ' · <b>diskvalificerad</b>' : '')));
          const arrow = document.createElement('div');
          arrow.className = 'opt-arrow';
          arrow.textContent = '→';
          optimizeResultsCard.appendChild(arrow);
          optimizeResultsCard.appendChild(optMetricBox('Efter',
            `${Math.round(summary.after.total_time_loss_s / 60)} min`,
            `${summary.after.trip_count} resor` +
            (summary.after_disqualified ? ' · <b>diskvalificerad</b>' : '')));
          const pct = summary.relative_time_loss_pct;
          const deltaGood = pct !== null && pct < 0;
          const deltaDiv = document.createElement('div');
          deltaDiv.className = 'opt-delta';
          deltaDiv.innerHTML = `<div class="opt-metric-label">Förändring</div>` +
            `<div class="opt-delta-value ${deltaGood ? 'good' : 'bad'}">` +
            `${pct === null ? '–' : (pct > 0 ? '+' : '') + pct + '%'}</div>` +
            `<div class="opt-metric-sub">${summary.disqualified
              ? 'diskvalificerad: ' + summary.disqualification_reasons.join(', ')
              : 'ej diskvalificerad'}</div>`;
          optimizeResultsCard.appendChild(deltaDiv);

          const metaLines = [];
          metaLines.push(`Medianförändring cykeltid: ` +
            `${summary.median_cycle_delta_pct === null ? '–' : summary.median_cycle_delta_pct.toFixed(1) + '%'} ` +
            `över ${summary.n_junctions} korsningar (${summary.seeds} seeds).`);
          // Seed/demand-variant-matched (common-random-numbers) comparison,
          // shown alongside the aggregate Δ above so a reader can judge
          // whether that headline number is one outlier seed or a real,
          // consistent effect (found inert — computed but never shown —
          // in review 2026-07-12).
          if (summary.paired_comparison) {
            const p = summary.paired_comparison;
            metaLines.push(`Parad jämförelse (samma seed/variant båda sidor): ` +
              `median Δ ${p.median_time_loss_delta_s >= 0 ? '+' : ''}` +
              `${Math.round(p.median_time_loss_delta_s)}s, ` +
              `std ${Math.round(p.stddev_time_loss_delta_s)}s över ${p.n_pairs} par.` +
              (summary.paired_sign_disagrees
                ? ' <span class="opt-warn">⚠ medianen och den totala ' +
                  'förändringen pekar åt OLIKA håll — lita inte blint på ' +
                  'procentsatsen ovan.</span>'
                : ''));
          }
          if (summary.closure) {
            metaLines.push(`${summary.truncated_vehicles} förkortade, ` +
              `${summary.dropped_vehicles} borttagna fordon (ingen omväg runt avstängningen).`);
            if (summary.route_stability.fraction_identical !== null) {
              metaLines.push(`Ruttstabilitet: ` +
                `${(summary.route_stability.fraction_identical * 100).toFixed(1)}% av ` +
                `${summary.route_stability.n_common_vehicles} jämförbara fordon körde ` +
                `identisk rutt före/efter signalbytet.`);
            }
          }
          if (!summary.recommendation_allowed) {
            metaLines.push('<b>Detta är INTE en rekommendation</b> — signalprogrammen ' +
              'är syntetiska gissningar, inte Göteborgs verkliga ljusplaner.');
          }
          optimizeResultsMeta.innerHTML = metaLines.join('<br>');

          const scheduleByTls = new Map();
          for (const row of summary.tls_timing_schedule ?? []) {
            const rows = scheduleByTls.get(row.tls_id) ?? [];
            rows.push(row);
            scheduleByTls.set(row.tls_id, rows);
          }
          const sorted = [...summary.tls_plan_diff].sort((a, b) =>
            Math.abs(b.cycle_delta_pct ?? 0) - Math.abs(a.cycle_delta_pct ?? 0));
          optimizePlanBody.replaceChildren(...sorted.map(d => {
            const tr = document.createElement('tr');
            const idTd = document.createElement('td');
            idTd.textContent = d.tls_id;
            const cycleTd = document.createElement('td');
            cycleTd.textContent = `${d.cycle_before_s.toFixed(0)}s → ${d.cycle_after_s.toFixed(0)}s`;
            const deltaTd = document.createElement('td');
            deltaTd.textContent = d.cycle_delta_pct === null ? '–' :
              `${d.cycle_delta_pct > 0 ? '+' : ''}${d.cycle_delta_pct.toFixed(1)}%`;
            const offsetTd = document.createElement('td');
            offsetTd.textContent = `${d.offset_before_s.toFixed(0)}s → ${d.offset_after_s.toFixed(0)}s`;
            const splitTd = document.createElement('td');
            splitTd.textContent = d.max_split_change_pct === null ? '–' :
              `${d.max_split_change_pct.toFixed(1)} pp`;
            const timingTd = document.createElement('td');
            timingTd.textContent = (scheduleByTls.get(d.tls_id) ?? []).map(row =>
              `${row.from_edge && row.to_edge ? row.from_edge + '→' + row.to_edge : 'L' + row.link_index}: ` +
              `G ${row.green_s.toFixed(0)}s, R ${row.red_s.toFixed(0)}s, Y ${row.yellow_s.toFixed(0)}s`).join(' | ') || '–';
            tr.append(idTd, cycleTd, deltaTd, offsetTd, splitTd, timingTd);
            return tr;
          }));

          suggestResults.classList.remove('show');
          optimizeResults.classList.add('show');
        }

        btnOptimize.addEventListener('click', async () => {
          const active = scenIndex?.scenarios.find(s => s.file === scenSelect.value);
          const activeClosure = active?.closures?.[0];
          const hhmm = seconds => {
            if (!Number.isFinite(seconds)) return null;
            const minute = Math.floor(seconds / 60) % (24 * 60);
            return String(Math.floor(minute / 60)).padStart(2, '0') + ':' +
              String(minute % 60).padStart(2, '0');
          };
          // A time-windowed closure carries begin_s/end_s in the scenario
          // manifest. Pass that exact window through to signal optimization;
          // the server's 07:00–09:00 default is only for whole-run studies.
          const beginS = Number(activeClosure?.begin_s);
          const endS = Number(activeClosure?.end_s);
          const begin = hhmm(beginS);
          const end = hhmm(endS);
          btnOptimize.disabled = true;
          btnOptimize.textContent = 'Startar…';
          try {
            const inClosureWindow = begin && end && endS > beginS &&
              endS - beginS < 86400;
            const scenario_spec = await signalScenarioSpec(
              active, inClosureWindow ? begin : null, inClosureWindow ? end : null);
            const res  = await fetch('/api/optimize_signals', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ scenario_spec }),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
            btnOptimize.textContent = 'Optimerar… (0s)';
            const status = await pollOptimize(
              s => btnOptimize.textContent = `Optimerar… (${s}s)`);
            if (status.status === 'error') throw new Error(status.error);
            renderOptimizeResults(status.result);
          } catch (e) {
            alert('Signaloptimeringen misslyckades: ' + e.message);
          } finally {
            btnOptimize.disabled = false;
            btnOptimize.textContent = 'Optimera signaler';
          }
        });

        optimizeResultsClose.addEventListener('click', () => {
          optimizeResults.classList.remove('show');
        });

        // "Simulate the future": Historik 2025 calibrates against actual
        // sensor counts; Prognos 2027 calibrates against Agent 1's
        // LightGBM forecast for a chosen 2027 date instead — the date
        // input's range switches to match whichever is selected.
        const sourceBtns = document.querySelectorAll('#source-seg button');
        let currentSource = 'historical';
        const DATE_RANGES = {
          historical: { min: '2025-01-01', max: '2025-12-31', fallback: '2025-09-16' },
          forecast:   { min: '2027-01-01', max: '2027-12-31', fallback: '2027-09-16' },
        };
        sourceBtns.forEach(btn => {
          btn.addEventListener('click', () => {
            currentSource = btn.dataset.source;
            sourceBtns.forEach(b => b.classList.toggle('active', b === btn));
            const r = DATE_RANGES[currentSource];
            dateInput.min = r.min; dateInput.max = r.max;
            dateInput.value = (currentSimSource === currentSource) ? currentSimDate : r.fallback;
          });
        });

        btnDay.addEventListener('click', () => {
          dayPickMode = true;
          currentSource = currentSimSource;
          sourceBtns.forEach(b => b.classList.toggle('active', b.dataset.source === currentSource));
          const r = DATE_RANGES[currentSource];
          dateInput.min = r.min; dateInput.max = r.max;
          dateInput.value = currentSimDate;
          daysInput.value = 1;
          updateDayRunLabel();
          refreshCloseUI();
        });
        btnDayCancel.addEventListener('click', async () => {
          if (!recalibrationJobRunning) {
            dayPickMode = false;
            refreshCloseUI();
            return;
          }
          btnDayCancel.disabled = true;
          btnDayCancel.textContent = 'Avbryter…';
          try {
            const res = await fetch('/api/cancel?kind=recalibrate', { method: 'POST' });
            if (!res.ok) throw new Error((await res.json()).error || `HTTP ${res.status}`);
          } catch (e) {
            alert('Kunde inte avbryta omkalibreringen: ' + e.message);
            btnDayCancel.disabled = false;
            btnDayCancel.textContent = 'Avbryt';
          }
        });

        Render.onEdgeClick(id => {
          if (!closureTool) return;
          if (closureTool === 'monthly' && monthlyJobRunning) return;
          if (closureTool === 'suggest' && suggestJobRunning) return;
          selected.has(id) ? selected.delete(id) : selected.add(id);
          refreshCloseUI();
        });

        // One orchestration function for every road-closing operation. The
        // backend contracts remain deliberately different (ScenarioSpec for
        // a concrete simulation, ScenarioSpec + search inputs for an active
        // day, ClosureSearchSpec for a calendar search), but start/poll/error
        // handling lives here once so the three UI paths cannot drift.
        const ROAD_CLOSURE_OPERATIONS = {
          simulate: {
            startUrl: '/api/close',
            statusUrl: '/api/close/status',
            pollMs: 2000,
          },
          suggest: {
            startUrl: '/api/suggest_closure',
            statusUrl: '/api/suggest_closure/status',
            pollMs: 3000,
          },
          monthly: {
            startUrl: '/api/monthly_search',
            statusUrl: '/api/monthly_search/status',
            pollMs: 4000,
          },
        };

        async function runRoadClosureOperation(kind, requestBody, onProgress) {
          const operation = ROAD_CLOSURE_OPERATIONS[kind];
          if (!operation) throw new Error(`okänd avstängningsåtgärd: ${kind}`);
          if (requestBody) {
            const response = await fetch(operation.startUrl, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(requestBody),
            });
            let data;
            try {
              data = await response.json();
            } catch (_) {
              throw new Error('servern gav ett ofullständigt startsvar');
            }
            if (!response.ok) {
              throw new Error(data.error || `HTTP ${response.status}`);
            }
          }
          for (;;) {
            await new Promise(r => setTimeout(r, operation.pollMs));
            let status;
            try {
              const response = await fetch(operation.statusUrl, {
                cache: 'no-store',
              });
              if (!response.ok) throw new Error(`HTTP ${response.status}`);
              status = await response.json();
            } catch (e) {
              continue;   // transient network hiccup — keep polling
            }
            if (status.status === 'running' || status.status === 'cancelling') {
              onProgress?.(status);
              continue;
            }
            return status;
          }
        }

        // Shared by both the plain closure-picking flow (btnRun below) and
        // "load this suggested window" (suggest-closure results table) —
        // both end at the same place: a freshly built scenario, activated.
        async function activateClosedScenario(status) {
          await loadScenIndex(true);
          scenSelect.value = status.file;
          await activateScenario(status.file, { fresh: true });
        }

        let baselineSpecCache = null;

        async function baseScenarioSpec() {
          if (!baselineSpecCache) {
            const res = await fetch('data/scenarios/baseline.json?t=' + Date.now(),
                                    { cache: 'no-store' });
            if (!res.ok) throw new Error(`baslinje-ScenarioSpec saknas (HTTP ${res.status})`);
            const payload = await res.json();
            if (!payload.scenario_spec) {
              throw new Error('baslinjen saknar ScenarioSpec — bygg om scenarierna först');
            }
            baselineSpecCache = payload.scenario_spec;
          }
          return JSON.parse(JSON.stringify(baselineSpecCache));
        }

        async function closureScenarioSpec(edges, begin = null, end = null) {
          const spec = await baseScenarioSpec();
          const start = begin || spec.start_time;
          const finish = end || spec.end_time;
          const suffix = begin
            ? `_${start.replace(/[^0-9]/g, '')}_${finish.replace(/[^0-9]/g, '')}`
            : '';
          spec.scenario_id = `close_${edges.join('_')}${suffix}`
            .replace(/[^A-Za-z0-9_+-]/g, '_').slice(0, 80);
          spec.closures = edges.map(edge_id => ({
            edge_id,
            start_time: start,
            end_time: finish,
            closure_type: 'full',
            permitted_access_exceptions: [],
          }));
          spec.objective_profile = begin ? 'closure-window' : 'closure';
          return spec;
        }

        async function signalScenarioSpec(active, begin, end) {
          // Signal tools need the exact loaded scenario, not just its edge
          // list. Older manifests may not have embedded the spec, so retain a
          // deterministic compatibility fallback while new scenarios use the
          // canonical manifest copy.
          const spec = active?.scenario_spec
            ? JSON.parse(JSON.stringify(active.scenario_spec))
            : await baseScenarioSpec();
          const day = spec.start_time.slice(0, 10);
          const start = begin || '07:00';
          const finish = end || '09:00';
          if ((!spec.closures || spec.closures.length === 0) &&
              active?.closed_edges?.length) {
            spec.closures = active.closed_edges.map(edge_id => ({
              edge_id,
              start_time: begin ? `${day}T${start}:00` : spec.start_time,
              end_time: end ? `${day}T${finish}:00` : spec.end_time,
              closure_type: 'full',
              permitted_access_exceptions: [],
            }));
          }
          spec.simulation_mode = 'micro';
          spec.objective_profile = spec.closures?.length
            ? 'closure-signal-optimization' : 'signal-optimization';
          spec.analysis_window = {
            warmup_s: 0,
            measure_start: `${day}T${start}:00`,
            measure_end: `${day}T${finish}:00`,
            drain_s: 0,
          };
          return spec;
        }

        async function suggestClosureRequest(edges, durationHours) {
          const scenario_spec = await baseScenarioSpec();
          scenario_spec.objective_profile = 'closure-search';
          // Robust search contract: one matched pilot seed per q50/q10/q90,
          // then four repetitions per variant for the finalist decision.
          // The complete seed/variant identity travels in ScenarioSpec.
          scenario_spec.seed_set = Array.from(
            { length: 12 }, (_, index) => 1000 + index);
          scenario_spec.demand_variant_mapping = Object.fromEntries(
            scenario_spec.seed_set.map((seed, index) => [
              String(seed), ['q50', 'q10', 'q90'][index % 3],
            ]));
          return {
            scenario_spec,
            edges,
            duration_hours: durationHours,
          };
        }

        btnRun.addEventListener('click', async () => {
          closureJobRunning = true;
          btnRun.disabled = true;
          btnCancel.disabled = false;
          btnRun.textContent = 'Startar…';
          showJobProgress('Startar simulering', 0, 90);
          try {
            const scenario_spec = await closureScenarioSpec([...selected]);
            const status = await runRoadClosureOperation(
              'simulate', { scenario_spec }, state => {
              const elapsed = state.elapsed_s || 0;
              btnRun.textContent = state.status === 'cancelling'
                ? 'Avbryter…' : `Simulerar… (${elapsed}s)`;
              showJobProgress('Simulerar avstängning', elapsed, 90);
            });
            if (status.status === 'error') throw new Error(status.error);
            if (status.status === 'cancelled') {
              announceStudyOutcome('Avstängningssimuleringen', 'cancelled',
                                   [...selected].join(', '));
              setClosureTool(null);
              return;
            }
            clearStudyOutcome();
            setClosureTool(null);
            await activateClosedScenario(status);
          } catch (e) {
            announceStudyOutcome('Avstängningssimuleringen', 'error',
                                 [...selected].join(', '), e.message);
          } finally {
            closureJobRunning = false;
            hideRecalibrationProgress();
            btnRun.disabled = false;
            btnRun.textContent = 'Simulera avstängning';
            btnCancel.disabled = false;
            btnCancel.textContent = 'Avbryt';
          }
        });

        // begin_s/end_s are seconds from the search's own epoch_sim — the
        // SAME naive-local-wall-time convention the rest of the app uses
        // for the epoch string (provider.js appends 'Z' to parse it as
        // UTC; formatting back strips it the same way, so the ISO string
        // sent to /api/close means the same wall-clock instant SUMO
        // simulated against).
        function isoFromOffset(epochSim, offsetS) {
          const t = new Date(epochSim.endsWith('Z') ? epochSim : epochSim + 'Z');
          t.setUTCSeconds(t.getUTCSeconds() + offsetS);
          return t.toISOString().slice(0, 19);   // strip the trailing 'Z'
        }

        function fmtHHMM(epochSim, offsetS) {
          const t = new Date(epochSim.endsWith('Z') ? epochSim : epochSim + 'Z');
          t.setUTCSeconds(t.getUTCSeconds() + offsetS);
          return t.toISOString().slice(11, 16);
        }

        function fmtDelta(seconds) {
          const sign = seconds >= 0 ? '+' : '−';
          return `${sign}${Math.round(Math.abs(seconds) / 60)} min`;
        }

        let lastSuggestResult = null;   // holds epoch_sim + edges for the load-row action

        function renderSuggestResults(summary) {
          lastSuggestResult = summary;
          const streets = summary.streets.join(', ');
          suggestResultsTitle.textContent =
            `${streets} · ${summary.duration_hours}h avstängning · ` +
            `${summary.n_simulated} av ${summary.n_candidate_windows} fönster simulerade`;

          const metaLines = [];
          metaLines.push(`Baslinje: <b>${Math.round(summary.baseline_total_time_loss_s / 60)} ` +
                         `min total väntetid</b> över ${summary.baseline_trip_count} resor ` +
                         `(${summary.seeds} seeds).`);
          const decision = summary.robust_decision;
          if (decision?.status === 'unique_winner') {
            const winner = summary.candidates.find(c => c.is_robust_winner);
            if (winner) {
              metaLines.push(
                `<b>Bäst bland SUMO-verifierade finalister: ` +
                `${fmtHHMM(summary.epoch_sim, winner.begin_s)}–` +
                `${fmtHHMM(summary.epoch_sim, winner.end_s)}</b>.`);
            } else {
              metaLines.push(
                `<span id="suggest-results-warn">Vinnarens kördata saknas; ` +
                `resultatet visas inte som en rekommendation.</span>`);
            }
          } else if (decision?.status === 'tie') {
            metaLines.push(
              `<b>Ingen ensam vinnare:</b> ${decision.tie_ids.length} tider är ` +
              `praktiskt likvärdiga.`);
          } else if (decision?.status === 'no_viable') {
            metaLines.push(
              `<span id="suggest-results-warn">Ingen säker genomförbar ` +
              `stängning hittades.</span>`);
          } else if (decision) {
            metaLines.push(
              `<span id="suggest-results-warn">Ingen tydlig vinnare ännu; ` +
              `resultatet är statistiskt osäkert.</span>`);
          } else {
            metaLines.push(
              `<span id="suggest-results-warn">Pilotgrinden kunde inte gå ` +
              `vidare till robusta finalister.</span>`);
          }
          if (!summary.claim_boundary?.global_best_claim_allowed) {
            metaLines.push(
              `<span id="suggest-results-warn">Resultatet gäller de ` +
              `SUMO-verifierade finalisterna; den nya månadsgrinden är ännu ` +
              `inte godkänd för påståendet “globalt bäst”.</span>`);
          }
          const det = summary.detour_availability;
          if (det.score === null) {
            metaLines.push(`<span id="suggest-results-warn">⚠ Vägen har ingen anslutning ` +
                           `alls in/ut från denna riktning — ingen omväg är möjlig.</span>`);
          } else if (det.score < 1) {
            metaLines.push(`<span id="suggest-results-warn">⚠ Endast ${det.reachable_pairs}/` +
                           `${det.total_pairs} möjliga omvägar finns runt denna avstängning — ` +
                           `räkna med kvarlämnade fordon oavsett vald tid.</span>`);
          }
          const corr = summary.validation.correlation;
          if (corr) {
            metaLines.push(corr.spearman_rho > 0.3
              ? `Rangordningen stämmer väl överens med simuleringen ` +
                `(Spearman ρ=${corr.spearman_rho.toFixed(2)}).`
              : `<span id="suggest-results-warn">⚠ Svagt samband mellan rangordning och ` +
                `simulerat resultat (Spearman ρ=${corr.spearman_rho.toFixed(2)}) — ` +
                `lita inte blint på rangordningen här.</span>`);
          } else {
            metaLines.push('För få icke-diskvalificerade fönster för att bedöma rangordningens tillförlitlighet.');
          }
          metaLines.push('Rang = förslagets ranking (INTE en tidsuppskattning); ΔTid är simulerad väntetid jämfört med baslinjen.');
          suggestResultsMeta.innerHTML = metaLines.join('<br>');

          suggestResultsBody.replaceChildren(...summary.candidates.map(c => {
            const tr = document.createElement('tr');
            if (c.disqualified) tr.classList.add('disqualified');
            if (!c.in_proxy_top_k) tr.classList.add('outside-topk');

            const rankTd = document.createElement('td');
            rankTd.textContent = `#${c.proxy_rank + 1}`;
            if (!c.in_proxy_top_k) {
              const tag = document.createElement('span');
              tag.className = 'sr-tag rank';
              tag.textContent = 'kontroll';
              rankTd.appendChild(tag);
            }

            const timeTd = document.createElement('td');
            timeTd.textContent = `${fmtHHMM(summary.epoch_sim, c.begin_s)}–` +
                                 `${fmtHHMM(summary.epoch_sim, c.end_s)}`;

            const deltaTd = document.createElement('td');
            deltaTd.textContent = fmtDelta(c.delta_time_loss_median_s);

            const spanTd = document.createElement('td');
            spanTd.textContent = `${fmtDelta(c.delta_time_loss_min_s)} … ${fmtDelta(c.delta_time_loss_max_s)}`;

            const strandedTd = document.createElement('td');
            strandedTd.textContent = (c.truncated_vehicles + c.dropped_vehicles) > 0
              ? `${c.truncated_vehicles + c.dropped_vehicles}` : '0';

            const queueTd = document.createElement('td');
            queueTd.textContent = c.max_queue_vehicles ?? '–';

            const statusTd = document.createElement('td');
            if (c.disqualified) {
              const tag = document.createElement('span');
              tag.className = 'sr-tag disq';
              tag.textContent = c.disqualification_reasons.join(', ');
              statusTd.appendChild(tag);
            } else if (c.is_robust_winner) {
              statusTd.textContent = 'Bäst';
            } else if (c.is_robust_tie) {
              statusTd.textContent = 'Likvärdig';
            } else if (c.evidence_level === 'robust_finalist') {
              statusTd.textContent = 'Finalist';
            } else {
              statusTd.textContent = 'Pilot';
            }

            const loadTd = document.createElement('td');
            const loadBtn = document.createElement('button');
            loadBtn.className = 'sr-load-btn';
            loadBtn.textContent = 'Ladda';
            loadBtn.disabled = c.disqualified;
            loadBtn.title = c.disqualified
              ? 'Diskvalificerad (fordon strandade/teleporterade) — ej rekommenderad att ladda'
              : 'Bygg detta som ett riktigt scenario på kartan';
            loadBtn.addEventListener('click', () => loadSuggestedWindow(c, loadBtn));
            loadTd.appendChild(loadBtn);

            tr.append(rankTd, timeTd, deltaTd, spanTd, strandedTd, queueTd, statusTd, loadTd);
            return tr;
          }));

          suggestResults.classList.add('show');
        }

        async function loadSuggestedWindow(candidate, btn) {
          const others = suggestResultsBody.querySelectorAll('.sr-load-btn');
          others.forEach(b => b.disabled = true);
          btn.textContent = 'Startar…';
          try {
            const begin = isoFromOffset(lastSuggestResult.epoch_sim, candidate.begin_s);
            const end   = isoFromOffset(lastSuggestResult.epoch_sim, candidate.end_s);
            const scenario_spec = await closureScenarioSpec(
              lastSuggestResult.edges, begin, end);
            const status = await runRoadClosureOperation(
              'simulate', { scenario_spec }, state => {
                btn.textContent = state.status === 'cancelling'
                  ? 'Avbryter…'
                  : `Simulerar… (${state.elapsed_s || 0}s)`;
              });
            if (status.status === 'error') throw new Error(status.error);
            if (status.status === 'cancelled') return;
            suggestResults.classList.remove('show');
            setClosureTool(null);
            await activateClosedScenario(status);
          } catch (e) {
            alert('Kunde inte ladda scenariot: ' + e.message);
          } finally {
            others.forEach(b => b.disabled = false);
            btn.textContent = 'Ladda';
          }
        }

        btnSuggestRun.addEventListener('click', async () => {
          suggestJobRunning = true;
          btnSuggestRun.disabled = true;
          btnSuggestCancel.disabled = false;
          const durationHours = Number(durationInput.value) || 6;
          btnSuggestRun.textContent = 'Startar…';
          try {
            const request = await suggestClosureRequest(
              [...selected], durationHours);
            const status = await runRoadClosureOperation(
              'suggest', request, state => {
                btnSuggestRun.textContent = state.status === 'cancelling'
                  ? 'Avbryter…'
                  : `Söker… (${state.elapsed_s || 0}s)`;
              });
            if (status.status === 'error') throw new Error(status.error);
            if (status.status === 'cancelled') {
              announceStudyOutcome('Tidsoptimeringen', 'cancelled',
                                   [...selected].join(', '));
              suggestJobRunning = false;
              setClosureTool(null);
              return;
            }
            renderSuggestResults(status.result);
          } catch (e) {
            alert('Sökningen misslyckades: ' + e.message);
          } finally {
            suggestJobRunning = false;
            btnSuggestRun.disabled = selected.size === 0;
            btnSuggestCancel.disabled = false;
            btnSuggestCancel.textContent = 'Avbryt';
            btnSuggestRun.textContent = 'Sök bästa tid';
            refreshCloseUI();
          }
        });

        // ── Bästa arbetsperiod (Phase 4 step 7): resumable monthly closure
        // search. The server forces the frozen golden policy and chooses
        // held-out-validated proxy screening or the safe bounded-exhaustive
        // fallback; the browser only supplies user intent (ClosureSearchSpec).
        let monthlySource = 'forecast';
        let lastMonthlyResult = null;
        let lastMonthlySpec = null;

        // Deterministic id from the form content: re-running the exact same
        // search resumes the same immutable workspace (completed SUMO work
        // is loaded, not repeated) instead of starting from scratch.
        function monthlyStableKey(text) {
          let h = 0x811c9dc5;
          for (let i = 0; i < text.length; i++) {
            h ^= text.charCodeAt(i);
            h = Math.imul(h, 0x01000193) >>> 0;
          }
          return h.toString(36);
        }

        function monthlyClosureSearchSpec(edges) {
          const weekdays = [...monthlyWeekdays.querySelectorAll('button.active')]
            .map(btn => Number(btn.dataset.day));
          const body = {
            directed_edges: edges,
            source: monthlySource,
            permitted_date_start: monthlyDateStart.value,
            permitted_date_end: monthlyDateEnd.value,
            required_work_minutes:
              Math.round((Number(monthlyWorkHours.value) || 4) * 60),
            max_consecutive_start_days:
              Math.min(90, Math.max(1, Number(monthlyMaxDays.value) || 1)),
            // "Heldag" gives the true full-day band 00:00–24:00 (the
            // contract accepts 24:00 only as latest_end, which a
            // type="time" input cannot express).
            permitted_daily_band: monthlyFullday.checked
              ? { earliest_start: '00:00', latest_end: '24:00' }
              : {
                  earliest_start: monthlyBandStart.value.slice(0, 5),
                  latest_end: monthlyBandEnd.value.slice(0, 5),
                },
            allowed_weekdays: weekdays,
            same_daily_window: true,
            resolution_minutes: 15,
            closure_type: 'full',
            duration_basis: 'required_work_time',
            work_to_closure_assumption: 'one_to_one',
            objective_profile: monthlyPeriodMode.checked
              ? 'closure_cost_v1' : 'robust_time_loss',
            policy_status: 'user_supplied_unverified',
            // Long searches are evaluated as exact date-specific daily SUMO
            // units. Traffic is reset between work days (the explicit fast
            // approximation), while every day's downloaded demand, road,
            // closure, variant and seed identity remains distinct.
            interday_policy: 'independent_daily_reset_v1',
            // Recurring road work uses the exact same start and end time on
            // every selected workday. Period lengths that cannot divide the
            // requested work into equal 15-minute shifts are not candidates.
            work_allocation_policy: 'exact_equal_daily_v1',
            period_comparison_policy: monthlyPeriodMode.checked
              ? 'rolling_period_v1' : 'none_v1',
          };
          const key = monthlyStableKey(JSON.stringify(body));
          return {
            search_id: `ui-monthly-${key}`,
            demand_build_id: `ui-monthly-release-${key}`,
            ...body,
          };
        }

        function restoreMonthlySearchSpec(spec) {
          if (!spec || typeof spec !== 'object') return;
          lastMonthlySpec = spec;
          monthlySource = spec.source || monthlySource;
          monthlySourceSeg.querySelectorAll('button').forEach(btn =>
            btn.classList.toggle('active', btn.dataset.source === monthlySource));
          monthlyDateStart.value = spec.permitted_date_start || '';
          monthlyDateEnd.value = spec.permitted_date_end || '';
          monthlyWorkHours.value = Number(spec.required_work_minutes || 0) / 60;
          monthlyMaxDays.value = spec.max_consecutive_start_days || 1;
          const band = spec.permitted_daily_band || {};
          const fullDay = band.earliest_start === '00:00' &&
            band.latest_end === '24:00';
          monthlyFullday.checked = fullDay;
          monthlyBandStart.disabled = fullDay;
          monthlyBandEnd.disabled = fullDay;
          if (!fullDay) {
            monthlyBandStart.value = band.earliest_start || '07:00';
            monthlyBandEnd.value = band.latest_end || '18:00';
          }
          monthlyPeriodMode.checked =
            spec.period_comparison_policy === 'rolling_period_v1';
          const allowed = new Set(spec.allowed_weekdays || []);
          monthlyWeekdays.querySelectorAll('button').forEach(btn =>
            btn.classList.toggle('active', allowed.has(Number(btn.dataset.day))));
        }

        const MONTHLY_PHASE_LABELS = {
          policy: 'Startar',
          preflight: 'Mäter sökningens storlek',
          enumerate: 'Räknar upp lagliga scheman',
          screen: 'Väljer kandidater',
          paused_budget: 'Pausad vid resursgränsen',
          cost_units: 'Beräknar dagskostnader',
          cost_parents: 'Summerar kostnad per schema',
          health_scan: 'Kontrollerar körbarhet',
          prepare_backend: 'Bygger kalibrerat underlag per datum (~6 min/datum första gången)',
          pilot: 'Pilotkörningar i SUMO',
          finalists: 'Finalistkörningar i SUMO',
          decide: 'Beslutar',
          adaptive_finalists: 'Fler körningar för precision',
          publish: 'Publicerar resultat',
        };

        // What the long phases are actually doing, when the search says so.
        // A bare X/Y tells the user how far it has come but not what it is
        // spending the time on; a cache hit and a current cutoff do.
        function monthlyProgressDetail(detail) {
          if (!detail || typeof detail !== 'object') return '';
          const parts = [];
          if (Number.isFinite(detail.costed) && Number.isFinite(detail.cost_total)) {
            parts.push(`${detail.costed}/${detail.cost_total} kostnadsberäknade`);
          }
          if (Number.isFinite(detail.cache_hits)) {
            parts.push(`${detail.cache_hits} cacheträffar`);
          }
          if (Number.isFinite(detail.verified) && Number.isFinite(detail.total)) {
            parts.push(`${detail.verified}/${detail.total} SUMO-verifierade`);
          }
          if (Number.isFinite(detail.cutoff)) {
            parts.push(`gräns ${detail.cutoff.toFixed(2)} fordonstimmar`);
          }
          if (Number.isFinite(detail.parent_schedules) && !parts.length) {
            parts.push(`${detail.parent_schedules} scheman`);
          }
          if (Number.isFinite(detail.daily_units)) {
            parts.push(`${detail.daily_units} unika dagsenheter totalt`);
          }
          if (Number.isFinite(detail.leg_daily_units)) {
            parts.push(`${detail.leg_daily_units} i senaste körningen`);
          }
          return parts.length ? ` · ${parts.join(' · ')}` : '';
        }

        function updateMonthlyProgress(status) {
          const progress = status.progress || {};
          const label = MONTHLY_PHASE_LABELS[progress.phase] || 'Söker';
          const counts = progress.total
            ? ` ${progress.completed}/${progress.total}` : '';
          const detail = monthlyProgressDetail(progress.detail);
          btnMonthlyRun.textContent = `Söker… (${status.elapsed_s || 0}s)`;
          monthlyProgress.hidden = false;
          monthlyProgress.textContent = status.status === 'cancelling'
            ? 'Avbryter…' : `${label}${counts}${detail}`;
        }

        function scheduleLabel(schedule) {
          const intervals = schedule.intervals || [];
          const parts = intervals.slice(0, 2).map(iv =>
            `${iv.start_time.slice(0, 10)} ${iv.start_time.slice(11, 16)}–` +
            `${iv.end_time.slice(11, 16)}`);
          return intervals.length > 2
            ? `${parts[0]} … (${intervals.length} dagar)` : parts.join(', ');
        }

        function renderMonthlyResults(result) {
          lastMonthlyResult = result;
          lastMonthlySpec = result.closure_search_spec || lastMonthlySpec;
          const screening = result.screening || {};
          const periodComparison = result.period_comparison;
          monthlyResultsTitle.textContent =
            periodComparison
              ? `Bästa period · ${periodComparison.start_date_count} startdatum · ` +
                `${periodComparison.evaluated_count} SUMO-verifierade scheman`
              : `Bästa arbetsperiod · ${screening.candidate_count ?? '?'} lagliga ` +
                `scheman · ${screening.shortlist_count ?? '?'} SUMO-verifierade`;

          const metaLines = [];
          const boundary = result.claim_boundary || {};
          const winner = (result.selected_schedules || [])
            .find(s => s.schedule_id === result.winner_id);
          if (periodComparison) {
            monthlyColPeriod.textContent = 'Period';
            monthlyColPrimary.textContent = 'Ford.timmar (värsta variant)';
            monthlyColSecondary.textContent = 'Extra km';
          } else {
            monthlyColPeriod.textContent = 'Schema';
            monthlyColPrimary.textContent = 'ΔTid p50 (värsta variant)';
            monthlyColSecondary.textContent = 'Övre 95 %';
          }
          if (!boundary.ui_exposure_allowed) {
            metaLines.push('<span class="monthly-warn">Resultatet får inte visas ' +
              'som rekommendation: ' + (boundary.reason || 'okänd orsak') + '</span>');
          } else if (result.status === 'unique_winner' && winner) {
            metaLines.push(`<b>Bäst bland SUMO-verifierade scheman inom angivna ` +
              `tider: ${scheduleLabel(winner)}</b>`);
          } else if (result.status === 'tie') {
            metaLines.push(`<b>Ingen ensam vinnare:</b> ${result.tie_ids.length} ` +
              `scheman är praktiskt likvärdiga.`);
          } else if (result.status === 'no_viable') {
            metaLines.push('<span class="monthly-warn">Ingen genomförbar ' +
              'arbetsperiod hittades — alla kandidater föll på hårda grindar ' +
              '(omväg, strandade fordon, simuleringshälsa).</span>');
          } else {
            metaLines.push('<span class="monthly-warn">Inget säkert svar — ' +
              'resultatet är statistiskt oavgjort eller pilotgrinden kunde ' +
              'inte gå vidare.</span>');
          }
          metaLines.push(periodComparison
            ? 'Alla rullande perioder jämförs med samma closure-cost-mått: värsta ' +
              'tillagda fordonstimmar över q10/q50/q90, sedan extra sträcka ' +
              'och antal berörda fordon. Optimeringen väljer samma dagliga ' +
              'start/slut i 15-minuterssteg inom det tillåtna tidsfönstret. ' +
              'Perioder får korsa vecko- och månadsgränser.'
            : 'Varje kandidat körs parat mot sin egen baslinje i SUMO över ' +
              'q10/q50/q90-varianterna.');
          if (periodComparison) {
            metaLines.push('Samma tider varje dag: en period tas med när den ' +
              'totala arbetstiden kan delas exakt i lika långa 15-minuterspass ' +
              'per arbetsdag. Maxgränsen är 90 arbetsdagar.');
          }
          if (result.policy_status === 'provisional') {
            metaLines.push('<span class="monthly-warn">Periodanalysen använder ' +
              'den preliminära v2-policyn. Resultatet är beslutsstöd, inte en ' +
              'releasegodkänd global rekommendation.</span>');
          }
          if (periodComparison && !periodComparison.comparison_complete) {
            metaLines.push('<span class="monthly-warn">Periodjämförelsen är ' +
              'inte komplett; minst ett schema saknar tillgängligt eller ' +
              'färdigt underlag.</span>');
          }
          if (!boundary.global_best_claim_allowed) {
            metaLines.push('<span class="monthly-warn">Gäller de uppräknade ' +
              'kandidaterna — grinden för påståendet “globalt bäst över hela ' +
              'sökintervallet” är ännu inte godkänd.</span>');
          }
          monthlyResultsMeta.innerHTML = metaLines.join('<br>');

          const statsById = Object.fromEntries(
            ((result.robust_decision || {}).candidates || [])
              .map(c => [c.candidate_id, c]));
          const tieIds = new Set(result.tie_ids || []);
          const scheduleRows = () => (result.shortlisted_schedules || []).map(schedule => {
              const stats = statsById[schedule.schedule_id];
              const tr = document.createElement('tr');
              const failed = stats?.hard_failures?.length;
              if (failed) tr.classList.add('disqualified');

              const schedTd = document.createElement('td');
              schedTd.textContent = scheduleLabel(schedule);

              const deltaTd = document.createElement('td');
              deltaTd.textContent = stats?.robust_point_s != null
                ? fmtDelta(stats.robust_point_s) : '–';

              const upperTd = document.createElement('td');
              upperTd.textContent = stats?.robust_upper_95_s != null
                ? fmtDelta(stats.robust_upper_95_s) : '–';

              const statusTd = document.createElement('td');
              if (failed) {
                const tag = document.createElement('span');
                tag.className = 'sr-tag disq';
                tag.textContent = stats.hard_failures.join(', ');
                statusTd.appendChild(tag);
              } else if (schedule.schedule_id === result.winner_id) {
                statusTd.textContent = 'Bäst';
              } else if (tieIds.has(schedule.schedule_id)) {
                statusTd.textContent = 'Likvärdig';
              } else if (stats) {
                statusTd.textContent = 'Finalist';
              } else {
                statusTd.textContent = 'Utsållad i pilot';
              }

              const loadTd = document.createElement('td');
              const loadBtn = document.createElement('button');
              loadBtn.className = 'sr-load-btn';
              loadBtn.textContent = 'Ladda';
              loadBtn.disabled = Boolean(failed) ||
                !boundary.ui_exposure_allowed;
              loadBtn.title = failed
                ? 'Föll på hårda grindar — ej lämplig att ladda'
                : 'Bygg detta schema som ett riktigt scenario på kartan';
              loadBtn.addEventListener('click',
                () => loadMonthlySchedule(schedule, loadBtn));
              loadTd.appendChild(loadBtn);

              tr.append(schedTd, deltaTd, upperTd, statusTd, loadTd);
              return tr;
            });
          const periodRows = () => (periodComparison?.periods || []).map(period => {
            const tr = document.createElement('tr');
            if (period.status === 'no_viable') tr.classList.add('disqualified');
            const periodTd = document.createElement('td');
            periodTd.textContent = period.best_schedule
              ? scheduleLabel(period.best_schedule)
              : `Start ${period.start_date}`;
            const hoursTd = document.createElement('td');
            hoursTd.textContent = period.best_cost
              ? Number(period.best_cost.added_vehicle_hours).toFixed(4) : '–';
            const distanceTd = document.createElement('td');
            distanceTd.textContent = period.best_cost
              ? (Number(period.best_cost.added_metres_total) / 1000).toFixed(1) : '–';
            const statusTd = document.createElement('td');
            statusTd.textContent = period.status === 'best_period' ? 'Bästa perioden'
              : period.status === 'tied_best_period' ? 'Likvärdig bäst'
              : period.status === 'viable' ? `${period.viable_count} giltiga`
              : period.status === 'no_viable' ? 'Ingen giltig'
              : 'Ofullständig';
            const loadTd = document.createElement('td');
            const loadBtn = document.createElement('button');
            loadBtn.className = 'sr-load-btn';
            loadBtn.textContent = 'Ladda';
            loadBtn.disabled = !period.best_schedule ||
              !boundary.ui_exposure_allowed;
            if (period.best_schedule) {
              loadBtn.title = `Ladda periodens bästa: ${scheduleLabel(period.best_schedule)}`;
              loadBtn.addEventListener('click', () =>
                loadMonthlySchedule(period.best_schedule, loadBtn));
            }
            loadTd.appendChild(loadBtn);
            tr.append(periodTd, hoursTd, distanceTd, statusTd, loadTd);
            return tr;
          });
          monthlyResultsBody.replaceChildren(
            ...(periodComparison ? periodRows() : scheduleRows()));
          monthlyResults.classList.add('show');
        }

        // Exact-schedule handoff: the loaded scenario is built from the
        // schedule's OWN intervals, never re-derived. If the live demand
        // does not cover the schedule's dates it is recalibrated first via
        // the ordinary "Byt dag" pipeline (the honest ~6 min/day cost),
        // then the closure runs as a normal windowed scenario.
        async function loadMonthlySchedule(schedule, btn) {
          const edges = (lastMonthlySpec &&
                         lastMonthlySpec.directed_edges) ||
                        lastMonthlyResult?.edges || [...selected];
          const intervals = schedule.intervals || [];
          if (!edges.length || !intervals.length) {
            return alert('Schemat saknar kanter eller intervall.');
          }
          const source = lastMonthlySpec?.source || monthlySource;
          const firstDate = intervals[0].start_time.slice(0, 10);
          const lastDate = intervals[intervals.length - 1]
            .start_time.slice(0, 10);
          const spanDays = Math.round(
            (Date.parse(lastDate) - Date.parse(firstDate)) / 86400000) + 1;
          // Interactive load recalibrates via the standard demand path,
          // which is capped at 7 days. A longer winning schedule is still a
          // valid RECOMMENDATION — it just can't be played back as an
          // interactive scenario yet (that would need a multi-week demand
          // build, the same cost the search already paid).
          if (spanDays > 7) {
            alert(`Schemat är ${spanDays} dagar långt. Sökresultatet gäller, `
              + 'men perioder över 7 dagar kan ännu inte laddas som ett '
              + 'interaktivt scenario på kartan (kräver ett fleraveckors '
              + 'demand-bygge). Använd de exakta datumen/tiderna direkt.');
            return;
          }
          const days = Math.min(7, Math.max(1, spanDays));
          const others = monthlyResultsBody.querySelectorAll('.sr-load-btn');
          others.forEach(b => b.disabled = true);
          try {
            const base = await baseScenarioSpec();
            const covered = currentSimSource === source &&
              base.start_time <= intervals[0].start_time &&
              base.end_time >= intervals[intervals.length - 1].end_time;
            if (!covered) {
              if (!confirm(`Kartan är kalibrerad för ${currentSimDate} ` +
                  `(${currentSimSource === 'forecast' ? 'prognos' : 'historik'}). ` +
                  `Kalibrera om för ${firstDate} (${days} dag(ar), ` +
                  `~${estimatedMinutes(days)} min) och ladda schemat?`)) return;
              btn.textContent = 'Kalibrerar…';
              rememberPendingRecal(firstDate, source, days);
              await requestRecalibration(firstDate, source, days, {
                start_date: firstDate, source, days,
                begin: '00:00', end: '24:00',
                structural_reference_date: '2025-09-16',
              });
              recalibrationJobRunning = true;
              const recalibration = await pollRecalibration();
              recalibrationJobRunning = false;
              if (recalibration.status !== 'done') {
                throw new Error(
                  recalibration.error ||
                  'omkalibreringen slutfördes inte; avstängningen startades inte');
              }
            }
            const spec = await baseScenarioSpec();
            spec.scenario_id = (`close_${edges.join('_')}` +
              `_p${monthlyStableKey(JSON.stringify(intervals))}`)
              .replace(/[^A-Za-z0-9_+-]/g, '_').slice(0, 80);
            spec.closures = intervals.flatMap(iv => edges.map(edge_id => ({
              edge_id,
              start_time: iv.start_time,
              end_time: iv.end_time,
              closure_type: 'full',
              permitted_access_exceptions: [],
            })));
            spec.objective_profile = 'closure-window';
            btn.textContent = 'Simulerar… (0s)';
            const status = await runRoadClosureOperation(
              'simulate', { scenario_spec: spec }, state => {
                btn.textContent = state.status === 'cancelling'
                  ? 'Avbryter…'
                  : `Simulerar… (${state.elapsed_s || 0}s)`;
              });
            if (status.status === 'error') throw new Error(status.error);
            if (status.status === 'cancelled') return;
            monthlyResults.classList.remove('show');
            setClosureTool(null);
            await activateClosedScenario(status);
          } catch (e) {
            alert('Kunde inte ladda schemat: ' + e.message);
          } finally {
            others.forEach(b => b.disabled = false);
            btn.textContent = 'Ladda';
          }
        }

        btnMonthly.addEventListener('click', () => setClosureTool('monthly'));
        btnMonthlyResultsClose.addEventListener('click',
          () => monthlyResults.classList.remove('show'));
        // Full-day band overrides (and visibly disables) the time inputs.
        monthlyFullday.addEventListener('change', () => {
          monthlyBandStart.disabled = monthlyFullday.checked;
          monthlyBandEnd.disabled = monthlyFullday.checked;
        });
        monthlyWeekdays.querySelectorAll('button').forEach(btn =>
          btn.addEventListener('click', () => btn.classList.toggle('active')));
        monthlySourceSeg.querySelectorAll('button').forEach(btn =>
          btn.addEventListener('click', () => {
            monthlySource = btn.dataset.source;
            monthlySourceSeg.querySelectorAll('button').forEach(b =>
              b.classList.toggle('active', b === btn));
            const range = DATE_RANGES[monthlySource];
            for (const input of [monthlyDateStart, monthlyDateEnd]) {
              input.min = range.min;
              input.max = range.max;
              if (input.value) {
                input.value = `${range.min.slice(0, 4)}${input.value.slice(4)}`;
              }
            }
          }));

        // Catch the invalid combinations the contract would reject, but with
        // a plain Swedish message and no server round-trip to a raw error.
        function monthlyBandMinutes(hhmm) {
          const [h, m] = hhmm.split(':').map(Number);
          return h * 60 + m;
        }
        function validateMonthlySpec(spec) {
          if (!spec.allowed_weekdays.length) return 'Välj minst en veckodag.';
          if (!spec.permitted_date_start || !spec.permitted_date_end) {
            return 'Välj ett datumintervall.';
          }
          if (spec.permitted_date_start > spec.permitted_date_end) {
            return 'Första datumet måste vara före det sista.';
          }
          const band = spec.permitted_daily_band;
          const start = monthlyBandMinutes(band.earliest_start);
          const end = monthlyBandMinutes(band.latest_end);
          if (end <= start) {
            return 'Tidsbandet måste ha en positiv längd — sätt sluttiden '
              + 'efter starttiden, eller kryssa i Heldag.';
          }
          // Work distributes across up to max_consecutive_start_days days,
          // each day's closure bounded by the band — so the necessary
          // condition is total work ≤ days × band length.
          const capacity = (end - start) * spec.max_consecutive_start_days;
          if (spec.required_work_minutes > capacity) {
            return `Arbetstiden (${spec.required_work_minutes / 60} h) ryms `
              + `inte i ${spec.max_consecutive_start_days} dag(ar) × `
              + `${(end - start) / 60} h tidsband. Vidga bandet (eller Heldag), `
              + 'öka dagar i följd, eller minska arbetstiden.';
          }
          return null;
        }

        // PR B: the EXACT size of the search, read-only, before anything
        // starts. Replaces a range/day-count heuristic that could only guess.
        // A refusal here costs nothing and leaves every input editable; the
        // old behaviour was to discover the 10 000-unit cap after the
        // enumeration had already run.
        async function monthlyPreflightReport(spec) {
          const res = await fetch('/api/monthly_search/preflight', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ closure_search_spec: spec }),
          });
          const body = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
          return body;
        }

        function renderMonthlyPreflight(report) {
          const days = (report.valid_workday_counts || []);
          const span = days.length
            ? (days.length === 1 ? `${days[0]} arbetsdagar`
                                 : `${days[0]}–${days[days.length - 1]} arbetsdagar`)
            : 'inga giltiga dagantal';
          const parts = [
            `${report.parent_schedule_count} perioder (${span})`,
            `${report.unique_daily_unit_count} unika dagsenheter`,
          ];
          if (report.units_outside_demand_year) {
            parts.push(`${report.units_outside_demand_year} utanför demandåret`);
          }
          monthlyPreflight.className = report.size_class || '';
          monthlyPreflight.textContent = parts.join(' · ');
          monthlyPreflight.hidden = false;
        }

        btnMonthlyRun.addEventListener('click', async () => {
          const spec = monthlyClosureSearchSpec([...selected]);
          const problem = validateMonthlySpec(spec);
          if (problem) return alert(problem);

          let report;
          btnMonthlyRun.disabled = true;
          btnMonthlyRun.textContent = 'Beräknar omfattning…';
          try {
            report = await monthlyPreflightReport(spec);
          } catch (e) {
            monthlyPreflight.hidden = true;
            alert('Omfattningen kunde inte beräknas: ' + e.message);
            return;
          } finally {
            btnMonthlyRun.textContent = 'Sök arbetsperiod';
            refreshCloseUI();
          }
          renderMonthlyPreflight(report);

          if (report.size_class === 'over_resource_budget') {
            // Refused BEFORE any job exists, and the inputs stay as they are so
            // the user can shorten the range, change the requested work time or
            // lower the workday cap and ask again.
            alert('Sökningen är för stor för den nuvarande resursgränsen:\n\n'
              + (report.limit_reasons || []).join('\n')
              + '\n\nMinska datumintervallet, ändra antal arbetstimmar eller '
              + 'sänk taket för antal arbetsdagar och försök igen.');
            return;
          }
          if (report.parent_schedule_count === 0) {
            alert('Inget schema uppfyller villkoren. Kontrollera tillåtet '
              + 'tidsfönster, arbetstid och antal arbetsdagar.');
            return;
          }
          if (report.size_class === 'large_but_runnable') {
            if (!confirm(
                `Stor men körbar sökning: ${report.parent_schedule_count} `
                + `perioder och ${report.unique_daily_unit_count} unika `
                + 'dagsenheter. Sökningen använder datumriktig trafik och '
                + 'återanvänder identiska dagsresultat, men en helt ny period '
                + 'kan ta tid att köra i SUMO. Trafikläget återställs mellan '
                + 'arbetsdagar enligt det valda snabbläget. Jobbet är '
                + 'återupptagbart — du kan stänga fliken och starta samma '
                + 'sökning igen. Fortsätta?')) return;
          }
          lastMonthlySpec = spec;
          monthlyJobRunning = true;
          refreshCloseUI();
          btnMonthlyRun.disabled = true;
          btnMonthlyRun.textContent = 'Startar…';
          try {
            const status = await runRoadClosureOperation(
              'monthly', { closure_search_spec: spec }, updateMonthlyProgress);
            if (status.status === 'error') throw new Error(status.error);
            if (status.status === 'cancelled') {
              monthlyProgress.hidden = false;
              monthlyProgress.textContent =
                status.note || 'Avbruten — sökningen kan återupptas.';
              return;
            }
            if (status.status === 'paused') {
              monthlyProgress.hidden = false;
              monthlyProgress.textContent = status.note ||
                'Pausad vid resursgränsen — starta samma sökning igen för att fortsätta.';
              return;
            }
            monthlyProgress.hidden = true;
            renderMonthlyResults(status.result);
          } catch (e) {
            alert('Sökningen misslyckades: ' + e.message);
          } finally {
            monthlyJobRunning = false;
            btnMonthlyRun.textContent = 'Sök arbetsperiod';
            refreshCloseUI();
          }
        });

        btnMonthlyCancel.addEventListener('click', async () => {
          if (!monthlyJobRunning) return setClosureTool(null);
          btnMonthlyCancel.disabled = true;
          btnMonthlyCancel.textContent = 'Avbryter…';
          try {
            const res = await fetch('/api/cancel?kind=monthly', { method: 'POST' });
            if (!res.ok) throw new Error((await res.json()).error || `HTTP ${res.status}`);
          } catch (e) {
            alert('Kunde inte avbryta sökningen: ' + e.message);
          } finally {
            btnMonthlyCancel.disabled = false;
            btnMonthlyCancel.textContent = 'Avbryt';
          }
        });

        // Recover whichever road-closing operation owns the shared SUMO
        // resource. This is intentionally one path for simulate/suggest/
        // monthly: after a reload or closed tab, the active job reopens the
        // unified Vägavstängning workspace and keeps polling its own status.
        // Finished historical jobs are left in Körhistorik; only genuinely
        // running/cancelling server state takes over a fresh page.
        (async () => {
          try {
            const states = await Promise.all(
              Object.entries(ROAD_CLOSURE_OPERATIONS).map(
                async ([kind, operation]) => {
                  const response = await fetch(operation.statusUrl, {
                    cache: 'no-store',
                  });
                  if (!response.ok) throw new Error(`HTTP ${response.status}`);
                  return [kind, await response.json()];
                }));
            const active = states.find(([, state]) =>
              state.status === 'running' || state.status === 'cancelling');
            if (!active) {
              const paused = states.find(([kind, state]) =>
                kind === 'monthly' && state.status === 'paused');
              if (!paused) return;
              const [, state] = paused;
              await openWorkspace('closure');
              setClosureTool('monthly');
              restoreMonthlySearchSpec(state.closure_search_spec);
              selected.clear();
              for (const edge of state.edges ||
                   state.closure_search_spec?.directed_edges || []) {
                selected.add(edge);
              }
              monthlyProgress.hidden = false;
              monthlyProgress.textContent = state.note ||
                'Pausad — starta samma sökning igen för att fortsätta.';
              refreshCloseUI();
              return;
            }

            const [kind, state] = active;
            await openWorkspace('closure');
            setClosureTool(kind);
            const spec = state.closure_search_spec;
            if (kind === 'monthly') restoreMonthlySearchSpec(spec);
            const edges = state.edges || spec?.directed_edges || [];
            for (const edge of edges) selected.add(edge);
            closureJobRunning = kind === 'simulate';
            suggestJobRunning = kind === 'suggest';
            monthlyJobRunning = kind === 'monthly';
            refreshCloseUI();

            const onProgress = current => {
              if (kind === 'simulate') {
                btnRun.textContent = current.status === 'cancelling'
                  ? 'Avbryter…'
                  : `Simulerar… (${current.elapsed_s || 0}s)`;
              } else if (kind === 'suggest') {
                btnSuggestRun.textContent = current.status === 'cancelling'
                  ? 'Avbryter…'
                  : `Söker… (${current.elapsed_s || 0}s)`;
              } else {
                updateMonthlyProgress(current);
              }
            };
            const done = await runRoadClosureOperation(kind, null, onProgress);
            if (done.status === 'done') {
              clearStudyOutcome();
              if (kind === 'simulate') {
                closureJobRunning = false;
                setClosureTool(null);
                await activateClosedScenario(done);
              } else if (kind === 'suggest') {
                renderSuggestResults(done.result);
              } else {
                lastMonthlySpec = done.closure_search_spec || lastMonthlySpec;
                monthlyProgress.hidden = true;
                renderMonthlyResults(done.result);
              }
            } else if (done.status === 'cancelled' || done.status === 'paused') {
              announceStudyOutcome('Avstängningsjobbet', 'cancelled',
                                   edges.join(', '));
              if (kind !== 'monthly') {
                closureJobRunning = false;
                suggestJobRunning = false;
                setClosureTool(null);
              } else {
                monthlyProgress.hidden = false;
                monthlyProgress.textContent =
                  done.note || (done.status === 'paused'
                    ? 'Pausad — starta samma sökning igen för att fortsätta.'
                    : 'Avbruten — sökningen kan återupptas.');
              }
            } else if (done.status === 'error') {
              announceStudyOutcome('Avstängningsjobbet', 'error',
                                   edges.join(', '), done.error);
            }
            closureJobRunning = false;
            suggestJobRunning = false;
            monthlyJobRunning = false;
            btnRun.textContent = 'Simulera avstängning';
            btnSuggestRun.textContent = 'Sök bästa tid';
            btnMonthlyRun.textContent = 'Sök arbetsperiod';
            refreshCloseUI();
          } catch (e) {
            // Static hosting has no status API; a malformed live response
            // must likewise release the local UI state instead of leaving
            // every road-closing control permanently disabled.
            closureJobRunning = false;
            suggestJobRunning = false;
            monthlyJobRunning = false;
            refreshCloseUI();
          }
        })();

        // The recalibration takes ~6 min — a single held-open fetch for
        // that long is fragile (browser timeout, closed tab, sleeping
        // laptop, dropped wifi all abandon the CLIENT while the SERVER
        // keeps computing regardless). This is what actually happened
        // during testing: a job outlived the request that started it and
        // silently finished ~10 min after the tab had given up. Poll a
        // status endpoint instead, so the job's lifetime is independent of
        // any one connection, and a fresh page load can discover an
        // already-running (or already-finished) job rather than assuming
        // idle.
        async function applyFinishedRecalibration(data) {
          forgetPendingRecal();
          baselineSpecCache = null;
          currentSimDate = data.date;
          currentSimSource = data.source;
          const srcLabel = data.source === 'forecast' ? 'Prognos' : 'Historik';
          const days = data.days || 1;
          simDayHint.textContent = days > 1
            ? `${srcLabel}: ${data.date} (${days} dagar)`
            : `${srcLabel}: ${data.date}`;
          await loadScenIndex(true);
          scenSelect.value = data.file;
          await activateScenario(data.file, { fresh: true });
        }

        async function pollRecalibration() {
          for (;;) {
            await new Promise(r => setTimeout(r, 4000));
            let status;
            try {
              status = await (await fetch('/api/recalibrate/status')).json();
            } catch (e) {
              continue;   // transient network hiccup — keep polling
            }
            if (status.status === 'running' || status.status === 'cancelling') {
              btnDayRun.textContent = `Kalibrerar om… (${status.elapsed_s}s)`;
              showRecalibrationProgress(status.elapsed_s || 0);
              if (status.status === 'cancelling') {
                recalProgressLabel.textContent = 'Avbryter simulering…';
              }
              continue;
            }
            hideRecalibrationProgress();
            const attempted = `${status.date || 'okänt datum'}`
              + (status.source ? ` (${status.source})` : '');
            if (status.status === 'error') {
              forgetPendingRecal();
              announceStudyOutcome('Omkalibreringen', 'error', attempted,
                                   status.error);
            } else if (status.status === 'cancelled') {
              forgetPendingRecal();
              announceStudyOutcome('Omkalibreringen', 'cancelled', attempted);
            } else if (status.status === 'done') {
              clearStudyOutcome();
              await applyFinishedRecalibration(status);
            }
            return status;
          }
        }

        async function recoverStartedRecalibration(date, source, days) {
          // A dev tunnel or sleeping browser can drop the small 202 response
          // after the server has already accepted the background job. Status
          // is authoritative, so do not show a false start failure in that
          // case.
          try {
            const response = await fetch('/api/recalibrate/status', { cache: 'no-store' });
            const status = await response.json();
            const sameJob = status.date === date && status.source === source &&
              Number(status.days || 1) === days;
            if (!sameJob || !['running', 'done'].includes(status.status)) return false;
            dayPickMode = false;
            refreshCloseUI();
            if (status.status === 'done') {
              await applyFinishedRecalibration(status);
            } else {
              recalibrationJobRunning = true;
              btnDayRun.textContent = `Kalibrerar om… (${status.elapsed_s || 0}s)`;
              showRecalibrationProgress(status.elapsed_s || 0);
              await pollRecalibration();
            }
            return true;
          } catch (_) {
            return false;
          }
        }

        btnDayRun.addEventListener('click', async () => {
          recalibrationJobRunning = true;
          btnDayRun.disabled = true;
          btnDayCancel.disabled = false;
          btnDayCancel.textContent = 'Avbryt';
          const days = Number(daysInput.value) || 1;
          const source = currentSource;
          const date = selectedDemandDate(source);
          btnDayRun.textContent = 'Startar…';
          showRecalibrationProgress(0);
          rememberPendingRecal(date, source, days);
          try {
            // Send one explicit demand contract. The server still accepts the
            // legacy query form, but the structured body is the authoritative
            // identity archived and fingerprinted by the build pipeline.
            const demand_spec = {
              start_date: date,
              source,
              days,
              begin: '00:00',
              end: '24:00',
              structural_reference_date: '2025-09-16'
            };
            const data = await requestRecalibration(date, source, days, demand_spec);
            dayPickMode = false;
            refreshCloseUI();
            btnDayRun.textContent = 'Kalibrerar om… (0s)';
            await pollRecalibration();
          } catch (e) {
            if (await recoverStartedRecalibration(date, source, days)) return;
            alert('Kunde inte starta omkalibreringen: ' + e.message);
          } finally {
            recalibrationJobRunning = false;
            hideRecalibrationProgress();
            recalibrationJobRunning = false;
            btnDayRun.disabled = false;
            btnDayCancel.disabled = false;
            btnDayCancel.textContent = 'Avbryt';
            updateDayRunLabel();
          }
        });

        simDayHint.textContent = `Historik: ${currentSimDate}`;

        // A job started from THIS tab, another tab, or a session that has
        // since closed is still visible here — check once on load instead
        // of assuming idle (this is exactly the gap that caused the site
        // to silently end up on a forecast date nobody saw finish).
        //
        // A currently-RUNNING job is genuinely shared, current server
        // state — any visitor benefits from being told the server is busy
        // recalibrating right now, so that branch is unconditional. A
        // job that already finished is different: /api/recalibrate/status
        // stays "done" in server memory indefinitely (it's the last
        // completed job, not "yours"), so applying it unconditionally
        // switches EVERY future fresh page load into whatever scenario
        // that old job produced — pendingRecalMatches() (sessionStorage,
        // set when THIS tab itself started a recalibration) scopes the
        // recovery to a tab that actually has a reason to expect it,
        // fixing a real bug found via browser testing 2026-07-12: a
        // completely fresh page load with no query params landed in
        // Simulering mode on a stale 2027 forecast scenario.
        (async () => {
          try {
            const status = await (await fetch('/api/recalibrate/status')).json();
            if (status.status === 'running') {
              dayPickMode = true;
              recalibrationJobRunning = true;
              refreshCloseUI();
              btnDayRun.disabled = true;
              btnDayCancel.disabled = false;
              btnDayRun.textContent = `Kalibrerar om… (${status.elapsed_s}s)`;
              await pollRecalibration();
              btnDayRun.disabled = false;
              btnDayCancel.disabled = false;
              btnDayRun.textContent = 'Räkna om (~6 min)';
            } else if (status.status === 'done' && status.date !== currentSimDate &&
                      pendingRecalMatches(status)) {
              await applyFinishedRecalibration(status);
            }
          } catch (e) { /* serve.py not running (static hosting) — ignore */ }
        })();

        // Deep links must dismiss the workspace landing page, not just
        // switch the provider underneath it — openWorkspace() is what the
        // task cards themselves run (regression found 2026-07-17:
        // ?mode=scenario left "Välj en arbetsyta" covering the map).
        if (params.get('mode') === 'scenario') await openWorkspace('scenario');
        else if (params.get('mode') === 'forecast') await openWorkspace('forecast');

      } catch (err) {
        // Safe DOM, not innerHTML (J; audit P1-6): err.message can echo
        // attacker-controlled URL parts (?file=... flows into fetch error
        // text) — a reflected-XSS vector if interpolated as HTML. The CSP
        // would block the payload's execution; this removes the injection
        // itself.
        const box = document.createElement('div');
        box.style.cssText = 'padding:40px;color:#f87171;'
          + 'font-family:monospace;white-space:pre-wrap';
        const b = document.createElement('b');
        b.textContent = 'Fel:';
        box.append(b, '\n' + err.message + '\n\nKör pipeline och starta:\n'
          + '  python3 build_data.py --data_dir ... --coords ...\n'
          + '  python3 build_features.py\n'
          + '  python3 build_agent1_flows.py\n'
          + '  cd web && python3 -m http.server 8000');
        document.body.replaceChildren(box);
        console.error(err);
      }
    })();
