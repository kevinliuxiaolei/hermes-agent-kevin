# Model Routing Implementation Progress

> Goal: implement the reviewed P0 -> P1 -> P2 routing improvements without interrupting running tasks.
> Recovery rule: a replacement model should read `MODEL_ROUTING_STRATEGY.md` and this file before continuing.
> Design version: v4.1.2

## Current Status

- Goal state: completed
- Current phase: completed
- Last updated: 2026-06-12

## Follow-up Goal

- Goal state: completed
- Current phase: completed
- Objective: fix the remaining user-visible routing and messaging issues by making Telegram/CLI status output truthful and quiet, rendering `/model` by effective quota order, and elevating AGY to slot-level state with dual HOME isolation.
- Recovery rule: a replacement model should read `MODEL_ROUTING_STRATEGY.md` and this file before continuing.

## Lifecycle Reliability Goal

- Goal state: completed
- Current phase: completed
- Objective: prevent narration-only false completion, preserve active work when Telegram users send follow-ups, expose truthful task progress and staged-stall diagnostics, route compression through paid/quota-aware fallbacks, and simplify model inspection.
- Recovery rule: read the `Lifecycle Reliability Goal` and `2026-06-11 v3.9` sections before continuing. Do not restart Gateway until focused and broad regressions pass.

## Lifecycle Reliability Plan

1. Reject narration-only assistant output as a successful terminal response and continue/fallback with a bounded budget.
2. Make Telegram follow-up text queue by default; reserve interruption for explicit `/stop`, `/new`, or `/steer`.
3. Expose task phase, executed route, last meaningful activity, route attempt, and elapsed time through status/heartbeat output.
4. Add phase-aware warning/fallback behavior for model wait, ACP silence, tools, and overall inactivity.
5. Route auxiliary compression through the shared quota-aware RoutePlan before free fallback/static markers.
6. Allow read-only `/model`, `/model list`, and `/model current` while busy; keep switching isolated to turn boundaries.
7. Reduce `/model list` to a readable available-route summary, with full diagnostics behind `/model list all`.

## Lifecycle Reliability Acceptance

- A response consisting only of lines such as `I will update...` cannot complete a task.
- A normal Telegram follow-up cannot silently kill active work.
- `/status` and heartbeat identify the current phase and last meaningful activity.
- Compression 503/429 attempts healthy paid routes before free/static fallback.
- Busy sessions can inspect models without switching the active turn.
- Focused tests, broad routing/gateway tests, doctor, and post-restart smoke checks pass.

## Follow-up Plan

1. Unify the displayed state contract so footer and `/model` can distinguish requested, selected, and executed model identities.
2. Make AGY quota and availability slot-aware, exposing the selected HOME slot instead of flattening the two physical accounts into one family badge.
3. Rewrite `/model` and `/model list` to sort by usable quota, remove static recommendation noise, and keep only necessary operational hints.
4. Reduce Telegram/CLI intermediate chatter to the minimum necessary progress signals and add regression tests for the new display contract.

## Decisions

1. Explicit routes default to `pin_with_fallback`; only `strict_pin` disables fallback.
2. Configured fallback entries are policy preferences. They are merged before healthy registry candidates.
3. Every generated fallback chain has one total length limit after merging.
4. Execution keeps receiving the existing legacy fallback dictionaries while RoutePlan adoption is staged.

## Progress Log

### 2026-06-11 - P0 started

- Added `agent/route_plan.py` with shared `RoutePlan` and `build_route_plan`.
- Centralized configured/registry merge, primary-route removal, de-duplication, route modes, and full-chain limits.
- Connected Cron, Gateway, CLI, and auxiliary registry fallback construction to the shared planner.
- Fixed Gateway complete session overrides so they retain a fallback plan instead of returning a primary-only runtime.
- Added shared RoutePlan contract tests.

### 2026-06-11 - P0 completed

- All production entry points now obtain registry fallback candidates through `agent/route_plan.py`.
- Gateway startup auth fallback now merges configured policy entries with registry candidates instead of choosing one source.
- CLI supplies its current primary route to RoutePlan, preventing the primary from being retried as fallback.
- RoutePlan supports `auto`, `pin_with_fallback`, and `strict_pin`.
- RoutePlan enforces the limit after configured and registry candidates are merged.
- Focused verification: `202 passed`; Python compilation and scoped `git diff --check` passed.

### 2026-06-11 - P1 completed

