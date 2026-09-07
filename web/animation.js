const Animation = (() => {
  function shouldRenderFrame(playing, dirty) {
    return Boolean(playing || dirty);
  }

  function frameDelta(lastTs, ts, playing) {
    if (!playing || lastTs === null) return 0;
    return Math.min((ts - lastTs) / 1000, 0.05);
  }

  function applyMarkerStyle(marker, key, style) {
    if (marker._trafficStyleKey === key) return false;
    marker._trafficStyleKey = key;
    marker.setStyle(style);
    return true;
  }

  return { shouldRenderFrame, frameDelta, applyMarkerStyle };
})();
