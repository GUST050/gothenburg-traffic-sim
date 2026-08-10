# Tasks

Only the marked `WORKFLOW_CONTROL` and `ACTIVE_TASK` blocks describe the
current focus. The remaining task ledger is historical context; old Sol/Luna
owners, states and approval formulas are not active workflow rules. See
`AGENTS.md` for the flexible actor protocol.

<!-- WORKFLOW_CONTROL_START -->
## WORKFLOW_CONTROL

- Mode: `FLEXIBLE — roles are capabilities, not model identities`
- Current focus: `Full annual warming plus a simpler professional desktop UI`
- Status: `IN_PROGRESS. Plan 38d91d22… is running under the supervisor with
  three state-workers and demand prefetch. At the latest bound check 20,145 of
  104,685 units had succeeded, none had failed, and live SUMO plus demand
  processes were confirmed. The web shell now uses a neutral graphite/blue
  desktop palette, solid surfaces, restrained corners/shadows and text labels
  instead of decorative purple gradients, glows and emoji controls.`
- Suggested next action: `Keep monitoring warming without changing plan-bound
  demand inputs. Review the desktop UI in a real browser when available, then
  commit and push the isolated web/test change if the visual result is accepted.`
- Eligible actors: `Any model or person; no model-specific gate`
- Safety boundary: `Do not use held observations in pool, picker or certificate.
  Do not weaken TAG/fit/provenance gates or promote pilot artifacts as release
  evidence. Do not edit annual plan-bound inputs while warming is active.`
- Updated: `plan 38d91d22… running; 20,145/104,685 succeeded and 0 failed at
  latest check; desktop UI change passes 114 server/UI integration tests and
  JavaScript syntax checks / 2026-08-10`
<!-- WORKFLOW_CONTROL_END -->

<!-- ACTIVE_TASK_START -->
## ACTIVE_TASK

### UI-DESKTOP-V1 — Professional desktop shell during annual warming

- Status: `IMPLEMENTED; browser visual review pending`
- Objective and scope: `Simplify the existing web UI for desktop use without
  touching annual warming inputs, simulation contracts or generated evidence.`
- Completion outcome: `The home screen and analysis controls use one restrained
  blue accent, neutral graphite/white surfaces, reduced rounding and shadows,
  no decorative purple gradients/glows and no emoji action labels. The home
  grid is explicitly desktop-only at a 1,180 px minimum width.`
- Context or checkpoints: `The in-app browser backend was unavailable, so the
  change was verified from source, JavaScript syntax and integration tests;
  final visual review still requires a normal desktop browser.`
- Primary files: `web/index.html, web/controls.js, web/app.js,
  tests/test_serve.py`
- Constraints and safety: `Preserve semantic data colours on the map and status
  warnings. Do not alter web/data, SUMO demand files or warming plan identity.`
- Acceptance criteria: `Professional palette contract is tested; desktop-only
  layout is explicit; no old purple/glow tokens or decorative action emoji
  remain; existing server/UI integration behaviour remains green.`
- Useful checks: `node --check web/app.js; node --check web/controls.js;
  pytest -q tests/test_serve.py (114 passed); git diff --check.`
<!-- ACTIVE_TASK_END -->

## History

Everything before 2026-08-06 lives in `docs/history/TASKS_history.md`
(7,234 lines). It is preserved context only —
per `AGENTS.md`, nothing outside the marked blocks above is current.
