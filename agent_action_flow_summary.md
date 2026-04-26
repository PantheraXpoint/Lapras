# Agent-Side Action & Timing Summary

Scope: rule-driven actions, threshold/sensor config, and the state agents publish.
Treats `context_rule_manager.py` as opaque. Sensor-decision logic for `proximity_status` /
`motion_status` is excluded except where a threshold change flips the published booleans.

---

## 1. MQTT Surface Area (agent-side only)

All three agents inherit `VirtualAgent` ([virtual_agent.py:13](lapras_middleware/virtual_agent.py#L13))
and share a common subscribe/publish skeleton, with per-agent additions.

### Common to all (from `VirtualAgent._on_connect`, [virtual_agent.py:44-70](lapras_middleware/virtual_agent.py#L44-L70))

**Subscribes:**
- `TopicManager.context_to_virtual_action(agent_id)` — receives `applyAction` events.
  Payload fields read: `actionName`, `parameters` (varies per action). Handler:
  `_handle_action_command` → `execute_action` ([virtual_agent.py:387-436](lapras_middleware/virtual_agent.py#L387-L436)).
- `TopicManager.sensor_config_command(agent_id)` — receives `sensorConfig` events.
  Payload fields read: `target_agent_id`, `action` (`configure`/`add`/`remove`/`list`),
  `sensor_config`. Handler: `_handle_sensor_config_command` ([virtual_agent.py:148-186](lapras_middleware/virtual_agent.py#L148-L186)).

**Publishes:**
- `TopicManager.virtual_to_context_report(agent_id)` — `actionReport` events.
  Emitted at end of `_handle_action_command`. Payload: `command_id` (echoes the inbound
  applyAction id), `success`, `message`, `new_state`.
- `TopicManager.virtual_to_context(agent_id)` — `updateContext` events.
  Emitted from `_publish_context_update_with_data` ([virtual_agent.py:498-537](lapras_middleware/virtual_agent.py#L498-L537)),
  driven by `_check_and_publish_state_change` whenever `local_state` differs from the
  last published snapshot. Payload: `state` (full `local_state`), `sensors` (sanitized
  per-sensor record). Queued through a worker thread to avoid MQTT reentrancy.
- `TopicManager.sensor_config_result(agent_id)` — `sensorConfigResult` events.
  Payload: `command_id` (echo), `success`, `message`, `action`, `current_sensors`.

### ClubHouseAgent extras

No threshold subscription. Only the common surface area applies.
Action topic accepts `actionName` ∈ {`turn_on`, `turn_off`, `change_mode`}; parameters
read in `execute_action` ([clubhouse_agent.py:1126-1251](lapras_agents/clubhouse_agent.py#L1126-L1251)):
`parameters.target` ("light"/"aircon"/"both"), `parameters.light_ids` (list, optional),
`parameters.mode` (for change_mode).

### AirconAgent extras

**Adds subscription** ([aircon_agent.py:733-740](lapras_agents/aircon_agent.py#L733-L740)):
- `TopicManager.threshold_config_command(agent_id)` — receives `thresholdConfig` events.
  Payload fields read: `threshold_type` (must be `"temperature"`),
  `config["threshold"]` (float, validated 0–50 °C). Handler:
  `_handle_threshold_config_message` → `_process_threshold_config`
  ([aircon_agent.py:755-825](lapras_agents/aircon_agent.py#L755-L825)).

**Adds publication**:
- `TopicManager.threshold_config_result(agent_id)` — `thresholdConfigResult`.
  Payload: `command_id`, `success`, `message`, `threshold_type`, `current_config`.
  Emitted from `_publish_threshold_config_result`.

Action topic accepts `actionName` ∈ {`turn_on`, `turn_off`, `change_mode`}; parameters:
`skip_verification` (bool), `mode` (for change_mode — accepts `normal`/`eco`/`boost`,
purely scaffold).

### LightHueAgent extras

**Adds subscription**:
- `TopicManager.threshold_config_command(agent_id)` — `thresholdConfig` events with
  `threshold_type == "light"`. Handler: `_process_threshold_config`
  ([light_hue_agent.py:794-883](lapras_agents/light_hue_agent.py#L794-L883)).
  Payload validated: `threshold` must be positive float.

**Adds publication**:
- `TopicManager.threshold_config_result(agent_id)` — same shape as Aircon's.

Action topic accepts `actionName` ∈ {`turn_on`, `turn_off`, `change_mode`}; parameters:
`light_ids` (optional list), `skip_verification`, `mode`.

---

## 2. Action-Handling Flow

### ClubHouseAgent

#### `turn_on` / `turn_off` ([clubhouse_agent.py:1131-1228](lapras_agents/clubhouse_agent.py#L1131-L1228))

Reads `parameters.target` (default `"both"`) and `parameters.light_ids` (optional).
Builds two closures `_light_on()` / `_light_off()` that pick the per-id reconciler
when `light_ids` is present, else the group path.

| target  | light path                                | aircon path                                 |
|---------|-------------------------------------------|---------------------------------------------|
| `both`  | `_light_on/off()` then aircon             | `__turn_on_aircon` / `__turn_off_aircon`    |
| `light` | `_light_on/off()` only                    | (skipped)                                   |
| `aircon`| (skipped)                                 | `__turn_on_aircon` / `__turn_off_aircon`    |

After each subsystem call, `local_state["light_power"]` and `local_state["aircon_power"]`
are updated from the sub-result's `new_state`. Combined `power`:
- both `on` → `"on"`
- both `off` → `"off"`
- otherwise → `"partial"`

`new_state` reported back to CM contains: `power`, `light_power`, `aircon_power`.
A `_trigger_state_publication()` call always follows, pushing an `updateContext` event.

##### Light sub-paths

- **No `light_ids` (group path):** `__turn_on_lights` ensures the named Hue group
  (`back`/`front`/`all`) exists via `__ensure_group_exists`, then issues a single
  `PUT /groups/{id}/action` with `self.light_settings` (preset bri/hue/sat plus
  `on:True`). Returns `light_power: "on"`. Off path: `PUT {"on": False}` to the group.

- **With `light_ids` (`__turn_on_lights_by_ids`, [clubhouse_agent.py:701-761](lapras_agents/clubhouse_agent.py#L701-L761)):**
  1. Compute `excluded = KNOWN_HUE_LIGHTS - target_set`.
     `KNOWN_HUE_LIGHTS = ("1","2","3","5","6","7","8","10")`.
  2. **Exclude pass** — for each excluded id, `PUT /lights/{id}/state {"on": False}`
     sequentially.
  3. **Target pass** — for each id in `light_ids`, `PUT /lights/{id}/state` with
     `light_settings ∪ {effect:"none", transitiontime:0}`.
  4. Tally `successes` / `failures`. Returns:
     - all succeed → `light_power: "on"`
     - any succeed but some fail → `light_power: "partial"`
     - all fail → `light_power: "off"`

  `__turn_off_lights_by_ids` is symmetric but only does the off-pass, no exclusions,
  no `light_settings`.

##### Aircon sub-paths

- **Single-device modes (`back`/`front`):** `_execute_ir_command("on")` for the
  preset `ir_device_serial`, then `time.sleep(0.5)`, then
  `_execute_ir_command(self.aircon_temp_command)` (`temp_up` or `temp_down`).
  Returns combined success and `aircon_power: "on"`.
- **`all-normal`:** `_execute_multiple_ir_commands("on")` against
  `ir_device_serials = [164793, 322207]` sequentially. `aircon_power: "on"` if all
  succeed, else `"partial"`.
- **`all-clean`:** `aircon_mode == "both_off"` short-circuit: returns
  `success=True, aircon_power: "off"` without invoking IR. (Guard #2 in §3.)
- **Off path:** mirrors above without the temp follow-up.

#### `change_mode` ([clubhouse_agent.py:1026-1124](lapras_agents/clubhouse_agent.py#L1026-L1124))

1. Extract `(position, current_mode)`. If `new_mode == current_mode`, **short-circuit**
   (Guard #1 in §3): return success without reapplying profile, so a subsequent
   `turn_on` with `light_ids` drives lighting cleanly. `new_state` includes only mode
   metadata (preset_mode, light profile fields).
2. Validate position/mode pairing:
   - `position == "all"` requires `mode ∈ {"normal","clean"}`.
   - `position ∈ {"back","front"}` requires `mode ∈ {"nap","read"}`.
3. Update `self.preset_mode = f"{position}-{new_mode}"` and call
   `_configure_preset_settings()` to refresh `light_group`, `light_settings`,
   `ir_device_serial(s)`, `aircon_temp_command` / `aircon_mode`.
4. Update `local_state` with the new mode-state fields (preset_mode, mode_name,
   light_profile_*, aircon_profile_*).
5. **Re-apply profile only if currently powered:** if any of `power`/`light_power`/
   `aircon_power` is `"on"`, call `__turn_on_lights()` and `__turn_on_aircon()`,
   merge their `new_state` into the response. This path uses the *group* call —
   no `light_ids` reconciliation.

### AirconAgent

#### `turn_on` / `turn_off` ([aircon_agent.py:611-689](lapras_agents/aircon_agent.py#L611-L689))

Calls `__turn_on_aircon` / `__turn_off_aircon`, which run `_execute_ir_command`
on **both** `ir_device_serials = [322207, 164793]` sequentially. No verification
loop — `local_state["power"]` is set unconditionally to the requested value.
`new_state.power` is also forced to `"on"`/`"off"`. `_trigger_state_publication` is
always called.

`parameters.skip_verification` only changes the message text; the agent has no
real verification path anyway.

#### `change_mode`

`_change_mode` ([aircon_agent.py:588-609](lapras_agents/aircon_agent.py#L588-L609))
is metadata-only: validates `mode ∈ {"normal","eco","boost"}`, updates
`self.current_mode` and `local_state["mode_name"]`. No IR or hardware effect.

### LightHueAgent

#### `turn_on` / `turn_off` ([light_hue_agent.py:619-734](lapras_agents/light_hue_agent.py#L619-L734))

- Reads `parameters.light_ids` (optional). When absent, `DEFAULT_LIGHT_IDS =
  ["1","2","3","5","6","7","8","10"]` is used.
- `__turn_on_light(ids)` / `__turn_off_light(ids)` issue a sequential
  `PUT /lights/{id}/state {"on": True/False}` per id. No `light_settings` are
  applied here; this is plain on/off.
- **No exclude pass** in this agent. Lights outside the target set are left as-is.
- After the PUTs, if `skip_verification=False`, runs `__verify_action_result`
  (loops up to `max_retries=2`, sleeping `retry_delay=0.2s` then re-reading
  light state) and overrides `success`/`new_state.power` with verified state.
- Always calls `_trigger_state_publication`.

`new_state.power`:
- all PUTs succeeded → `"on"` / `"off"`
- partial — verification absent: `"on" if successes else "off"`
- partial — verification present: whatever majority-vote returns from
  `__check_current_light_state`

#### `change_mode`

`_change_mode` ([light_hue_agent.py:596-617](lapras_agents/light_hue_agent.py#L596-L617))
is metadata-only: `mode ∈ {"normal","focus","relax"}`, updates `self.current_mode`
and `local_state["mode_name"]`. No Hue effect.

### Threshold-Config Flow (Aircon, Light)

When a `thresholdConfig` event arrives:

1. Decoded by `_handle_threshold_config_message` → `_process_threshold_config`.
2. `threshold_type` must match the agent (`"temperature"` for aircon, `"light"` for
   hue). Mismatch → result event with `success=False`, no state change.
3. Validate `config["threshold"]`. Aircon: 0 ≤ x ≤ 50. Light: x > 0.
4. Under `state_lock`, mutate:
   - Aircon: `temperature_threshold_config["threshold"]` and `["last_update"]`,
     and `local_state["temp_threshold"]`.
   - Light: `light_threshold_config["threshold"]` / `["last_update"]`,
     `local_state["light_threshold"]`.
5. Re-evaluate the *most recent* sensor reading immediately:
   - Aircon: `_reevaluate_temperature_status` → `_classify_temperature_status` flips
     `local_state["temperature_status"]` to `"hot"` / `"cool"` based on the new
     threshold.
   - Light: `_reevaluate_light_status` flips `local_state["light_status"]` to
     `"bright"` / `"dark"`.
6. `_trigger_state_publication()` → next `updateContext` carries the new threshold
   *and* the (possibly flipped) status booleans.
7. `_publish_threshold_config_result` emits the result event.

**Threshold-change → published-state link:** the boolean fields the CM consumes
(`temperature_status`, `light_status`) are not direct sensor values; they are
threshold-derived classifications. A threshold update can therefore flip a
`local_state` value with no fresh sensor input, producing a new `updateContext`
event purely as a side effect of the `thresholdConfig` command.

(`activity_detected` is *not* threshold-controlled in any agent — it is derived
from `proximity_status`/`motion_status`/`activity_status`.)

### Sensor-Config Flow

`_handle_sensor_config_command` ([virtual_agent.py:148-186](lapras_middleware/virtual_agent.py#L148-L186))
handles `action ∈ {configure, add, remove, list}`:

- `configure`: unsubscribe all current sensor topics, clear `sensor_data`, then
  subscribe new ones; calls `_update_sensor_config(new, action="configure")`.
- `add`/`remove`: incremental.
- After config changes, the agent's per-type `_process_*_sensor` methods only see
  inputs from the new sensor set, so `proximity_status` / `motion_status` /
  `activity_status` are recomputed from a smaller or larger pool. `activity_detected`
  flips accordingly on the next sensor message.

A `sensorConfigResult` is always emitted with `current_sensors`.

---

## 3. State-Machine Self-Gating Inside Agents

| # | Guard | Where | Condition | Effect |
|---|-------|-------|-----------|--------|
| 1 | Same-mode short-circuit | `_change_mode` ([clubhouse_agent.py:1040-1050](lapras_agents/clubhouse_agent.py#L1040-L1050)) | `new_mode == current_mode` | Skips `_configure_preset_settings`, skips `__turn_on_lights()` / `__turn_on_aircon()` reapply. Returns success with mode-state-only `new_state`. Lets the next rule's `light_ids` drive lighting via the per-id reconciler. |
| 2 | Clean-mode aircon block | `__turn_on_aircon` ([clubhouse_agent.py:861-867](lapras_agents/clubhouse_agent.py#L861-L867)) | `aircon_mode == "both_off"` (i.e. `all-clean` preset) | Returns `success=True, aircon_power: "off"` without issuing any IR command. `turn_on` with `target="both"` therefore lights up while AC stays off. |
| 3 | Power-state guard on profile reapply | `_change_mode` ([clubhouse_agent.py:1078-1095](lapras_agents/clubhouse_agent.py#L1078-L1095)) | None of `power`/`light_power`/`aircon_power` is `"on"` | `__turn_on_lights()` / `__turn_on_aircon()` are NOT called. Mode metadata is updated but no hardware effect. |
| — | "Already on/off" early returns inside `__turn_on_lights`, `__turn_off_lights`, `__turn_on_aircon`, `__turn_off_aircon` | n/a | none exist | These methods always call the bridge / IR script; there is no idempotency guard at the per-method layer. Repeated `turn_on` issues fresh PUTs/IR commands every time. |

### Per-light failure mapping

`__turn_on_lights_by_ids` ([clubhouse_agent.py:745-761](lapras_agents/clubhouse_agent.py#L745-L761)):

| Outcome                  | `light_power` |
|--------------------------|---------------|
| no failures              | `"on"`        |
| any successes + failures | `"partial"`   |
| all failures             | `"off"`       |

`__turn_off_lights_by_ids` ([clubhouse_agent.py:778-795](lapras_agents/clubhouse_agent.py#L778-L795)):

| Outcome                  | `light_power` |
|--------------------------|---------------|
| no failures              | `"off"`       |
| any successes + failures | `"partial"`   |
| all failures             | `"on"`        |

LightHueAgent's `__turn_on_light` / `__turn_off_light` use a simpler scheme
(`"on" if successes else "off"` for partial); verification can override.

`_execute_multiple_ir_commands` ([clubhouse_agent.py:958-967](lapras_agents/clubhouse_agent.py#L958-L967)):
all succeed → `"on"`/`"off"`; any failure → `"partial"`.

### Combined `power` derivation (ClubHouseAgent, [clubhouse_agent.py:1175-1187](lapras_agents/clubhouse_agent.py#L1175-L1187))

```
light_power == "on"  AND aircon_power == "on"  → power = "on"
light_power == "off" AND aircon_power == "off" → power = "off"
otherwise                                       → power = "partial"
```

So `light_power = "partial"` (per-light reconciler had a mix) plus
`aircon_power = "on"` always yields `power = "partial"` — the CM cannot tell
"some lights failed" from "lights on, AC off."

---

## 4. Timing Inventory (Agent Side)

| Name | Seconds | Trigger | Gates |
|------|---------|---------|-------|
| `transmission_interval` (ClubHouseAgent) | 0.5 (default ctor arg) | sensor update via `_schedule_transmission` | minimum spacing between sensor-driven `updateContext` publications |
| `transmission_interval` (AirconAgent) | 0.5 (default) | same | same |
| `transmission_interval` (LightHueAgent) | 0.1 (default ctor arg, [light_hue_agent.py:27](lapras_agents/light_hue_agent.py#L27)) | same | same |
| Aircon ON → temp adjust gap | 0.5 | inside `__turn_on_aircon` ([clubhouse_agent.py:879](lapras_agents/clubhouse_agent.py#L879)) | sequential delay between `_execute_ir_command("on")` and `_execute_ir_command(self.aircon_temp_command)` for back/front presets |
| `_trigger_initial_state_publication` sleep | 0.5 | end of `__init__` for all three agents ([clubhouse_agent.py:1263](lapras_agents/clubhouse_agent.py#L1263), [aircon_agent.py:701](lapras_agents/aircon_agent.py#L701), [light_hue_agent.py:741](lapras_agents/light_hue_agent.py#L741)) | wait for MQTT `_on_connect` before first publication |
| `VirtualAgent` deferred initial publish | 1.0 | `threading.Timer` in base ctor ([virtual_agent.py:42](lapras_middleware/virtual_agent.py#L42)) | first `_publish_initial_state` |
| Light verify retry delay | 0.2 (`retry_delay`) | `__verify_action_result` ([light_hue_agent.py:564-594](lapras_agents/light_hue_agent.py#L564-L594)) | sleep before each of `max_retries=2` re-reads of bridge state after `turn_on`/`turn_off` |
| Light verify max retries | 2 attempts × 0.2s = up to 0.4s plus N × ~5s GETs | per `turn_on`/`turn_off` when `skip_verification=False` | extends apparent action duration before `actionReport` is emitted |
| Hue GET timeout (group lookup, light state read) | 5 (`urllib.urlopen(timeout=5)`) | `__get_group_id_by_name`, `_test_light_controller`, `__check_current_light_state` per-light loop | per-call ceiling on bridge reads |
| Hue PUT timeout (group/light state writes) | 10 | `__turn_on_lights`, `__turn_off_lights`, `__turn_on_lights_by_ids`, `__turn_off_lights_by_ids`, `__turn_on_light`, `__turn_off_light`, `__ensure_group_exists` POST | per-PUT ceiling on bridge writes |
| Hue `transitiontime` | 0 (deciseconds, hardware-side) | `__turn_on_lights_by_ids` payload field ([clubhouse_agent.py:729](lapras_agents/clubhouse_agent.py#L729)) | bridge applies the new color/brightness immediately rather than fading |
| `effect: "none"` | (instant, hardware-side) | same payload | cancels prior `colorloop` / `alert` effect so subsequent hue/sat lands cleanly |
| IR script `--help` probe | 5 (`subprocess.run(timeout=5)`) | `_test_ir_controller` at init | startup smoke test |
| IR command timeout | 10 (`subprocess.run(timeout=10)`) | `_execute_ir_command`, `_execute_multiple_ir_commands` | per-IR-command ceiling |
| Periodic perception state log | 10 | `perception` ([clubhouse_agent.py:569-572](lapras_agents/clubhouse_agent.py#L569-L572) and analogues) | how often the agent's full `local_state` is logged |
| Heartbeat (base `_publish_loop`) | 30 | `VirtualAgent._publish_loop` | currently a no-op `time.sleep` cycle; no heartbeat is actually emitted |
| Command-id memory cap | 100 entries (50 retained on overflow) | `_handle_action_command` ([virtual_agent.py:402-406](lapras_middleware/virtual_agent.py#L402-L406)) | duplicate-command suppression window |

There is no per-light-PUT delay other than each call's own urllib round-trip latency.

---

## 5. Per-Light Reconciliation Timing

For `__turn_on_lights_by_ids([1,2,5,7,8,10])` ([clubhouse_agent.py:701-761](lapras_agents/clubhouse_agent.py#L701-L761)):

**Order of bridge calls (all sequential, single-threaded):**

1. **Exclude pass** (lights in `KNOWN_HUE_LIGHTS = (1,2,3,5,6,7,8,10)` not in target):
   excluded = `[3, 6]`. For each, `PUT /lights/{id}/state {"on": False}` —
   urllib timeout 10s each.
2. **Target pass** in iteration order over the input list `[1,2,5,7,8,10]`. For each,
   `PUT /lights/{id}/state` with `light_settings ∪ {effect:"none", transitiontime:0}` —
   urllib timeout 10s each.

**No parallelism.** Each PUT awaits the previous response (`response.read()` is called
synchronously). Worst-case wall time:
- Exclude pass: 2 × 10s = 20s
- Target pass: 6 × 10s = 60s
- **Total worst case: 80 seconds** (2 excluded + 6 targets, 10s each).
- Typical Hue LAN latency: ~50–200 ms per PUT, so realistic: ~0.4–1.6s total.

For the worst case `__turn_on_lights_by_ids` on all 8 known lights:
0 excluded + 8 targets = 80s ceiling. For the symmetric off path with 8 ids: 80s.

**Mid-flight risk:** Between the start of the exclude pass and the end of the target
pass, the agent is single-threaded inside `execute_action`, holding no locks but also
not draining the MQTT inbound queue for action commands (paho-mqtt callbacks are
serialized on the network thread; `_handle_action_command` blocks that thread for the
duration of `execute_action`). If the CM publishes a *new* `applyAction` during this
window, it queues at the broker and is processed only after the current call returns.

If the Hue bridge stalls or returns slowly:
- `actionReport` for the in-flight command is delayed by up to (excluded + target) × 10s.
- Subsequent action commands wait at the broker.
- The next `change_mode` or `turn_on(light_ids=…)` is then applied on top of state the
  CM saw asynchronously via earlier `updateContext` publications, not the in-flight
  changes (which haven't been reported yet).

---

## 6. Black-Box Assumptions About the CM

Inferred from how each agent treats inbound action and config commands:

1. **The CM serializes per-agent actions and waits for `actionReport` before
   sending the next.** The agents have no internal queue, no in-flight flag, no
   "already executing" guard. Multiple overlapping `applyAction` events would all
   start executing on the MQTT callback thread (serially within paho, but with no
   reasoning about ordering against in-flight Hue/IR work).
2. **The CM reads `actionReport.new_state` as authoritative.** The agents update
   `local_state` *before* `_trigger_state_publication` fires, so the next
   `updateContext` agrees with the report; the CM can rely on either signal.
3. **The CM doesn't replay `command_id`s.** The duplicate-suppression window in
   `_handle_action_command` only keeps the last 100 ids; an old id reused after
   eviction would re-execute.
4. **The CM treats `light_power == "partial"` and `power == "partial"` as
   transient/observational, not as a request to retry.** Agents never auto-retry;
   the bad lights stay bad until a new command arrives.
5. **The CM uses `light_ids` as a complete spec for the desired ON set, on each
   command.** ClubHouseAgent's exclude pass implements exactly this contract:
   "this list ON, every other known light OFF." LightHueAgent does not — it
   silently leaves non-listed lights in their current state.
6. **The CM does not assume `change_mode` repaints lights when already in that
   mode.** The same-mode short-circuit (Guard #1) is explicit about deferring
   lighting to a follow-up `turn_on(light_ids=…)`.
7. **The CM tolerates the threshold-derived booleans flipping without a fresh
   sensor reading.** Threshold changes are followed immediately by a re-classified
   `updateContext`, so the CM must accept that `temperature_status` /
   `light_status` can change purely from a `thresholdConfig` event.

**Possible mismatches with these assumptions:**

- If the CM sends rapid back-to-back `turn_on` events with different `light_ids`,
  the *first* may still be inside its exclude/target pass when the second is
  dequeued. Because paho serializes callbacks, the second simply waits — but if
  the first is slow (Hue stall), the user-visible delay can be long, and the
  second's exclude pass will run on top of whatever partial state the first left.
- If the CM does not wait for `actionReport` and instead acts on `updateContext`,
  it can issue commands based on stale `local_state` (an earlier `change_mode`
  applied profile metadata before any hardware effect).
- If the CM reuses `command_id` values after the 50-id eviction, those commands
  are silently dropped as duplicates.

---

## 7. Race Conditions and Ordering Hazards (Agent-Side View)

### A. Two `change_mode` actions arrive close together

- Both run through `_change_mode` sequentially on the MQTT callback thread.
- The first updates `preset_mode` and (if powered on) calls `__turn_on_lights()` /
  `__turn_on_aircon()` using the group path.
- The second sees the first's `preset_mode` as its baseline and may short-circuit
  via Guard #1 if both happen to map to the same `mode`.
- **Bad outcome:** if mode A and mode B share the same `mode` (e.g. both "clean")
  but rule wiring expects different `light_ids` later, the second `change_mode`
  silently no-ops, and the rule subset never lands until the next `turn_on`.

### B. `turn_on(light_ids=[A,B,C])` arrives mid-flight during prior `turn_on(light_ids=[A,B,D])`

- paho serializes callbacks, so the second waits until the first returns. But the
  first may have *already* turned D ON (target pass) and not yet started any
  exclude pass. The second then begins its exclude pass treating D as in
  `KNOWN_HUE_LIGHTS \ {A,B,C}` and turns D OFF — correct end state, but with a
  visible flicker on D.
- If the second was ordered *opposite* — exclude pass first, target pass second
  — the agent will briefly turn ON A, B, C and OFF the others, then immediately
  apply the next command. Since both passes run inside one `execute_action`, the
  intermediate "D briefly ON" is unavoidable when D was already ON.
- **Bad outcome:** "ghost light" — D flickers ON during the first command's
  target pass, then OFF in the second's exclude pass. Visible as a brief flash on
  the failing light.

### C. `change_mode` arrives while `__turn_on_lights_by_ids` is iterating

- Cannot interleave on the same paho thread. The `change_mode` waits until
  the per-id loop finishes, then runs.
- If `change_mode` Guard #1 short-circuits, no profile reapply — the per-id
  reconciler's color choice (from the *old* `light_settings`) stays on the lights,
  and the new mode metadata in `local_state` does not match the actual color.
- **Bad outcome:** "wrong color" — `mode_name` and `light_profile_*` published
  via `updateContext` describe one profile, but the bulbs show the previous
  profile's bri/hue/sat.

### D. Sensor update triggers `_schedule_transmission` while an action is in flight

- `_schedule_transmission` runs on the paho thread inside the sensor callback,
  serialized with action callbacks. So while `execute_action` is running, no
  sensor callback fires. After `execute_action` returns, the queued sensor
  message is dequeued and may trigger `_transmit_to_context_manager` under
  `transmission_interval` rate-limit.
- **Bad outcome:** if the action took longer than `transmission_interval`, the
  next sensor-driven `updateContext` fires immediately and may carry both the
  action's new state *and* a fresh sensor classification — fine logically, but
  the CM sees a "double change" attributed to one event.

### E. Threshold change concurrent with sensor update

- `_process_threshold_config` and `_process_*_sensor` both grab `state_lock`,
  so they cannot interleave. Whichever runs first determines whether the
  re-classification uses the old or new threshold.
- **Bad outcome:** at the instant of a threshold change, the most recent stored
  sensor sample may or may not trigger a flipped status, depending on lock order.
  Successive `updateContext` events may show the threshold change and the
  re-classification in either order, but both end states are consistent.

### F. `change_mode` flips `aircon_mode` to `"both_off"` between a `turn_on` and its `__turn_on_aircon`

- Within a single `turn_on(target="both")` call, the order is fixed: lights first,
  then aircon, both inside the same `execute_action`. No interleaving from another
  command is possible until this returns.
- However, if an earlier `change_mode` set `aircon_mode = "both_off"` (i.e.
  `all-clean`), Guard #2 keeps AC OFF on the *next* `turn_on`. If the rule expects
  AC ON in this preset, the agent silently does nothing and reports
  `aircon_power: "off"` — which the CM sees as success.
- **Bad outcome:** "AC didn't come on, but report said success" — only the
  `new_state` differential reveals it.

### G. `change_mode` while `power == "on"` reapplies via group path even though the active rule subset uses `light_ids`

- The reapply branch ([clubhouse_agent.py:1095-1110](lapras_agents/clubhouse_agent.py#L1095-L1110))
  always calls `__turn_on_lights()`, which issues a `PUT /groups/{id}/action` —
  this *lights every member of the group* with the new profile, including lights
  the active rule's `light_ids` excluded.
- **Bad outcome:** lights the rule wanted OFF momentarily come ON when
  `change_mode` repaints. If the CM's next command is an `light_ids` reconciler,
  the exclude pass eventually corrects it, but there's a visible flash on the
  excluded lights. (The same-mode Guard #1 was added precisely to avoid this when
  the mode hasn't actually changed.)
