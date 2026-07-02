const State = (() => {
  const MAX_QI = 365 * 96 - 1; // 35 039

  let _qi      = 0;    // kept as float so small speeds accumulate correctly
  let _speed   = 24;
  let _playing = false;

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
    get MAX_QI()  { return MAX_QI; },

    setQI(qi) {
      _qi = Math.max(0, Math.min(MAX_QI, Number(qi)));
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
      if (_qi > MAX_QI) _qi = 0;
      if (Math.floor(_qi) !== prev) emit();
    },
  };
})();
