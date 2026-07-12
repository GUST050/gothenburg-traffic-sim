// Drives animation using requestAnimationFrame.
// Converts elapsed wall-clock time × speed into quarter advances.
const Clock = (() => {
  let _lastTs = null;

  function tick(ts) {
    if (State.playing) {
      if (_lastTs !== null) {
        const elapsedSec = (ts - _lastTs) / 1000;
        State.advance(elapsedSec * State.speed);
      }
      _lastTs = ts;
      // State.advance() only dispatches 'tick' when the INTEGER quarter
      // index changes (correct — Render's edge/sensor redraw only needs
      // per-quarter granularity). The clock/date/slider DOM text needs
      // finer resolution than that, especially at Simulering's real-
      // time-anchored speeds where a whole quarter can take up to 900
      // real seconds — without this direct per-frame call the display
      // would sit frozen the entire time even though qiFloat (and the
      // map's own independent per-frame animation) are both advancing.
      Controls.updateDisplay();
    } else {
      _lastTs = null;
    }
    requestAnimationFrame(tick);
  }

  return {
    start() { requestAnimationFrame(tick); },
  };
})();
