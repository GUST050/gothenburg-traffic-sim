// Wires DOM → State. Updates display on 'tick' events.
const Controls = (() => {
  let _provider = null;

  // Cached on init() — getElementById is cheap but called every animation frame
  let _playBtn   = null;
  let _tdClock   = null;
  let _tdDate    = null;
  let _daySlider = null;
  let _todSlider = null;
  let _speedBtns = null;
  let _displayCache = {};

  // "Speed" is quarters (900 s of simulated time) advanced per real second.
  // The historical/forecast flow view is a 15-min-bucket conveyor — there's
  // no meaning to "real time" there, so it uses fast scrubbing presets.
  // Vehicle playback in Simulering is individual cars moving continuously,
  // so it gets presets anchored to actual elapsed seconds instead.
  const QUARTER_SPEEDS  = [
    { label: '1×',  speed: 1 },   { label: '4×',  speed: 4 },
    { label: '24×', speed: 24 },  { label: '96×', speed: 96 },
  ];
  const REALTIME_SPEEDS = [
    { label: 'Realtid', speed: 1 / 900 },  { label: '10×',  speed: 10 / 900 },
    { label: '60×',     speed: 60 / 900 }, { label: '300×', speed: 300 / 900 },
  ];

  const DAYS   = ['Sön','Mån','Tis','Ons','Tor','Fre','Lör'];
  const MONTHS = ['jan','feb','mar','apr','maj','jun','jul','aug','sep','okt','nov','dec'];

  // Fill the slider track left of the thumb (accent → dark track)
  function setFill(slider) {
    const pct = (100 * (slider.value - slider.min)) / (slider.max - slider.min);
    slider.style.background =
      `linear-gradient(to right, #3b82f6 0% ${pct}%, #1c2740 ${pct}% 100%)`;
  }

  // Reads current State fresh (not from an event payload) so it can be
  // called both from the 'tick' event AND every animation frame during
  // playback (see Clock.js) — State.advance() only DISPATCHES 'tick' when
  // the INTEGER quarter index changes (correct: Render's edge/sensor
  // redraw only needs per-quarter granularity), but Simulering's real-
  // time-anchored speeds (10x-300x, all far slower than 1 quarter/sec)
  // mean that gate can go 90+ real seconds between events — the clock,
  // date, and sliders would sit frozen the whole time even though
  // qiFloat (and the map's own independent per-frame animation loop) are
  // both actually advancing. Found via browser testing: play a Simulering
  // scenario and watch the clock never move for a minute and a half.
  function updateDisplay() {
    // Use the FRACTIONAL quarter index (not the rounded one) for the
    // clock — real-time vehicle playback needs actual seconds to visibly
    // move; at 1x real-time speed a whole quarter takes 15 real minutes;
    // without this the displayed clock would sit on the same minute the
    // entire time.
    const d = _provider.dateFromQI(State.qiFloat);
    const h = String(d.getUTCHours()).padStart(2, '0');
    const m = String(d.getUTCMinutes()).padStart(2, '0');
    const s = String(d.getUTCSeconds()).padStart(2, '0');
    const clock = `${h}:${m}:${s}`;
    if (_displayCache.clock !== clock) {
      _tdClock.textContent = clock;
      _displayCache.clock = clock;
    }
    // Year is always shown — otherwise 2025/2027 modes are indistinguishable
    const date =
      `${DAYS[d.getUTCDay()]} ${String(d.getUTCDate()).padStart(2, '0')} ` +
      `${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
    if (_displayCache.date !== date) {
      _tdDate.textContent = date;
      _displayCache.date = date;
    }
    const play = State.playing ? '⏸' : '▶';
    if (_displayCache.play !== play) {
      _playBtn.textContent = play;
      _displayCache.play = play;
    }

    const qi  = State.qi;
    const day = Math.floor(qi / 96);
    const tod = qi % 96;
    if (Number(_daySlider.value) !== day) {
      _daySlider.value = day;
      setFill(_daySlider);
    }
    if (Number(_todSlider.value) !== tod) {
      _todSlider.value = tod;
      setFill(_todSlider);
    }
  }

  return {
    setProvider(p) {
      _provider = p;
      // A provider switch can keep the same qi while changing the displayed
      // epoch (2025 historical -> 2027 forecast); invalidate only the tiny
      // DOM memo, not the provider data.
      _displayCache = {};
    },

    // Call when entering/leaving Simulering — swaps the 4 speed buttons
    // between real-time-anchored presets (watching cars move) and the
    // fast quarter-scrubbing presets (browsing a year of flow data).
    setSpeedMode(realtime) {
      const presets = realtime ? REALTIME_SPEEDS : QUARTER_SPEEDS;
      _speedBtns.forEach((btn, i) => {
        btn.textContent = presets[i].label;
        btn.dataset.speed = presets[i].speed;
      });
      const def = realtime ? presets[1] : presets[2];   // "10×" / "24×"
      State.setSpeed(def.speed);
      _speedBtns.forEach(b => b.classList.toggle('active', Number(b.dataset.speed) === def.speed));
    },

    init(provider) {
      _provider  = provider;
      _playBtn   = document.getElementById('play-btn');
      _tdClock   = document.getElementById('td-clock');
      _tdDate    = document.getElementById('td-date');
      _daySlider = document.getElementById('day-slider');
      _todSlider = document.getElementById('tod-slider');
      _displayCache = {};

      _playBtn.addEventListener('click', () => State.toggle());

      // Speed — segmented buttons. The 4 DOM nodes are reused for both
      // preset sets (setSpeedMode swaps label/data-speed in place) so the
      // listeners attached here keep working after a mode switch.
      _speedBtns = document.querySelectorAll('#speed-seg button');
      _speedBtns.forEach(btn => {
        btn.addEventListener('click', () => {
          State.setSpeed(Number(btn.dataset.speed));
          _speedBtns.forEach(b => b.classList.toggle('active', b === btn));
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
      window.addEventListener('tick', updateDisplay);
    },

    // Called every animation frame during playback by Clock.js, in
    // addition to the coarser 'tick' event above — see updateDisplay's
    // own comment for why both paths are needed.
    updateDisplay,
  };
})();