- Added shared runtime circuit breaker in `agent/route_health.py`.
- Error classification writes provider cooldown; successful valid responses clear it.
- Selector candidate construction and execution-time fallback activation both enforce runtime health as a hard gate.
- Fixed `build_fallback_chain` so fallback-only entries cannot exceed the total candidate limit.
- Added `model_selection.max_route_attempts` for the maximum number of distinct routes. `agent.api_max_retries` remains the per-route API retry limit; legacy `max_execute_attempts` is not used as a hard gate because its old value would truncate viable cross-provider recovery.
- Fixed AGY quota lookup to use model-specific quota when trustworthy per-model data exists, falling back to family status only when model data is absent.
- Focused P1 verification: `392 passed`; Python compilation and scoped `git diff --check` passed.

### 2026-06-11 - P2 completed

- Explicit aliases remain pinned while executable and quota-available; low quota alone no longer silently changes the primary.
- Automatic scoring now applies task route-group policy before cross-family quota percentage.
- Registry and runtime provider recognize the installed Hermes AGY bridge without requiring gateway-only environment variables.
- Doctor now validates executable routes for available quota families, RoutePlan invariants, and ordered failure progression.
- Updated live config with `model_selection.max_route_attempts: 6`, preserving one primary plus five fallback routes.
- Doctor result: `PASS`.
- Broader routing regression suite: `289 passed`.

### 2026-06-11 - Real incident verification

- Restarted Gateway successfully; active PID after restart: `2290500`.
- Triggered original Cron job `stock-realtime-300693-interpret` (`3c81eea288d5`).
- Explicit Volcengine Coding Plan primary returned 429 as expected.
- Execution skipped cooled `volcengine-agent-plan` and `gemini` routes.
- Fallback activated `antigravity-acp / models/gemini-flash-lite-latest`.
- Job completed successfully at `2026-06-11 15:46:06 +08:00`; Cron status is `ok`.

### 2026-06-11 - Telegram presentation polish

- Kept Telegram `/model` on a dedicated HTML `<pre>` renderer instead of reusing the CLI diagnostic output.
- Removed raw `quota_family` noise from the compact picker and grouped the quota overview into Codex / Volcengine / AGY / Fallback sections.
- Switched the Telegram `current` action to a compact summary so the inline menu stays aligned and readable.
- Added regression coverage for the compact Telegram menu and current-model view.
- Verification for this UI pass: `python3 -m py_compile agent/model_command.py gateway/platforms/telegram.py gateway/run.py`; `16 passed` in the focused pytest slice.

### 2026-06-11 - Follow-up scope clarified

- Confirmed the next scope is separate from the completed P0 -> P1 -> P2 routing goal.
- Agreed to treat AGY as two physically isolated HOME slots under `bin/agy_acp_bridge.py`, not as a single flattened quota family.
- Agreed to fix footer semantics and `/model` ordering before touching any deeper selector heuristics.

### 2026-06-11 - Follow-up display contract started

- Reworked `agent/model_command.py` so `/model` and `/model list` sort candidates by effective quota rather than static recommendation order.
- Removed the static recommendation block from the Telegram picker and replaced it with a quota-ordered candidate list.
- Changed `/model current` to report requested vs actual selection separately when the selector falls back.
- Extended `gateway.runtime_footer.py` so the runtime footer can show requested vs executed model state instead of a single ambiguous model string.
- Wired `gateway/run.py` to pass requested and executed route identities into the footer builder.
- Tightened Telegram turn handling so interim assistant messages stay off by default unless explicitly enabled per platform.
- Added regression tests covering the new footer semantics and compact model-command output.
- Focused verification after this pass: `34 passed`.

### 2026-06-11 - Follow-up routing and messaging implementation completed

- Added `agent/agy_slot_state.py` and persistent bridge state at `/home/lighthouse/.hermes/state/agy_slots.json`.
- Fixed `bin/agy_acp_bridge.py` so physical HOME-slot cooldown survives bridge process restarts; the bridge now reports the last executed slot and filters ACP process narration.
- Changed narration-only AGY output from a false successful response into an empty-response failure that can activate fallback.
- Made AGY quota status expose both authenticated physical execution slots and hard-gate the AGY family when all slots are cooling down.
- Updated runtime footer and CLI status display to report the actual executed alias and AGY slot, while preserving requested-model identity separately.
- Fixed CLI `/model` switching so the unified command callback accepts the registry contract and rebuilds runtime state.
- Fixed Gateway `/model` session switching so provider/model/CODEX_HOME are retained instead of being overwritten by an alias-only override.
- Rebuilt CLI RoutePlan each turn so changing quota/health state can change the fallback chain without restarting the CLI.
- Reworked `/model` candidate badges to use concrete-model quota and sort equal-quota routes with paid/subscription groups before free fallback.
- Reworked `/model list` into one Chinese, globally quota-ordered message; removed the English registry dump, raw internal status values, and three-message Telegram split.
- Current live inspection reports AGY `2/2 slots ready`; `/model list` is one part and approximately 1.8K characters.
- Follow-up regression suite: `193 passed`; Python compilation and scoped `git diff --check` passed.

