// HistoricalProvider — the only implementation of the flowAt seam today.
// Later, ModelProvider and ScenarioProvider will plug in behind the same interface.
class HistoricalProvider {
  constructor() {
    this._flows = {};        // edgeId → Array<int|null>
    this._maxByEdge = {};    // edgeId → max non-null count (for normalisation)
    this.epoch = null;       // Date object
    this.intervalMinutes = 15;
  }

  async load(url = 'data/flows.json', { signal } = {}) {
    const res = await fetch(url, { signal });
    if (!res.ok) throw new Error(`Failed to load ${url}: ${res.status}`);
    const payload = await res.json();

    // Epoch string has no timezone suffix. Without 'Z', JS parses it as LOCAL
    // time, but controls.js formats with getUTC* — the display would show one
    // hour behind (CET). Parse as UTC so parse and format agree.
    this.epoch = new Date(
      payload.epoch.endsWith('Z') ? payload.epoch : payload.epoch + 'Z'
    );
    this.intervalMinutes = payload.interval_minutes;
    this._flows = payload.flows;
    this._maxByEdge = {};

    // Scenario files (from run_scenario.py) carry extra fields; plain
    // historical/forecast files leave these empty.
    this.isScenario  = !!payload.scenario;
    this.trajectories = payload.trajectories ?? null;   // per-vehicle playback file
    this.closedEdges = payload.scenario?.closed_edges
                       ?? (payload.scenario?.closed_edge ? [payload.scenario.closed_edge] : []);
    // New scenarios carry per-edge [begin_s, end_s) windows. Existing saved
    // scenarios have no such field and retain their whole-scenario styling.
    this.closures    = payload.scenario?.closures ?? null;
    this.label       = payload.scenario?.label ?? null;
    this.source      = payload.scenario?.source ?? 'historical';  // 'forecast' = simulated 2027
    this.agentDemand = payload.scenario?.agent_demand ?? null;
    // Per directed sensor edge: frozen source value, calibration target,
    // displayed ensemble mean, and the single vehicle seed.  This is kept
    // outside flowAt() because it is audit metadata, not map flow data.
    this.sensorAudit = payload.sensor_audit ?? null;
    this.confidence  = payload.confidence ?? null;  // per-edge, per-scenario
    // Length in quarters (96 for a one-day scenario, 35 040 for a full
    // year) — State.setMaxQI() uses this so playback loops within THIS
    // provider's own data instead of the year-long default.
    this.numQuarters = payload.n_quarters
      ?? Object.values(this._flows)[0]?.length
      ?? 35040;

    // Compute the normalisation maximum and the calm midday reference in
    // one pass.  The previous filter+reduce allocated a second array for
    // every edge; with thousands of edges and year-long flows that was a
    // large, avoidable parse-time allocation.  null remains missing and
    // never participates in max or calm.
    this._calmByEdge = {};
    for (const [edgeId, arr] of Object.entries(this._flows)) {
      let max = 0;
      let hasValue = false;
      let calmSum = 0;
      let calmN = 0;
      for (let i = 0; i < arr.length; i++) {
        const value = arr[i];
        if (value !== null && value !== undefined) {
          hasValue = true;
          if (value > max) max = value;
          // Slots 40–59 = 10:00–14:45, the calm daytime window between the
          // rush peaks (modulo a day, so multi-day scenarios average all
          // their middays).
          const slot = i % 96;
          if (slot >= 40 && slot < 60) { calmSum += value; calmN++; }
        }
      }
      this._maxByEdge[edgeId] = hasValue ? max : 1;
      this._calmByEdge[edgeId] = calmN ? calmSum / calmN : null;
    }
    return this;
  }

  // Core seam — all consumers use only this.
  flowAt(edgeId, quarterIndex) {
    const arr = this._flows[edgeId];
    if (!arr) return null;
    const v = arr[quarterIndex];
    return v !== undefined ? v : null;
  }

  maxFlow(edgeId) {
    return this._maxByEdge[edgeId] ?? null;
  }

  // Calm midday (10:00–15:00) mean for this edge, from this provider's own
  // flows.  Gives simulated background streets the same colour semantics as
  // sensor edges ("relative to calm daytime traffic") — without it the
  // renderer fell back to each edge's own maximum, which paints EVERY street
  // full red at its own peak, however little traffic it actually carries.
  calmFlow(edgeId) {
    return this._calmByEdge?.[edgeId] ?? null;
  }

  // True if this provider carries data for the edge. Scenario providers have
  // data for most background edges too — the renderer colours those as well.
  hasEdge(edgeId) {
    return Object.prototype.hasOwnProperty.call(this._flows, edgeId);
  }

