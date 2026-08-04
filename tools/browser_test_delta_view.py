#!/usr/bin/env python3
"""Browser test for the comparison (delta) view.

pytest does not cover frontend JS — this project has already had one
production incident from exactly that gap — so the render path is driven in a
real headless Chrome over the DevTools Protocol and its console is captured.

No production code is modified: Leaflet's setStyle is patched at runtime, in
the page, purely to record the colours the renderer actually applies.
"""
import json
import subprocess
import time
import urllib.request

import websocket

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PORT = 9222
URL = "http://localhost:8000/"


def start_chrome():
    proc = subprocess.Popen(
        [CHROME, "--headless=new", f"--remote-debugging-port={PORT}",
         "--remote-allow-origins=*", "--no-first-run", "--no-default-browser-check",
         "--user-data-dir=/tmp/cdp-delta-profile", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://localhost:{PORT}/json/version", timeout=1)
            return proc
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("Chrome DevTools endpoint never came up")


class CDP:
    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, timeout=60)
        self.n = 0
        self.events = []

    def send(self, method, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
            self.events.append(msg)

    def evaluate(self, expr, timeout_note=""):
        res = self.send("Runtime.evaluate", expression=expr,
                        awaitPromise=True, returnByValue=True)
        if res.get("exceptionDetails"):
            raise RuntimeError(
                f"page exception{timeout_note}: "
                f"{json.dumps(res['exceptionDetails'])[:800]}")
        return res.get("result", {}).get("value")


def main():
    proc = start_chrome()
    try:
        targets = json.loads(
            urllib.request.urlopen(f"http://localhost:{PORT}/json").read())
        page = next(t for t in targets if t["type"] == "page")
        cdp = CDP(page["webSocketDebuggerUrl"])
        cdp.send("Runtime.enable")
        cdp.send("Log.enable")
        cdp.send("Page.enable")
        cdp.send("Page.navigate", url=URL)

        # `class`/`const` at script top level bind lexically, NOT on window —
        # probing window.Render silently reports "not loaded" forever.
        ready = False
        for _ in range(120):
            time.sleep(1)
            try:
                if cdp.evaluate(
                        "typeof Render === 'object' && "
                        "typeof DeltaProvider === 'function' && "
                        "typeof HistoricalProvider === 'function' && "
                        "typeof State === 'object'"):
                    ready = True
                    break
            except Exception:
                pass
        if not ready:
            raise RuntimeError("app globals never appeared")

        # The app opens on a task chooser; the map is only initialised once a
        # workspace is entered. Enter the scenario workspace like a user would.
        cdp.evaluate(
            "(()=>{const c=[...document.querySelectorAll('.task-card')]"
            ".find(x=>x.getAttribute('data-task')==='scenario');"
            "if(c) c.click(); return !!c})()")
        time.sleep(6)   # let the workspace build the map and load flows

        print("TEST 1 — comparison classes are loaded")
        assert cdp.evaluate("typeof DeltaProvider === 'function'"), "DeltaProvider missing"
        print("  ok  DeltaProvider present")

        print("TEST 2 — build a delta over REAL baseline data and render it")
        setup = r"""
        (async () => {
          // Record every style the renderer applies.
          window.__styles = [];
          if (!window.__patched) {
            const orig = L.Path.prototype.setStyle;
            L.Path.prototype.setStyle = function (s) {
              window.__styles.push({ color: s.color, opacity: s.opacity,
                                     dashArray: s.dashArray });
              return orig.call(this, s);
            };
            window.__patched = true;
          }

          const base = await new HistoricalProvider()
            .load('data/scenarios/baseline.json?t=' + Date.now());

          // Synthetic closure derived from the real baseline: a controlled
          // perturbation is the only way to assert the sign of the colouring.
          const raw = await (await fetch('data/scenarios/baseline.json?t=' + Date.now())).json();
          // Only edges whose CV is RECOVERABLE can be classified at all
          // (confidence = prior * exp(-CV) is not invertible where the prior
          // is 0). Selecting from the claimable population is what makes the
          // sign assertions meaningful rather than a lottery.
          const net = await (await fetch('data/network.geojson?v=' + Date.now())).json();
          const prior = {};
          for (const f of net.features) {
            if (f.geometry && f.geometry.type === 'LineString' &&
                typeof f.properties.confidence === 'number')
              prior[f.properties.id] = f.properties.confidence;
          }
          window.__prior = prior;
          const ids = Object.keys(raw.flows);
          const busy = ids.filter(id => {
            const a = raw.flows[id];
            if (!Array.isArray(a) || a.reduce((s,v)=>s+(v||0),0) <= 500) return false;
            return prior[id] > 0 && raw.confidence && raw.confidence[id] > 0;
          });
          window.__busyCount = busy.length;
          const n = Math.floor(busy.length / 3);
          const up   = busy.slice(0, n);
          const down = busy.slice(n, 2*n);
          const same = busy.slice(2*n, 3*n);
          for (const id of up)   raw.flows[id] = raw.flows[id].map(v => v===null?null:Math.round(v*3));
          for (const id of down) raw.flows[id] = raw.flows[id].map(v => v===null?null:Math.round(v*0.2));
          // `same` untouched -> must land inside the seed-noise band
          raw.scenario = Object.assign({}, raw.scenario,
            { closed_edges: [busy[60]], label: 'TESTAVSTÄNGNING' });

          const clos = new HistoricalProvider();
          clos._fromPayload ? clos._fromPayload(raw) : null;
          window.__closureRaw = raw;
          window.__up = up; window.__down = down; window.__same = same;
          window.__closed = busy[60];
          return { busy: busy.length, up: up.length, down: down.length };
        })()
        """
        info = cdp.evaluate(setup)
        print(f"  baseline edges with >500 veh/day: {info['busy']}")

        # Both arms are REAL HistoricalProvider instances built by the real
        # loader from the real baseline file; the closure arm's flow arrays are
        # then perturbed in place. (Its derived max/calm caches go stale, which
        # is irrelevant here: delta styling reads only flowAt/confidence.)
        build = r"""
        (async () => {
          const base = await new HistoricalProvider()
            .load('data/scenarios/baseline.json?t=' + Date.now());
          const clos = await new HistoricalProvider()
            .load('data/scenarios/baseline.json?t=' + Date.now());
          for (const id of window.__up)
            clos._flows[id] = clos._flows[id].map(v => v===null?null:Math.round(v*3));
          for (const id of window.__down)
            clos._flows[id] = clos._flows[id].map(v => v===null?null:Math.round(v*0.2));
          clos.closedEdges = [window.__closed];
          const dp = new DeltaProvider(base, clos, window.__prior);
          window.__dp = dp;
          const tally = (arr) => {
            const out = {};
            let posChanged = 0, negChanged = 0;
            for (const id of arr) {
              const c = dp.compare(id, 40);
              out[c.status] = (out[c.status] || 0) + 1;
              if (c.status === 'changed' && c.rel > 0) posChanged++;
              if (c.status === 'changed' && c.rel < 0) negChanged++;
            }
            return { n: arr.length, status: out, posChanged, negChanged };
          };
          const sample = tally;
          return {
            up: sample(window.__up), down: sample(window.__down),
            same: sample(window.__same),
            closed: dp.compare(window.__closed, 40).status,
          };
        })()
        """
        res = cdp.evaluate(build)

        print(f"  claimable busy edges tested : {res['up']['n'] * 3}")
        print(f"  tripled  -> {res['up']['status']}   (rel>0: {res['up']['posChanged']})")
        print(f"  cut      -> {res['down']['status']} (rel<0: {res['down']['negChanged']})")
        print(f"  untouched-> {res['same']['status']}")
        print(f"  closed edge status : {res['closed']}")
        assert res['up']['posChanged'] > 0, "tripling flow must register as an increase"
        assert res['down']['negChanged'] > 0, "cutting flow must register as a decrease"
        assert res['same']['status'].get('within_noise', 0) > 0, \
            "untouched edges must land inside the seed-noise band"
        assert res["closed"] == "closed", "closed edge must be reported as closed"

        print("TEST 3 — renderer applies the diverging ramp without throwing")
        render = r"""
        (() => {
          window.__styles = [];
          Render.setProvider(window.__dp);
          State.setQI(40);
          const seen = window.__styles.filter(s => typeof s.color === 'string');
          const parse = (c) => {
            const m = /rgb\((\d+),(\d+),(\d+)\)/.exec(c);
            return m ? [ +m[1], +m[2], +m[3] ] : null;
          };
          let greener = 0, redder = 0, neutral = 0, other = 0;
          for (const s of seen) {
            const p = parse(s.color);
            if (!p) { other++; continue; }
            const [r,g,b] = p;
            if (g > r + 20) greener++;
            else if (r > g + 20) redder++;
            else neutral++;
          }
          return { total: seen.length, greener, redder, neutral, other };
        })()
        """
        counts = cdp.evaluate(render)
        print(f"  setStyle calls: {counts['total']}  "
              f"grön={counts['greener']} röd={counts['redder']} "
              f"neutral={counts['neutral']} övrigt={counts['other']}")
        assert counts["total"] > 100, "renderer did not style the network"
        assert counts["greener"] > 0, "no edge was drawn green (decrease)"
        assert counts["redder"] > 0, "no edge was drawn red (increase)"

        print("TEST 4 — playback over the delta provider raises nothing")
        cdp.evaluate("(()=>{for(let q=0;q<96;q+=8){State.setQI(q);}return true})()")
        print("  ok  96 quarters stepped")

        print("TEST 5 — console is clean")
        errors = []
        for ev in cdp.events:
            if ev.get("method") == "Log.entryAdded":
                e = ev["params"]["entry"]
                if e.get("level") == "error":
                    errors.append(e.get("text"))
            if ev.get("method") == "Runtime.exceptionThrown":
                errors.append(json.dumps(ev["params"])[:300])
        real = [e for e in errors if "favicon" not in str(e).lower()]
        for e in real:
            print("  ERROR:", str(e)[:220])
        assert not real, f"{len(real)} console error(s)"
        print("  ok  no console errors")

        print("\nALLA WEBBLÄSARTESTER KLARA")
        return 0
    finally:
        proc.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