### 2026-06-11 - Follow-up final verification and deployment

- `python3 tools/hermes_model_doctor.py`: `DIAGNOSTIC STATUS: PASS`.
- Broad regression: `4156 passed`, with four order-dependent failures in `tests/agent/test_vision_routing_31179.py`; the same file passed independently (`12 passed`), so no routing-goal regression was identified.
- Restarted `hermes-gateway.service`; active PID is `2369018`, service state is `active`, and `NRestarts=0`.
- Post-restart rendering smoke checks:
  - `/model list`: one message, Chinese quota-ordered list, approximately 1.8K characters.
  - Telegram `/model`: HTML `<pre>` renderer active.
  - runtime footer: actual AGY slot renders as `gemini-low@secondary`.
  - AGY physical slots: `2/2 slots ready`.

### 2026-06-11 - Lifecycle reliability implementation completed

- Made narration-only terminal detection provider-independent. Each route receives one bounded continuation; repeated narration advances to the next RoutePlan and ultimately returns an explicit failure instead of false completion.
- Expanded AGY bridge narration filtering as defense in depth while keeping the runtime terminal guard authoritative.
- Changed Telegram normal follow-up text to queue by default even when the global busy-input mode is `interrupt`; explicit control commands remain the interruption boundary.
- Allowed busy sessions to run read-only `/model`, `/model list`, and `/model current` commands while rejecting route-changing model switches.
- Added runtime activity phases, executed route/attempt state, last meaningful activity, phase-aware heartbeat text, and staged inactivity warnings.
- Routed compression/title/web auxiliary capacity failures through the registry RoutePlan before configured/main/free fallback.
- Reduced default `/model list` to one representative per available quota family; `/model list all` retains the complete diagnostic alias list.
- Focused lifecycle/routing regression: `411 passed`.
- Final targeted regression after Codex continuation test-contract update: `67 passed`.
- Python compilation, scoped `git diff --check`, and `tools/hermes_model_doctor.py` passed.
- Broad agent/gateway/run-agent suite: `11774 passed`, `78 skipped`, `42 failed`. Two stale Codex continuation tests were updated and pass in isolation. Remaining failures are outside this goal and reproduce in isolated legacy-contract suites, including the old Telegram provider picker, non-registry `/model` persistence, Matrix, vision, and model-metadata expectations.

### 2026-06-11 - Lifecycle reliability deployment verified

- Final goal-related regression suite: `136 passed`.
- Default `/model list` smoke output: 15 lines / 620 characters; `/model list all`: 38 lines / 2662 characters.
- Restarted `hermes-gateway.service`; new active PID is `2393638`, `NRestarts=0`, `ExecMainStatus=0`.
- The replaced Gateway process logged exit status 1 during the requested systemd stop, but the new process started and remained active; this is recorded as stop-path debt rather than a deployment failure.
- Post-restart `tools/hermes_model_doctor.py`: `DIAGNOSTIC STATUS: PASS`.
- Post-restart RoutePlan still provides six ordered routes and keeps healthy paid/subscription routes before final free fallbacks.
- Scoped `git diff --check` passed.


### Deployment and Post-Review Fixes (2026-06-15 v4.1.1)

- **Fix**: [Sync Docs] Add automated synchronization script for routing strategies
- **Fix**: [Telegram] Fix checkmark display and implement registry-backed inline keyboard picker for auto-routing mode.


### Deployment and Post-Review Fixes (2026-06-15 v4.1.2)

- **Fix**: [Sync Docs] Add automated synchronization script for routing strategies
- **Fix**: [Telegram] Fix checkmark display and implement registry-backed inline keyboard picker for auto-routing mode.

## Remaining Work

- No required work remains for the approved P0 -> P1 -> P2 goal.
- Future optional cleanup: remove the legacy `max_execute_attempts` config key after downstream consumers no longer reference it.
- No required work remains for the approved follow-up goal.
- Known unrelated test-suite issue: four vision-routing tests can fail after the full `tests/agent` order but pass as an isolated file.
- No required work remains for the lifecycle reliability goal.
- Legacy compatibility debt remains for custom/non-registry `/model` switching and the old Telegram provider picker test contract; neither path is part of the unified registry UI.
- Gateway graceful-stop debt: the replaced process can log exit status 1 during a requested systemd restart even though the replacement starts successfully.

