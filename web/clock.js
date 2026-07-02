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
    } else {
      _lastTs = null;
    }
    requestAnimationFrame(tick);
  }

  return {
    start() { requestAnimationFrame(tick); },
  };
})();
