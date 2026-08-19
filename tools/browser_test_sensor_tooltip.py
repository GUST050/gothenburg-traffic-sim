#!/usr/bin/env python3
"""Browser test: the number a person hovers is the number we checked.

tools/check_map_matches_sensors.py proves the published artifact agrees with
the sensors. It cannot prove the browser shows that artifact — between the
file and the screen sit a provider, a canvas renderer and a tooltip closure,
none of which pytest sees. This drives a real headless Chrome over the
DevTools Protocol, hovers a sensor edge with a real mouse event, and reads
the tooltip out of the DOM.

The map is drawn with ``preferCanvas: true``, so edges are not DOM elements
and cannot be selected: the hover has to land on real pixels. Leaflet's
``L.map`` is therefore wrapped before the app runs, purely to learn where a
given coordinate ends up on screen — the same runtime-patch technique
tools/browser_test_delta_view.py uses to observe the renderer without
changing production code.

    python3 serve.py &          # must already be serving
    python3 tools/browser_test_sensor_tooltip.py

Exit code 0 only if the hovered number equals the published flow.
"""
from __future__ import annotations

import base64
import json
import re
import subprocess
import time
import urllib.request
from pathlib import Path

import websocket

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PORT = 9333
URL = "http://localhost:8000/"
PROFILE = "/tmp/cdp-sensor-tooltip-profile"
# A busy quarter: 08:00-08:15. A sleeping 03:00 would "pass" on two zeroes.
BUSY_QUARTER = 32


def geh_value(simulated: float, target: float) -> float:
    """Same comparison tools/check_map_matches_sensors.py gates on."""
    total = simulated + target
    return 0.0 if total <= 0 else ((2.0 * (simulated - target) ** 2 / total) ** 0.5)


