// HistoricalProvider — the only implementation of the flowAt seam today.
// Later, ModelProvider and ScenarioProvider will plug in behind the same interface.
class HistoricalProvider {
  constructor() {
    this._flows = {};        // edgeId → Array<int|null>
    this._maxByEdge = {};    // edgeId → max non-null count (for normalisation)
    this.epoch = null;       // Date object
    this.intervalMinutes = 15;
  }

  async load(url = 'data/flows.json') {
    const res = await fetch(url);
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
    this.confidence  = payload.confidence ?? null;  // per-edge, per-scenario
    // Length in quarters (96 for a one-day scenario, 35 040 for a full
    // year) — State.setMaxQI() uses this so playback loops within THIS
    // provider's own data instead of the year-long default.
    this.numQuarters = payload.n_quarters
      ?? Object.values(this._flows)[0]?.length
      ?? 35040;

    for (const [edgeId, arr] of Object.entries(this._flows)) {
      const nums = arr.filter(v => v !== null);
      this._maxByEdge[edgeId] = nums.length
        ? nums.reduce((a, b) => a > b ? a : b)
        : 1;
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