## Verification

- P0 focused suite: `202 passed in 18.64s`.
- P1 focused suite: `392 passed in 35.12s`.
- P2/broader routing suite: `289 passed in 43.89s`.
- `python3 -m py_compile` passed for changed routing modules.
- Scoped `git diff --check` passed.
- `python tools/hermes_model_doctor.py`: `DIAGNOSTIC STATUS: PASS`.
- Real Cron route recovery: `3c81eea288d5` completed successfully through Antigravity fallback.
- Follow-up routing/UI regression suite: `193 passed in 11.53s`.
- Broad follow-up suite: `4156 passed, 4 order-dependent failures`; isolated failed file: `12 passed`.
- Follow-up doctor: `DIAGNOSTIC STATUS: PASS`.
- Gateway restart smoke: active PID `2369018`, `NRestarts=0`.
- Lifecycle focused regression: `411 passed in 54.48s`.
- Lifecycle final targeted regression: `67 passed in 8.69s`.
- Lifecycle broad regression: `11774 passed, 78 skipped, 42 non-goal failures`.
- Lifecycle doctor: `DIAGNOSTIC STATUS: PASS`.
- Lifecycle final regression: `136 passed in 30.28s`.
- Lifecycle deployment smoke: active PID `2393638`, `NRestarts=0`, `ExecMainStatus=0`.

## 2026-06-12 - Post-review hardening goal

### Goal

Close the remaining runtime-routing gaps found during the June 12 review without
restarting or signalling the currently running Gateway/Cron process.

### Runtime safety boundary

- Do not start, stop, restart, reload, or signal the running Gateway process.
- Do not mutate live Cron jobs or their schedule/state database.
- Source edits and isolated tests only; deployment remains a separate,
  explicitly approved step.
- Baseline process: PID `2393638`, started `2026-06-11 21:27:32 CST`.
- `hermes-gateway.service` is not currently installed/visible to systemd, but
  the Gateway process remains alive and is the active Cron host.

### Planned work

- [x] Keep auth-failed routes disabled until explicit recovery instead of
  retrying invalid credentials after a fixed cooldown.
- [x] Make Telegram model-picker buttons use the same quota/health-ranked
  candidates as the `/model` text.
- [x] Preserve concrete provider identity for runtime-health recording and
  prevent unhealthy configured routes from consuming bounded RoutePlan slots.
- [x] Add useful auxiliary/compression fallback-route telemetry.
- [x] Run focused regressions and record results.

### Current deployment state

- The running Gateway predates late June 11 source edits.
- No changes made in this goal are loaded into the running process until a
  separately approved deployment.

### Implementation notes

- Authentication failures now remain blocked after cooldown expiry. Successful
  runtime execution or a successful `hermes auth` Codex login explicitly clears
  the block.
- Generic `custom` Volcengine routes are normalized to Agent Plan or Coding
  Plan using the runtime endpoint/model before route-health mutation.
- Configured fallback routes blocked by runtime health are skipped before the
  bounded RoutePlan slot limit is applied.
- Telegram's registry keyboard now contains only the current alias plus a
  compact quota/health-ranked executable candidate set.
- Compression registry fallback emits its RoutePlan candidates/skips at info
  level and emits an exhaustion warning with attempted routes.

### Verification and runtime status

- Focused routing/error-classifier suite: `166 passed`.
- Expanded routing/Telegram/auxiliary suite: `389 passed`.
- Final focused suite including Codex authentication: `414 passed`.
- Broad routing/CLI/Gateway/Cron contract suite: `365 passed`.
- Error-classifier and auxiliary fallback suite: `363 passed`.
- Python compilation passed for all touched runtime modules.
- Scoped `git diff --check` passed, excluding pre-existing whitespace debt in
  `agent/error_classifier.py`.
- Route-health reads are now isolated during tests unless a test explicitly
  supplies `HERMES_ROUTE_HEALTH_STATE_FILE`; this prevents workstation health
  state from changing deterministic selector/Cron expectations.
- Gateway/Cron process remained PID `2393638` throughout implementation.
- No service command, process signal, Cron mutation, or deployment action was
  performed.

### Pending deployment

- Source changes are complete but intentionally not loaded into the running
  Gateway/Cron process.
- Before deployment, confirm a maintenance window and restore/install the
  missing `hermes-gateway.service` unit or choose an explicit supervised
  replacement procedure.