def start_chrome() -> subprocess.Popen:
    proc = subprocess.Popen(
        [CHROME, "--headless=new", f"--remote-debugging-port={PORT}",
         "--remote-allow-origins=*", "--no-first-run",
         "--no-default-browser-check", "--window-size=1400,900",
         f"--user-data-dir={PROFILE}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            urllib.request.urlopen(
                f"http://localhost:{PORT}/json/version", timeout=1)
            return proc
        except Exception:
            time.sleep(0.5)
    proc.kill()
    raise RuntimeError("Chrome DevTools endpoint never came up")


class CDP:
    def __init__(self, ws_url: str) -> None:
        self.ws = websocket.create_connection(ws_url, timeout=60)
        self.n = 0
        self.events: list[dict] = []

    def send(self, method: str, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method,
                                 "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
            self.events.append(msg)

    def evaluate(self, expression: str):
        result = self.send("Runtime.evaluate", expression=expression,
                           awaitPromise=True, returnByValue=True)
        if result.get("exceptionDetails"):
            raise RuntimeError("page exception: "
                               f"{json.dumps(result['exceptionDetails'])[:600]}")
        return result.get("result", {}).get("value")

    def console_errors(self) -> list[str]:
        out = []
        for event in self.events:
            if event.get("method") == "Log.entryAdded":
                entry = event["params"]["entry"]
                if entry.get("level") == "error":
                    out.append(entry.get("text", "")[:200])
            if event.get("method") == "Runtime.exceptionThrown":
                detail = event["params"]["exceptionDetails"]
                out.append(str(detail.get("text"))[:200])
        return out


# Installed before any page script: remember every map Leaflet builds, so the
# test can convert a sensor edge's coordinate into the pixel to hover.
CAPTURE_MAP = """
(() => {
  let installed = false;
  const install = () => {
    if (installed || typeof L === 'undefined' || !L.map) return;
    installed = true;
    const original = L.map;
    L.map = function (...args) {
      const map = original.apply(this, args);
      window.__testMap = map;
      return map;
    };
    Object.assign(L.map, original);
  };
  const timer = setInterval(() => { install(); if (installed) clearInterval(timer); }, 10);
})();
"""


def main() -> int:
    proc = start_chrome()
    failures: list[str] = []
    shot = Path("/tmp/sensor-tooltip.png")
    try:
        targets = json.loads(
            urllib.request.urlopen(f"http://localhost:{PORT}/json").read())
        page = next(t for t in targets if t["type"] == "page")
        cdp = CDP(page["webSocketDebuggerUrl"])
        cdp.send("Runtime.enable")
        cdp.send("Log.enable")
        cdp.send("Page.enable")
        cdp.send("Page.addScriptToEvaluateOnNewDocument", source=CAPTURE_MAP)
        cdp.send("Page.navigate", url=URL)

        # const/class at script top level bind lexically, not on window, so
        # probe the names themselves rather than window.<name>.
        for _ in range(120):
            time.sleep(1)
            try:
                if cdp.evaluate("typeof Render === 'object' && "
                                "typeof State === 'object'"):
                    break
            except Exception:
                pass
        else:
            raise RuntimeError("app globals never appeared")

        # The app opens on a task chooser; the map exists only inside a
        # workspace. Enter it the way a person does.
        cdp.evaluate("(()=>{const c=[...document.querySelectorAll('.task-card')]"
                     ".find(x=>x.getAttribute('data-task')==='scenario');"
                     "if(c) c.click(); return !!c})()")
        time.sleep(6)

        print("STEG 1 — visa simuleringen (samma knapp som i UI:t)")
        cdp.evaluate("document.getElementById('btn-scen').click()")
        for _ in range(60):
            time.sleep(1)
            if cdp.evaluate("!!window.__testMap && typeof Render === 'object'"):
                break
        loaded = cdp.evaluate(
            "(async()=>{const r=await fetch('data/scenarios/index.json');"
            "const i=await r.json();return i.scenarios.map(s=>s.name).join(',')})()")
        print(f"  ok  publicerade scenarier: {loaded}")

        print(f"STEG 2 — ställ klockan på kvart {BUSY_QUARTER} (08:00)")
        cdp.evaluate(f"State.pause(); State.setQI({BUSY_QUARTER});")
        time.sleep(2)
        quarter = cdp.evaluate("State.qi")
        if quarter != BUSY_QUARTER:
            failures.append(f"clock is at quarter {quarter}, not {BUSY_QUARTER}")
        print(f"  ok  State.qi = {quarter}")

        print("STEG 3 — hitta en sensorkant och räkna ut var den ligger på skärmen")
        located = cdp.evaluate("""
        (async () => {
          const geo = await (await fetch('data/network.geojson')).json();
          const feats = geo.features.filter(f => f.properties.sensor_id
            && f.geometry.type === 'LineString');
          const map = window.__testMap;
          const size = map.getSize();
          const box = document.getElementById('map').getBoundingClientRect();
          // Offer EVERY vertex of every visible sensor edge. A single
          // midpoint sits a couple of pixels from a parallel background
          // street as often as not, and the canvas renderer hit-tests
          // whatever is under the pixel — the neighbour's tooltip is a
          // perfectly good tooltip and a completely useless test.
          for (const f of feats) {
            const points = [];
            for (const [lng, lat] of f.geometry.coordinates) {
              const p = map.latLngToContainerPoint([lat, lng]);
              if (p.x > 8 && p.y > 8 && p.x < size.x - 8 && p.y < size.y - 8) {
                points.push({x: Math.round(box.left + p.x),
                             y: Math.round(box.top + p.y)});
              }
            }
            if (points.length) {
              return {edge: f.properties.id,
                      sensor: String(f.properties.sensor_id),
                      name: f.properties.name || f.properties.id,
                      points: points};
            }
          }
          return null;
        })()
        """)
        if not located:
            raise RuntimeError("no sensor edge is inside the viewport")
        print(f"  ok  sensor {located['sensor']} ({located['name']}), "
              f"{len(located['points'])} punkter på kanten i vyn")

        # The dot layer redraws every animation frame and Leaflet drops the
        # hover with each redraw, so a tooltip survives only the gap between
        # two frames. Stop the dots for the hover: the tooltip's text is a
        # pure function of flowAt(), which the vehicle layer does not touch.
        cdp.evaluate("Render.setVehicleMode(false)")
        time.sleep(1)

        print("STEG 4 — för musen dit på riktigt och läs verktygstipset")
        wanted = f"Sensor {located['sensor']}"
        tooltip = None
        cursor = None
        probes = 0
        for point in located["points"]:
            for radius in (0, 1, 2, 3):
                offsets = [(0, 0)] if radius == 0 else [
                    (dx, dy) for dx in (-radius, 0, radius)
                    for dy in (-radius, 0, radius) if dx or dy]
                for dx, dy in offsets:
                    probes += 1
                    cdp.send("Input.dispatchMouseEvent", type="mouseMoved",
                             x=point["x"] + dx, y=point["y"] + dy,
                             button="none", buttons=0)
                    time.sleep(0.12)
                    # Read EVERY open tooltip, not the first in the DOM.
                    # A street can carry both a sensor edge and an ordinary
                    # segment with the same name, and querySelector would
                    # happily hand back the neighbour's box while the
                    # screenshot shows the other one. Requiring the sensor
                    # tooltip to be the only one open is what makes "this is
                    # on screen" a statement rather than a hope.
                    open_tooltips = cdp.evaluate(
                        "[...document.querySelectorAll('.leaflet-tooltip')]"
                        ".map(t => t.innerText)")
                    if len(open_tooltips) == 1 and wanted in open_tooltips[0]:
                        tooltip = open_tooltips[0]
                        cursor = (point["x"] + dx, point["y"] + dy)
                        break
                if tooltip:
                    break
            if tooltip:
                break
        if not tooltip:
            failures.append(
                f"hovering {located['edge']} never produced its own tooltip "
                f"({probes} probes)")
            print(f"  FEL  inget sensorverktygstips efter {probes} försök")
        else:
            print(f"  ok  verktygstips efter {probes} musrörelser "
                  f"(pekaren på {cursor}, ett enda öppet verktygstips):")
            for line in tooltip.splitlines():
                print(f"        {line}")
            # The canvas layer drops the hover on every redraw, so the box
            # lives for a fraction of a second and a screenshot cannot be
            # aimed at it reliably. The panel behind the "Sensorflöden"
            # button shows the same audited numbers and stays put, so the
            # picture is taken there — the tooltip evidence above is the DOM
            # read, not the image.
            cdp.evaluate("document.getElementById('sensor-audit-btn').click()")
            time.sleep(2)
            rows = cdp.evaluate(
                "(()=>{const t=document.getElementById('sensor-audit-table');"
                "return t? [...t.querySelectorAll('tr')].map("
                "r=>[...r.children].map(c=>c.innerText.trim()).join(' | ')) : null})()")
            if not rows:
                failures.append("the Sensorflöden panel rendered no table")
                print("  FEL  sensorpanelen visade ingen tabell")
            else:
                print(f"  ok  sensorpanelen visar {len(rows) - 1} rader:")
                for row in rows[:9]:
                    print(f"        {row}")
                shot.write_bytes(base64.b64decode(
                    cdp.send("Page.captureScreenshot")["data"]))

        print("STEG 5 — jämför den visade siffran med den publicerade filen")
        published = cdp.evaluate(f"""
        (async () => {{
          const s = await (await fetch('data/scenarios/baseline.json')).json();
          // The station's own calibration target for this quarter, so the
          // browser number is tied back to the sensor and not merely to the
          // file the browser just fetched.
          const st = (s.sensor_audit?.stations || []).find(
            x => (x.edge_ids || []).includes('{located["edge"]}'));
          return {{flow: s.flows['{located["edge"]}'][{BUSY_QUARTER}],
                   target: st ? st.target_mean[{BUSY_QUARTER}] : null,
                   measurement: st ? st.measurement : null}};
        }})()
        """)
        shown = None
        if tooltip:
            match = re.search(r"(\d+)\s*fordon\s*/\s*15\s*min", tooltip)
            shown = int(match.group(1)) if match else None
        print(f"  sensorns mål för kvarten ({published['measurement']}) = "
              f"{published['target']}")
        print(f"  publicerad flows[...][{BUSY_QUARTER}]{'':17s}= "
              f"{published['flow']}")
        print(f"  visad i webbläsaren{'':24s}= {shown}")
        if published["target"] is not None and published["flow"] is not None:
            deviation = geh_value(published["flow"], published["target"])
            print(f"  GEH(visad, sensor) = {deviation:.3f}")
            if deviation >= 5.0:
                failures.append(
                    f"the number on screen is GEH {deviation:.2f} from the "
                    "sensor target for that quarter")
        if shown is None:
            failures.append("no vehicle count could be read from the tooltip")
        elif shown != published["flow"]:
            failures.append(
                f"the browser shows {shown} where the published file says "
                f"{published['flow']}")
        else:
            print("  ok  identiska")

        print("STEG 6 — konsolfel")
        errors = cdp.console_errors()
        # A tile fetch failing offline says nothing about our data path.
        errors = [e for e in errors
                  if "tile" not in e.lower() and "openstreetmap" not in e.lower()]
        if errors:
            failures.append(f"console errors: {errors[:3]}")
            print(f"  FEL  {len(errors)} fel i konsolen")
        else:
            print("  ok  inga fel i konsolen")

        print(f"\nskärmbild: {shot}")
    finally:
        proc.kill()

    print()
    if failures:
        print("FAIL — vad kartan visar stämmer inte med den publicerade filen:")
        for problem in failures:
            print(f"  - {problem}")
        return 1
    print("PASS — siffran i webbläsaren är den publicerade, sensorkontrollerade "
          "siffran.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
