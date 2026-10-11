// Whether this page may offer the simulation workspaces, from /api/ping.
//
// A server answering is not the same as a server able to simulate: on
// 2026-10-10 a server whose Python lacked pandas and SUMO still answered
// ping, so "Byt dag" was offered and every job failed. The server now reports
// its own verdict in ping's "simulation" field (traffic_sim/simulation/
// interpreter.py); this module turns it into what the page shows.
const SimulationStatus = (() => {
  // While the server is still checking, ask again until it settles. Bounded
  // by the server-side probe timeout (120 s) plus margin.
  const RECHECK_MS = 1500;
  const MAX_RECHECKS = 100;

  /**
   * @param {boolean} apiAvailable  a local server answered /api/ping
   * @param {{status?: string, message?: string, fix?: string}|null|undefined} simulation
   * @returns {{canSimulate: boolean, message: string, recheck: boolean}}
   */
  function access(apiAvailable, simulation) {
    if (!apiAvailable) return { canSimulate: false, message: '', recheck: false };
    if (simulation === undefined || simulation === null) {
      // Every local server reports readiness; without it nothing says this
      // server can simulate, so it is not offered (review 2026-10-10).
      return {
        canSimulate: false,
        message: 'Servern rapporterar inte om simuleringen är redo. Starta om ' +
                 'servern med `make serve` i projektmappen.',
        recheck: false,
      };
    }
    const status = simulation.status;
    if (status === 'ready') return { canSimulate: true, message: '', recheck: false };
    // Only a completed "ready" offers simulation. "checking" waits and asks
    // again; "unknown" (the check could not finish) and "unavailable" are
    // not ready either (changed 2026-10-10: an incomplete check never
    // passes as ready).
    const fix = simulation.fix || 'make setup';
    const fallback = status === 'checking'
      ? 'Kontrollerar simuleringsmiljön…'
      : `Simuleringsmiljön är inte redo på servern — kör \`${fix}\` i projektmappen.`;
    return {
      canSimulate: false,
      message: simulation.message || fallback,
      recheck: status === 'checking',
    };
  }

  return { access, RECHECK_MS, MAX_RECHECKS };
})();
