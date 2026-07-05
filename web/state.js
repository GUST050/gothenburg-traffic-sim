const State = (() => {
  const YEAR_MAX_QI = 365 * 96 - 1; // 35 039 — default, for year-long providers

  let _qi      = 0;    // kept as float so small speeds accumulate correctly
  let _speed   = 24;
  let _playing = false;
  // The active provider's own length in quarters minus 1. A scenario file
  // is only ~96 quarters (one day) — without this, playback would run
  // past the end of the scenario's data and wrap using the YEAR's bound
  // (35 039), so a single simulated day would silently show "no data"
  // for the remaining ~99% of a fake extra "year" before ever looping.
  let _maxQI   = YEAR_MAX_QI;

  function emit() {
    window.dispatchEvent(new CustomEvent('tick', {
      detail: { qi: Math.floor(_qi), speed: _speed, playing: _playing },
    }));
  }

  return {
    get qi()      { return Math.floor(_qi); },
    // Fractional quarter index — render.js uses this to interpolate colour
    // and dot count smoothly between two 15-min slots.
    get qiFloat() { return _qi; },
    get speed()   { return _speed; },
    get playing() { return _playing; },
    get MAX_QI()  { return _maxQI; },

    // Call whenever the active provider changes — pass the new provider's
    // own length in quarters (e.g. 96 for a one-day scenario). Omit/pass
    // null to restore the year-long default (Historical/Forecast).
    setMaxQI(n) {
      _maxQI = (n == null) ? YEAR_MAX_QI : Math.max(0, Number(n) - 1);
      _qi = Math.min(_qi, _maxQI);
    },

    setQI(qi) {
      _qi = Math.max(0, Math.min(_maxQI, Number(qi)));
      emit();
    },

    setSpeed(s) { _speed = Math.max(0.25, Number(s)); },

    play()   { _playing = true;  emit(); },
    pause()  { _playing = false; emit(); },
    toggle() { _playing ? State.pause() : State.play(); },

    // Called every rAF frame — accumulate fractional quarters, never round.
    // Only emits when the integer quarter index actually changes, so Controls
    // and Render are not called 60× per second when speed is low or paused.
    advance(quarters) {
      const prev = Math.floor(_qi);
      _qi += quarters;
      if (_qi > _maxQI) _qi = 0;
      if (Math.floor(_qi) !== prev) emit();
    },
  };
})();
