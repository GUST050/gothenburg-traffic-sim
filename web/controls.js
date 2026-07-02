// Wires DOM → State. Updates display on 'tick' events.
const Controls = (() => {
  let _provider = null;

  // Cached on init() — getElementById is cheap but called every animation frame
  let _playBtn   = null;
  let _tdClock   = null;
  let _tdDate    = null;
  let _daySlider = null;
  let _todSlider = null;

  const DAYS   = ['Sön','Mån','Tis','Ons','Tor','Fre','Lör'];
  const MONTHS = ['jan','feb','mar','apr','maj','jun','jul','aug','sep','okt','nov','dec'];

  // Fill the slider track left of the thumb (accent → dark track)
  function setFill(slider) {
    const pct = (100 * (slider.value - slider.min)) / (slider.max - slider.min);
    slider.style.background =
      `linear-gradient(to right, #3b82f6 0% ${pct}%, #1c2740 ${pct}% 100%)`;
  }

  function onTick({ detail: { qi, playing } }) {
    const d = _provider.dateFromQI(qi);
    const h = String(d.getUTCHours()).padStart(2, '0');
    const m = String(d.getUTCMinutes()).padStart(2, '0');
    _tdClock.textContent = `${h}:${m}`;
    // Year is always shown — otherwise 2025/2027 modes are indistinguishable
    _tdDate.textContent =
      `${DAYS[d.getUTCDay()]} ${String(d.getUTCDate()).padStart(2, '0')} ` +
      `${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
    _playBtn.textContent = playing ? '⏸' : '▶';

    const day = Math.floor(qi / 96);
    const tod = qi % 96;
    if (Number(_daySlider.value) !== day) { _daySlider.value = day; setFill(_daySlider); }
    if (Number(_todSlider.value) !== tod) { _todSlider.value = tod; setFill(_todSlider); }
  }

  return {
    setProvider(p) {
      _provider = p;
    },

    init(provider) {
      _provider  = provider;
      _playBtn   = document.getElementById('play-btn');
      _tdClock   = document.getElementById('td-clock');
      _tdDate    = document.getElementById('td-date');
      _daySlider = document.getElementById('day-slider');
      _todSlider = document.getElementById('tod-slider');

      _playBtn.addEventListener('click', () => State.toggle());

      // Speed — segmented buttons
      const speedBtns = document.querySelectorAll('#speed-seg button');
      speedBtns.forEach(btn => {
        btn.addEventListener('click', () => {
          State.setSpeed(Number(btn.dataset.speed));
          speedBtns.forEach(b => b.classList.toggle('active', b === btn));
        });
      });

      _daySlider.addEventListener('input', e => {
        setFill(e.target);
        State.setQI(Number(e.target.value) * 96 + (State.qi % 96));
      });

      _todSlider.addEventListener('input', e => {
        setFill(e.target);
        State.setQI(Math.floor(State.qi / 96) * 96 + Number(e.target.value));
      });

      // Keyboard: space = play/pause, ←/→ = ±15 min, Shift+←/→ = ±1 day
      window.addEventListener('keydown', e => {
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'BUTTON') return;
        if (e.key === ' ') {
          e.preventDefault();
          State.toggle();
        } else if (e.key === 'ArrowRight') {
          State.setQI(State.qi + (e.shiftKey ? 96 : 1));
        } else if (e.key === 'ArrowLeft') {
          State.setQI(State.qi - (e.shiftKey ? 96 : 1));
        }
      });

      setFill(_daySlider);
      setFill(_todSlider);
      window.addEventListener('tick', onTick);
    },
  };
})();