  isEdgeClosed(edgeId, qi) {
    if (!this.closedEdges.includes(edgeId)) return false;
    if (!this.closures) return true;
    const t = qi * this.intervalMinutes * 60;
    return this.closures.some(c => c.edge_id === edgeId
      && c.begin_s <= t && t < c.end_s);
  }

  closureWindowText(edgeId) {
    if (!this.closures) return null;
    const windows = this.closures.filter(c => c.edge_id === edgeId);
    if (!windows.length) return null;
    const time = (seconds) => this.dateFromQI(seconds / (this.intervalMinutes * 60))
      .toISOString().slice(11, 16);
    return windows.map(c => `${time(c.begin_s)}–${time(c.end_s)}`).join(', ');
  }

  dateFromQI(qi) {
    return new Date(this.epoch.getTime() + qi * this.intervalMinutes * 60 * 1000);
  }
}

// NormalProfile — average September 2025 flow per edge, weekday / weekend.
// Used by render.js to colour edges relative to normal rather than yearly max.
// All providers compare against the same reference — load once, share everywhere.
class NormalProfile {
  constructor() {
    this._profiles   = {};
    this._slotsPerDay = 96;
  }

  async load(url = 'data/normal_profile.json') {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`Failed to load ${url}: ${res.status}`);
    const data = await res.json();
    this._profiles    = data.profiles;
    this._slotsPerDay = data.slots_per_day ?? 96;
    return this;
  }

  // Expected normal flow for this edge at this quarter-index.
  // dayOfWeek (0 = Mon … 6 = Sun) MUST come from the active provider's epoch
  // (2025 starts on a Wednesday, 2027 on a Friday) — never derive it from qi
  // alone, or the weekday/weekend profile is wrong in forecast mode.
  flowAt(edgeId, qi, dayOfWeek) {
    const p = this._profiles[edgeId];
    if (!p) return null;
    const slot = qi % this._slotsPerDay;
    const arr  = dayOfWeek >= 5 ? p.weekend : p.weekday;
    const v    = arr[slot];
    return (v !== null && v !== undefined) ? v : null;
  }

  // Average flow across the whole day in September (one number per edge).
  dailyAvgAt(edgeId, dayOfWeek) {
    const p = this._profiles[edgeId];
    if (!p) return null;
    const avg = dayOfWeek >= 5 ? p.weekend_daily_avg : p.weekday_daily_avg;
    return avg ?? null;
  }

  // Calm reference: September average EXCLUDING rush hours (07-09, 15-18).
  // Returns null during rush hour slots — caller falls back to flowAt().
  calmAt(edgeId, qi, dayOfWeek) {
    const p = this._profiles[edgeId];
    if (!p) return null;
    const slot = qi % this._slotsPerDay;
    const arr  = dayOfWeek >= 5 ? p.calm_weekend : p.calm_weekday;
    if (!arr) return null;
    const v = arr[slot];
    return (v !== null && v !== undefined) ? v : null;
  }
}


// DeltaProvider — the comparison seam: "what did closing this road change?"
//
// It wraps two ALREADY-LOADED providers (a baseline and a closure scenario)
// rather than fetching anything, so it plugs in behind the same flowAt()
// interface the renderer already speaks and needs no new pipeline artifact.
//
// Three honesty rules are built in, because a raw subtraction would overstate
// what the simulation can support:
//
//  1. Seed noise. Scenario flows are Monte Carlo means over seeds. Measured on
//     the checked-in baseline, busy edges carry a 6-19% coefficient of
//     variation BETWEEN seeds. A change smaller than that is what re-running
//     with different seeds would produce anyway, so it is reported as
//     'within_noise' and drawn neutral instead of coloured.
//  2. The accuracy gradient. run_scenario writes
//     confidence = spatial_prior * exp(-CV). Where the spatial prior is 0 the
//     CV cannot be recovered and no claim is supportable; those edges are
//     still shown (hiding them would hide real redistribution) but marked
//     unclaimable so the UI can say so.
//  3. Low volumes. A street going 0 -> 2 vehicles is +200% and means nothing.
//     A minimum baseline volume gates the relative view.
class DeltaProvider {
  // spatialPrior: {edgeId -> network.geojson confidence} (the exp(-d^2/2s^2)
  // prior), needed to invert the published confidence formula.
  constructor(baseline, closure, spatialPrior) {
    this.baseline     = baseline;
    this.closure      = closure;
    this._prior       = spatialPrior || {};
    this.isDelta      = true;
    this.isScenario   = false;   // delta styling, not confidence styling
    this.numQuarters  = Math.min(baseline.numQuarters, closure.numQuarters);
    this.epoch        = closure.epoch;
    this.closedEdges  = closure.closedEdges || [];
    this.closures     = closure.closures ?? null;
    this.label        = closure.label;
    this.source       = closure.source;
    // Fields the renderer and app read straight off the active provider. A
    // wrapper that omits any of them silently degrades a panel or throws
    // mid-redraw, so they are forwarded explicitly rather than by chance.
    this.confidence   = closure.confidence;
    this.trajectories = closure.trajectories;
    this.sensorAudit  = closure.sensorAudit;
    this.agentDemand  = closure.agentDemand;
    this._cv          = {};      // edgeId -> recovered CV, or null
  }

  // Renderer contract: an edge is comparable only when BOTH arms carry it.
  // Reporting an edge the baseline lacks would present "appeared from
  // nothing" as a redistribution effect.
  hasEdge(edgeId) {
    return this.closure.hasEdge(edgeId) && this.baseline.hasEdge(edgeId);
  }

  isEdgeClosed(edgeId, qi) {
    return this.closure.isEdgeClosed
      ? this.closure.isEdgeClosed(edgeId, qi) : false;
  }

  // Delegate the plain flow question to the closure arm so any existing
  // caller of flowAt() keeps getting a real, unmodified flow value.
  flowAt(edgeId, qi)      { return this.closure.flowAt(edgeId, qi); }
  maxFlow(edgeId)         { return this.closure.maxFlow(edgeId); }
  calmFlow(edgeId)        { return this.closure.calmFlow ? this.closure.calmFlow(edgeId) : null; }
  dateFromQI(qi)          { return this.closure.dateFromQI(qi); }
  closureWindowText(id)   { return this.closure.closureWindowText?.(id); }

  baselineAt(edgeId, qi)  { return this.baseline.flowAt(edgeId, qi); }

  // closure - baseline for one quarter. null when either side has no data:
  // a missing value is a gap, never a zero (project rule).
  deltaAt(edgeId, qi) {
    const after  = this.closure.flowAt(edgeId, qi);
    const before = this.baseline.flowAt(edgeId, qi);
    if (after === null || after === undefined) return null;
    if (before === null || before === undefined) return null;
    return after - before;
  }

  // Invert confidence = spatial_prior * exp(-CV). Returns null where the
  // inversion is undefined rather than guessing a noise level.
  cvFor(edgeId) {
    if (edgeId in this._cv) return this._cv[edgeId];
    let cv = null;
    const prior = this._prior[edgeId];
    const cb = this.baseline.confidence ? this.baseline.confidence[edgeId] : null;
    const cc = this.closure.confidence  ? this.closure.confidence[edgeId]  : null;
    if (typeof prior === 'number' && prior > 0 &&
        typeof cb === 'number' && cb > 0 &&
        typeof cc === 'number' && cc > 0) {
      const rb = Math.min(cb / prior, 1);
      const rc = Math.min(cc / prior, 1);
      if (rb > 0 && rc > 0) {
        // Combine both arms: the delta inherits noise from each side.
        cv = Math.sqrt(Math.pow(-Math.log(rb), 2) + Math.pow(-Math.log(rc), 2));
      }
    }
    this._cv[edgeId] = cv;
    return cv;
  }

  // The full comparison for one edge at one quarter.
  //   status: 'closed' | 'no_data' | 'unclaimable' | 'within_noise' | 'changed'
  //   rel:    signed relative change, clamped to [-1, 1]; null when unusable
  compare(edgeId, qi, minBaseline = 5) {
    if (this.closedEdges.includes(edgeId)) {
      return { status: 'closed', delta: null, rel: null, cv: null };
    }
    const before = this.baselineAt(edgeId, qi);
    const after  = this.closure.flowAt(edgeId, qi);
    if (before === null || before === undefined ||
        after === null || after === undefined) {
      return { status: 'no_data', delta: null, rel: null, cv: null };
    }
    const delta = after - before;
    const cv = this.cvFor(edgeId);
    // Below the volume floor a relative change is arithmetic noise.
    if (before < minBaseline && after < minBaseline) {
      return { status: 'within_noise', delta, rel: 0, cv };
    }
    const rel = delta / Math.max(before, minBaseline);
    if (cv === null) {
      return { status: 'unclaimable', delta, rel: Math.max(-1, Math.min(1, rel)), cv: null };
    }
    // Two combined sigma: below this, re-seeding alone explains the change.
    if (Math.abs(rel) <= 2 * cv) {
      return { status: 'within_noise', delta, rel: 0, cv };
    }
    return { status: 'changed', delta, rel: Math.max(-1, Math.min(1, rel)), cv };
  }

  // Whole-window totals for one edge — what a tooltip should quote.
  totals(edgeId) {
    let before = 0, after = 0, n = 0;
    for (let qi = 0; qi < this.numQuarters; qi++) {
      const b = this.baselineAt(edgeId, qi);
      const a = this.closure.flowAt(edgeId, qi);
      if (b === null || b === undefined || a === null || a === undefined) continue;
      before += b; after += a; n++;
    }
    if (!n) return null;
    return { before, after, delta: after - before, quarters: n };
  }
}
