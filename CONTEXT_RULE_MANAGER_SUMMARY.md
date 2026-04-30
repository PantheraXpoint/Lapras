# ContextRuleManager — Rule-Driven Action Surface

Black-box scope: only what `lapras_middleware/context_rule_manager.py` (the CM) reads from / writes to MQTT and the ordering of methods invoked when a trigger arrives. Agents are treated as opaque MQTT endpoints.

---

## 1. MQTT surface area (CM-side only)

### 1a. Topics the CM subscribes to

Subscriptions are created in `_on_connect` ([context_rule_manager.py:122-175](lapras_middleware/context_rule_manager.py#L122-L175)). All dispatch is in `_on_message` ([context_rule_manager.py:290-377](lapras_middleware/context_rule_manager.py#L290-L377)).

| # | Topic | Event type expected | Payload fields read | Handler |
|---|---|---|---|---|
| 1 | `virtual/+/to/context/updateContext` | `updateContext` | `state` (dict — incl. `power`, `light_power`, `aircon_power`, `proximity_status`, `motion_status`, `activity_status`, `activity_detected`, `temperature_status`, `light_status`, `door_status`, mode fields), `agent_type`, `sensors`; `event.timestamp`; `source.entityId` (= `agent_id`) | `_handle_context_update` ([context_rule_manager.py:588-655](lapras_middleware/context_rule_manager.py#L588-L655)) |
| 2 | `virtual/+/to/context/actionReport` | `actionReport` | `success`, `message`, `new_state` (merged into context map); `source.entityId` | `_handle_action_report` ([context_rule_manager.py:714-731](lapras_middleware/context_rule_manager.py#L714-L731)) |
| 3 | `TopicManager.dashboard_control_command()` | `dashboardCommand` | `agent_id`, `action_name`, `parameters`, `priority`; `event.id` | `_handle_dashboard_control_command` ([context_rule_manager.py:464-565](lapras_middleware/context_rule_manager.py#L464-L565)) |
| 4 | `TopicManager.rules_management_command()` | `rulesCommand` | `action` (`load`/`reload`/`switch`/`clear`/`list`/`list_presets`/`switch_preset`/`apply_inline`), `rule_files`, `preset_name`, `rule_content`, `virtual_path`; `event.id`; `source.entityId` (special-cased values `sensor-3d-ui`, `simulate-light-failure`, `simulate-light-repair`) | `_handle_rules_management_command` ([context_rule_manager.py:1564-1870](lapras_middleware/context_rule_manager.py#L1564-L1870)) |
| 5 | `TopicManager.dashboard_sensor_config_command()` | `sensorConfig` | `target_agent_id`, `action`, `sensor_config`; `event.id` | `_handle_sensor_config_command` ([context_rule_manager.py:2041-2091](lapras_middleware/context_rule_manager.py#L2041-L2091)) |
| 6 | `TopicManager.dashboard_threshold_command()` | (parsed as raw JSON, not via deserializer) | `event.id`, `payload.agent_id`, `payload.threshold_type`, `payload.config` | `_handle_threshold_config_command` ([context_rule_manager.py:2142-2211](lapras_middleware/context_rule_manager.py#L2142-L2211)) |
| 7 | `agent/+/sensorConfig/result` | `sensorConfigResult` | `command_id`, `success`, `message`, `agent_id`, `action`, `current_sensors` | `_handle_sensor_config_result` ([context_rule_manager.py:2093-2117](lapras_middleware/context_rule_manager.py#L2093-L2117)) |
| 8 | `agent/+/thresholdConfig/result` | `thresholdConfigResult` | `command_id`, `success`, `message`, `agent_id`, `threshold_type`, `current_config` | `_handle_threshold_config_result` ([context_rule_manager.py:2213-2244](lapras_middleware/context_rule_manager.py#L2213-L2244)) |
| 9 | `TopicManager.dashboard_control_mode_command()` | `dashboardControlModeCommand` | `payload.action` (`set`/`get`), `payload.mode` (`manual`/`automated`); `event.id` | `_handle_control_mode_command` ([context_rule_manager.py:380-449](lapras_middleware/context_rule_manager.py#L380-L449)) |

### 1b. Topics the CM publishes to that result in light/AC state changes

| Topic | Event type | Trigger inside CM | Payload fields emitted |
|---|---|---|---|
| `TopicManager.context_to_virtual_action(<agent_id>)` | `applyAction` (built by `EventFactory.create_action_event`) | `_send_action_command` ([context_rule_manager.py:1007-1034](lapras_middleware/context_rule_manager.py#L1007-L1034)) — called from `_apply_rules_for_agent` (rule eval), `_detect_and_apply_modes_from_rules` (rule load/switch), and `_handle_dashboard_control_command` follow-up after `change_mode` | `actionName` ∈ {`turn_on`, `turn_off`, `change_mode`}; `parameters` may contain `light_ids: [str,…]` (rule path) and/or `mode: "nap"\|"read"\|"normal"\|"clean"` (mode change), and `skip_verification: True` for the manual-command code path |
| same topic | `applyAction` | `_handle_dashboard_control_command` direct publish ([context_rule_manager.py:518-526](lapras_middleware/context_rule_manager.py#L518-L526)) | `actionName`, `parameters` from dashboard merged with `skip_verification=True`, `priority` |
| same topic | `applyAction` | `send_manual_command` programmatic API ([context_rule_manager.py:1922-2005](lapras_middleware/context_rule_manager.py#L1922-L2005)) | as above |
| `TopicManager.sensor_config_command(<agent_id>)` | forwarded `sensorConfig` event | `_handle_sensor_config_command` ([context_rule_manager.py:2073-2076](lapras_middleware/context_rule_manager.py#L2073-L2076)) | re-serialized incoming event (same fields) |
| `TopicManager.threshold_config_command(<agent_id>)` | `thresholdConfig` (built by `EventFactory.create_threshold_config_event`, with `event.id` overwritten to dashboard `command_id`) | `_handle_threshold_config_command` ([context_rule_manager.py:2173-2188](lapras_middleware/context_rule_manager.py#L2173-L2188)) | `agent_id`, `threshold_type`, `config` |

(Result/state-publication topics — `dashboard_control_result`, `dashboard_control_mode_result`, `rules_management_result`, `dashboard_sensor_config_result`, `dashboard_threshold_result`, `dashboard_context_state` — are emitted but do not themselves change device state, so they are excluded per scope.)

---

## 2. Rule-driven action flow

### 2a. Sensor update on `virtual/+/to/context/updateContext`

`_on_message` → `_handle_context_update` ([context_rule_manager.py:588](lapras_middleware/context_rule_manager.py#L588))
1. Deserialize, extract `agent_id = source.entityId`.
2. Under `context_lock`, write `{state, agent_type, sensors, timestamp, last_update=now}` into `context_map[agent_id]`.
3. Add to `known_agents`.
4. If `is_new_agent` and `agent_id ∈ pending_mode_detection`: pull rule files matching this agent via `_rule_file_applies_to_agent` and call `_detect_and_apply_modes_from_rules` (deferred mode push from rule files loaded before agent connected).
5. `_publish_dashboard_state_update`.
6. If any loaded rule file `_rule_file_applies_to_agent(file, agent_id)` is true, call `_apply_rules_for_agent(agent_id, new_state, timestamp)`.

`_apply_rules_for_agent` ([context_rule_manager.py:733-821](lapras_middleware/context_rule_manager.py#L733-L821)):
1. Check `_mode_change_cooldown_until[agent_id]` — if in cooldown, return.
2. Check `get_control_mode() == "automated"` — else return.
3. Check `_validate_control_topology()` — if error, return.
4. `evaluate_rules(agent_id, current_state)` runs SPARQL: rules where `lapras:hasAgent == agent_id`; for each rule, all `lapras:hasCondition` clauses must pass `_evaluate_condition` (compares state value with operator `equals`/`notEquals`/`greaterThan`/`lessThan`/`greaterThanOrEqual`/`lessThanOrEqual`); first action's `lapras:hasStateUpdate` JSON is merged into a working state copy. Returns the merged dict only if any rule changed any key.
5. From returned dict, read `power` (the decision) and optional `light_ids` list. If `light_ids` present → `action_params = {"light_ids": [...]}`.
6. `_apply_extended_window_logic(agent_id, current_power_state, raw_power_decision)` returns `final_power_decision`.
7. Branches:
   - `final != current`: `_send_action_command(agent_id, "turn_on"|"turn_off", parameters=action_params)`.
   - `final == "on"` and `action_params is not None`: compare against `last_action_commands[agent_id]`; if action name or parameters differ, re-dispatch `turn_on` with new params (the "params changed" branch).
   - else: no action.

### 2b. Rule switch on `context/rules/command`

`_handle_rules_management_command` ([context_rule_manager.py:1564](lapras_middleware/context_rule_manager.py#L1564)). The `source.entityId == "sensor-3d-ui"` flag (`demo_ui_source`) and `source.entityId ∈ {"simulate-light-failure", "simulate-light-repair"}` flag (`demo_sim_source`) change behavior.

- **Special demo policy**: if `demo_ui_source` and `action == "load"`, action is silently rewritten to `"switch"`. If `demo_ui_source` and `len(rule_files) > 1`, only the first file is kept.

| `action` | Sequence |
|---|---|
| `load` | For each file → `load_rules` (parses TTL, calls `_detect_and_apply_modes_from_rules([file])` per file). Then `_set_fast_response_window_for_all_agents("rule load")` (all known agents get 5s windows). Then `_detect_and_apply_modes_from_rules(loaded_files)` again (yes, twice). Then `_apply_rules_immediately_for_known_agents("rule load")`. |
| `reload` | Build a fresh `Graph()`, clear `loaded_rule_files`, then loop `load_rules`. Same `_set_fast_response_window_for_all_agents` + `_detect_and_apply_modes_from_rules` + `_apply_rules_immediately_for_known_agents("rule reload")`. |
| `switch` | Branch on `demo_ui_source`: <br>• If demo and a single back/front clubhouse rule was requested, the previously-loaded opposite-position rule (if any) is appended to keep both agents armed.<br>• `clear_rules()`. <br>• If `demo_ui_source`: keep `extended_window_agents` intact; else clear it. <br>• Loop `load_rules(file, detect_mode=False)`. <br>• If `demo_ui_source`: skip `_set_fast_response_window_for_all_agents` (reduces AC oscillation); else call it ("rule switch"). <br>• If `demo_ui_source`: `_detect_and_apply_modes_from_rules(explicitly_requested_rule_files)` then `_apply_rules_immediately_for_agents(target_agents, …)` where targets are derived from rule filenames; else `_detect_and_apply_modes_from_rules(loaded_files)` then `_apply_rules_immediately_for_known_agents`. |
| `clear` | `clear_rules()`, then `extended_window_agents.clear()`. |
| `list` / `list_presets` | Read-only, no side effects on devices. |
| `switch_preset` | Resolve `rule_presets[preset_name]` → list. `clear_rules()`, `extended_window_agents.clear()`, loop `load_rules`. If `demo_ui_source`: skip fast-response; else apply it. Then `_detect_and_apply_modes_from_rules(loaded_files)` + `_apply_rules_immediately_for_known_agents("preset switch")`. |
| `apply_inline` | Requires `rule_content` and `virtual_path`; rejects payloads that also carry `preset_name` / `rule_files`. `clear_rules()`. If neither `demo_ui_source` nor `demo_sim_source`: clear `extended_window_agents`; else preserve. `load_rules_from_content(rule_content, virtual_path)`. `_detect_and_apply_modes_from_rules([virtual_path])` then `_apply_rules_immediately_for_known_agents("apply_inline")`. |

`_detect_and_apply_modes_from_rules` ([context_rule_manager.py:1367-1485](lapras_middleware/context_rule_manager.py#L1367-L1485)) parses each filename:
- Filenames matching `repair-clean*`, `failover-clean*`, `failure-clean*` → `mode_commands["all"] = "clean"`.
- Filenames `<position>-<mode>.ttl` with `position ∈ {back, front, all}` and a per-position whitelist (`back/front` allow `nap`/`read`; `all` allows `normal`/`clean`) → `mode_commands[position] = mode`.
- For each `(agent_id, mode)`: if `control_mode != "automated"` → skip; if `_validate_control_topology` fails → skip; if `agent_id ∉ known_agents` → add to `pending_mode_detection`, skip; else `_send_action_command(agent_id, "change_mode", {"mode": mode}, is_manual=False)`, then set `_mode_change_cooldown_until[agent_id] = now + 2.5`. Then **only for `agent_id ∉ {"all","back","front"}`** (i.e. only split aircon/hue agents), if `_is_agent_effectively_powered_on(agent_id)`, `time.sleep(0.2)` and emit follow-up `turn_on` (no parameters) so the agent re-applies its mode profile.

### 2c. Sensor config change on `dashboard/sensor/config/command`

`_handle_sensor_config_command` ([context_rule_manager.py:2041](lapras_middleware/context_rule_manager.py#L2041)):
1. Validate `target_agent_id` and `action` exist.
2. Reject if `target_agent_id ∉ known_agents`.
3. `_set_fast_response_window(target_agent_id, "sensor config")` — installs a 5s fast-response window for that agent so a subsequent rule decision can flip OFF immediately.
4. Forward the original event verbatim to `TopicManager.sensor_config_command(target_agent_id)`.
5. Result comes back asynchronously on `agent/<id>/sensorConfig/result` and is forwarded to dashboard. CM does **not** itself emit any `turn_on`/`turn_off` here; the next sensor update from the agent (with the new sensor topology applied) is what drives subsequent rule actions.

### 2d. Threshold config change on `dashboard/threshold/command`

`_handle_threshold_config_command` ([context_rule_manager.py:2142](lapras_middleware/context_rule_manager.py#L2142)):
1. Parse JSON manually (not `MQTTMessage.deserialize`) → extract `event.id`, `payload.agent_id`, `payload.threshold_type`, `payload.config`.
2. Reject on missing required fields.
3. `_set_fast_response_window(agent_id, "threshold config")` — 5s fast window.
4. Build a `thresholdConfig` event via `EventFactory.create_threshold_config_event`, overwrite `event.id` with the dashboard-supplied `command_id`, publish to `TopicManager.threshold_config_command(agent_id)`.
5. Agent applies threshold internally and sends back `thresholdConfigResult` (handled in `_handle_threshold_config_result` and forwarded to dashboard).

How a new threshold reaches rule evaluation: the CM does **not** re-run thresholds on its own. The agent's next `updateContext` carries reclassified state booleans (e.g. `light_status=dark|bright`, `temperature_status=hot|cool`) computed against the new threshold. Those booleans appear in `context_map[agent_id].state` and are read by `_evaluate_condition` against the operator/value in the loaded rules. So a threshold change influences rule outcomes only via the agent-side reclassification visible in the next updateContext message — and the 5s fast-response window installed in step 3 gates the first OFF that this reclassification might trigger.

### 2e. Manual control on dashboard control topic

`_handle_dashboard_control_command` ([context_rule_manager.py:464](lapras_middleware/context_rule_manager.py#L464)):
1. Parse `dashboardCommand` event; extract `agent_id`, `action_name`, `parameters`, `priority`, `command_id`.
2. Reject if missing required fields.
3. Reject if `agent_id ∉ known_agents`.
4. `_validate_control_topology()` — reject on error.
5. If `action_name ∈ {turn_on, turn_off}` and `control_mode != "manual"` → reject with explanatory message.
6. Build `applyAction` event with `parameters ∪ {skip_verification: True}` (always added).
7. Publish directly to `TopicManager.context_to_virtual_action(agent_id)` — this path **bypasses** `_send_action_command` and therefore bypasses the redundancy check (`_is_action_redundant` is not consulted) and does **not** call `_record_action_command`. `last_action_commands[agent_id]` is left untouched.
8. If `action_name == "change_mode"` and `_is_agent_effectively_powered_on(agent_id)`: `time.sleep(0.2)` then `_send_action_command(agent_id, "turn_on", parameters=None, is_manual=False)` — this follow-up DOES go through redundancy / records the command. Note: `is_manual=False` here even though it's user-initiated.
9. Publish dashboard command result.

`send_manual_command` ([context_rule_manager.py:1922](lapras_middleware/context_rule_manager.py#L1922)) is a near-duplicate programmatic path with the same `known_agents` + topology + `manual`-mode checks; it also bypasses redundancy and does not auto-emit a `turn_on` follow-up.

### 2f. Control mode on dashboard control mode command

`_handle_control_mode_command` ([context_rule_manager.py:380](lapras_middleware/context_rule_manager.py#L380)):
1. `action == "get"` → publish current mode, return.
2. Validate `mode ∈ {manual, automated}`, set via `_set_control_mode`.
3. `_publish_dashboard_state_update`, publish result.
4. If transition was to `automated`: `_apply_rules_immediately_for_known_agents("control_mode switched to automated")` — this can immediately fire `turn_on`/`turn_off`/`change_mode` on every known agent based on its currently-cached state.

Switching to `manual` does not push any commands to agents — it merely begins suppressing rule-driven dispatch on subsequent context updates. There is no "park" or "freeze" command.

---

## 3. Decision gates that suppress or transform actions

| Gate | Location | Condition | Behavior when blocked |
|---|---|---|---|
| Control mode != "automated" | `_apply_rules_for_agent` ([context_rule_manager.py:746-752](lapras_middleware/context_rule_manager.py#L746-L752)); also inside `_detect_and_apply_modes_from_rules` ([context_rule_manager.py:1433-1438](lapras_middleware/context_rule_manager.py#L1433-L1438)) | `get_control_mode() != "automated"` | Log + early return. No action published. Manual `turn_on`/`turn_off` from dashboard requires the inverse (`== "manual"`) and is rejected when this is true ([context_rule_manager.py:500-507](lapras_middleware/context_rule_manager.py#L500-L507), [context_rule_manager.py:1958-1967](lapras_middleware/context_rule_manager.py#L1958-L1967)). |
| Topology validation | `_validate_control_topology` ([context_rule_manager.py:264-288](lapras_middleware/context_rule_manager.py#L264-L288)); called from `_apply_rules_for_agent`, `_handle_dashboard_control_command`, `_detect_and_apply_modes_from_rules`, `send_manual_command`, `_publish_dashboard_state_update` | Set of canonical IDs (`aircon`, `hue_light`, `all`, `back`, `front`) computed from agents whose `last_update` is within `CONTROL_AGENT_TTL_SEC` (20s) must equal one of: `{aircon}`, `{hue_light}`, `{all}`, `{back}`, `{front}`, `{aircon,hue_light}`, `{back,front}`. Disallowed: mixing `all` with `back`/`front`, or mixing split agents (`aircon`/`hue_light`) with clubhouse agents (`all`/`back`/`front`). | Returns explanatory error string. Rule path: log + return (no action). Manual paths: reject command and report error. |
| Post-mode-change cooldown | `_apply_rules_for_agent` ([context_rule_manager.py:737-744](lapras_middleware/context_rule_manager.py#L737-L744)) reads `_mode_change_cooldown_until[agent_id]`; written by `_detect_and_apply_modes_from_rules` ([context_rule_manager.py:1465-1466](lapras_middleware/context_rule_manager.py#L1465-L1466)) at `time.time() + 2.5` after each `change_mode` dispatch. | `_mode_change_cooldown_until.get(agent_id, 0) > now` | Log + return. Suppresses rule-driven `turn_on`/`turn_off` for ~2.5s; the only mode-detection follow-up `turn_on` (in `_detect_and_apply_modes_from_rules`) is sent before the cooldown is set, so it bypasses the cooldown by ordering. |
| Action redundancy | `_is_action_redundant` ([context_rule_manager.py:977-996](lapras_middleware/context_rule_manager.py#L977-L996)); enforced inside `_send_action_command` ([context_rule_manager.py:1011-1014](lapras_middleware/context_rule_manager.py#L1011-L1014)). | Same `(agent_id, action_name, parameters)` recorded in `last_action_commands` and elapsed time < `0.5s` (manual) / `2.0s` (rule). | `_send_action_command` returns silently — no publish, no record update. Note: dashboard `_handle_dashboard_control_command` and `send_manual_command` skip this gate by publishing directly. |
| Extended window logic | `_apply_extended_window_logic` ([context_rule_manager.py:880-962](lapras_middleware/context_rule_manager.py#L880-L962)) | Per-agent state machine over `extended_window_agents[agent_id] = {start_time, last_extend_time, duration, is_fast_response}` | See branch table below. |
| Pending mode deferral | `_detect_and_apply_modes_from_rules` ([context_rule_manager.py:1448-1453](lapras_middleware/context_rule_manager.py#L1448-L1453)) | `agent_id ∉ known_agents` (no context update has yet arrived) | `change_mode` not sent now; agent_id added to `pending_mode_detection`. Next `_handle_context_update` from that agent re-runs mode detection scoped to relevant rule files. |
| Filename heuristic | `_rule_file_applies_to_agent` ([context_rule_manager.py:213-246](lapras_middleware/context_rule_manager.py#L213-L246)); gates rule evaluation in `_handle_context_update` ([context_rule_manager.py:642-652](lapras_middleware/context_rule_manager.py#L642-L652)) and `evaluate_rules` ([context_rule_manager.py:1146-1151](lapras_middleware/context_rule_manager.py#L1146-L1151)) | Substring check on basename: literal `agent_id`; or for canonical `hue_light` requires both `"hue"` and `"light"` in name; for `aircon` requires `"aircon"`; for `"all"` accepts `"all"`/`"repair"`/`"failover"`/`"failure"`; for `back`/`front` requires that token. | When false, `_apply_rules_for_agent` is not called and `evaluate_rules` returns `None` early — no action. The SPARQL `hasAgent` filter inside `evaluate_rules` is a second correctness barrier, but the heuristic prevents the call entirely. |
| Same-mode short-circuit | Not visible in CM. CM always sends `change_mode` whenever `_detect_and_apply_modes_from_rules` resolves a `(agent_id, mode)`; whether the agent treats a no-op same-mode change as a no-op is opaque from the CM side. The CM also still installs the 2.5s `_mode_change_cooldown_until` regardless of whether the mode actually changed. |
| `light_ids` "params changed" branch | `_apply_rules_for_agent` ([context_rule_manager.py:798-816](lapras_middleware/context_rule_manager.py#L798-L816)) | `final_power_decision == "on"` AND `action_params is not None` AND `last_action_commands[agent_id]` differs in either `action_name` (≠ `"turn_on"`) or `parameters` (`light_ids` mismatch). | If condition holds → re-dispatch `turn_on` with new `light_ids` so the new subset is reconciled even though `power` did not transition. If condition false (same params), no action. |

### Extended window logic — every branch ([context_rule_manager.py:880-962](lapras_middleware/context_rule_manager.py#L880-L962))

Inputs: `(agent_id, current_power, raw_rule_decision)`. Output: `final_power_decision`.

1. **OFF→ON kickoff**: `current_power == "off"` and `raw == "on"` → install new normal window `{duration: EXTENDED_WINDOW_DURATION (60s), is_fast_response: False}`. Return `"on"`.
2. **No active window**: pass-through. Return `raw`.
3. **Window expired** (`now - last_extend >= duration`): delete window. Return `raw`.
4. **Fast-response window active** (`is_fast_response == True`):
   - `raw == "on"` → upgrade window to normal 60s (`is_fast_response = False`, reset `last_extend_time`). Return `"on"`.
   - `raw == "off"` → delete window, return `"off"` (allows immediate OFF — this is the key behavior of fast windows triggered by sensor/threshold config changes).
5. **Normal window active, rule says `"on"`**: refresh `last_extend_time = now`. Return `"on"`.
6. **Normal window active, rule says `"off"`**: do not refresh. Return `"on"` (stay on; the countdown continues silently).

This means within a normal extended window, OFF decisions are suppressed and the window only expires by quiescence (no `"on"` extending it for the full `duration`). When the window finally expires, the *next* sensor update with rule decision `"off"` causes the OFF to fire (one update late).

---

## 4. Timing inventory

| Name | Seconds | Triggered by | Extended/cleared by | Gates |
|---|---|---|---|---|
| `EXTENDED_WINDOW_DURATION` (default for normal windows) | `60.0` (literal at [context_rule_manager.py:82](lapras_middleware/context_rule_manager.py#L82); comment notes `15.0` for fast-OFF demo, `5.0` for fast-response derivative) | Set OFF→ON window in `_apply_extended_window_logic` case 1; upgrade from fast-response in case 4a | `set_extended_window_duration` (rewrites constant + clears all windows); `clear_rules` ('clear' command); `switch` non-demo path | Suppresses OFF rule decisions during the window |
| Fast-response window | `5.0` (literal at [context_rule_manager.py:1074](lapras_middleware/context_rule_manager.py#L1074), [context_rule_manager.py:1082](lapras_middleware/context_rule_manager.py#L1082), [context_rule_manager.py:1097](lapras_middleware/context_rule_manager.py#L1097), [context_rule_manager.py:1102](lapras_middleware/context_rule_manager.py#L1102)) | Set per-agent by `_set_fast_response_window` (sensor config + threshold config); set for all known agents by `_set_fast_response_window_for_all_agents` (rule load, rule reload, non-demo rule switch, non-demo preset switch) | Cleared on first OFF decision (case 4b) or upgraded to 60s on first ON (case 4a); window expiry; rule clear/non-demo switch wipes state | Allows immediate OFF after a config/rule change instead of waiting out a normal window |
| `CONTROL_AGENT_TTL_SEC` | `20.0` ([context_rule_manager.py:87](lapras_middleware/context_rule_manager.py#L87)) | N/A — used as a cutoff | N/A | `_normalized_control_agents` excludes agents whose `last_update` is older; `_validate_control_topology`'s "running" set; dashboard summary `active_running` |
| `_mode_change_cooldown_until[agent_id]` cooldown | `2.5` ([context_rule_manager.py:1466](lapras_middleware/context_rule_manager.py#L1466)) | After every successful `change_mode` dispatch in `_detect_and_apply_modes_from_rules` | Time-based only — no explicit clear | Suppresses rule-driven `_apply_rules_for_agent` actions (`turn_on`/`turn_off`) for that agent for 2.5s |
| Action redundancy cooldown (manual) | `0.5` ([context_rule_manager.py:991](lapras_middleware/context_rule_manager.py#L991)) | Each `_send_action_command` call recording via `_record_action_command` | New action with different `(action_name, parameters)` resets the comparison; passage of time | Skip publish in `_send_action_command` when called with `is_manual=True` |
| Action redundancy cooldown (rule) | `2.0` ([context_rule_manager.py:991](lapras_middleware/context_rule_manager.py#L991)) | as above | as above | Skip publish in `_send_action_command` when called with `is_manual=False` (the default for rule-driven and mode-detection paths) |
| `is_responsive` UI window | `300.0` ([context_rule_manager.py:670](lapras_middleware/context_rule_manager.py#L670), [context_rule_manager.py:2037](lapras_middleware/context_rule_manager.py#L2037)) | Read on each dashboard state publish / `get_agent_info` call | N/A | Boolean flag `is_responsive` in dashboard payload (UI display only — does not gate any action emission) |
| Mode-change → follow-up `turn_on` settle | `0.2` `time.sleep` ([context_rule_manager.py:545](lapras_middleware/context_rule_manager.py#L545), [context_rule_manager.py:1475](lapras_middleware/context_rule_manager.py#L1475)) | Manual `change_mode` if `_is_agent_effectively_powered_on`; auto-mode-detection for split (non-clubhouse) agents | N/A | Synchronous sleep on the MQTT callback thread before publishing the `turn_on` follow-up |
| MQTT client ID seed | `int(time.time()*1000)` ([context_rule_manager.py:97](lapras_middleware/context_rule_manager.py#L97)) | Once on init | N/A | Uniqueness of MQTT client session |
| `last_action_commands[].timestamp` | wall-clock from `time.time()` ([context_rule_manager.py:1004](lapras_middleware/context_rule_manager.py#L1004)) | Each successful `_send_action_command` | Each new send overwrites | Input to redundancy cooldowns above |
| `context_map[].timestamp` / `last_update` | wall-clock ([context_rule_manager.py:606-608](lapras_middleware/context_rule_manager.py#L606-L608)) | Each `updateContext` and each `actionReport` (the latter only updates `last_update`) | Each new write overwrites | Inputs to `CONTROL_AGENT_TTL_SEC`, `is_responsive`, dashboard summary |
| `test_extended_window_timing` sleeps | `5`, `30`, `30` ([context_rule_manager.py:2284](lapras_middleware/context_rule_manager.py#L2284), [context_rule_manager.py:2291](lapras_middleware/context_rule_manager.py#L2291), [context_rule_manager.py:2298](lapras_middleware/context_rule_manager.py#L2298)) | Manual-test method only — not invoked by any handler | N/A | None in production paths |

No other `time.sleep` or duration constants are present in the CM.

---

## 5. Race conditions and ordering hazards (CM-side observable)

1. **Rule switch racing an in-flight sensor update.**
   `_handle_rules_management_command` runs on the same MQTT callback thread as `_handle_context_update` (paho callbacks are serialized), so they do not literally overlap. But `_handle_context_update` only takes `_rule_file_applies_to_agent` snapshot once before evaluating; if a `switch` command is being processed *while a context update has just been queued*, the context update might evaluate against either the old or new rule graph depending on which lands first in the broker queue. Observable: a `turn_on` may be issued from old rules immediately before the switch wipes them, or skipped because the new rules don't match yet. The `_apply_rules_immediately_for_known_agents` post-switch helps converge but uses a stale `state` snapshot.

2. **Two rule switches in quick succession.**
   Each `switch` does `clear_rules → load → _apply_rules_immediately_for_known_agents`. If the first switch's reconciliation pass dispatches `turn_off` and is followed immediately by a second switch whose rules want `on`, the second reconciliation will see `current_state.power == "on"` (cached from before the first switch — since `actionReport` updates `power` only after the agent reports), and may decide "no change" and never reconcile the second switch's intent. Conversely, if `extended_window_agents` was preserved (demo path), the OFF in step 1 may have been suppressed by an active window, and the second switch's ON adds nothing new.

3. **Manual command racing an automated rule firing.**
   Dashboard `turn_on`/`turn_off` requires `control_mode == "manual"` and rule firing requires `"automated"`, so direct contention only happens during a mode flip. The transition to `automated` immediately calls `_apply_rules_immediately_for_known_agents`, which can dispatch actions before the user's last manual command's `actionReport` has been processed — meaning the rule decision is made against a stale `power` in `context_map`. Observable: a freshly-switched-on light is immediately turned off by a rule that thinks the previous state was OFF, or vice versa.

4. **`change_mode` followed by immediate `turn_on` follow-up.**
   The CM sleeps 0.2s on the callback thread, then publishes `turn_on` to the same agent. The CM assumes the agent has ingested and applied `change_mode` within that 0.2s. If the agent processes commands serially, the assumption holds; if it processes them concurrently or out of order, the `turn_on` may apply the *old* mode profile. The CM has no acknowledgment between the two publishes.

5. **Sensor update during post-mode-change cooldown.**
   For 2.5s after a `change_mode`, `_apply_rules_for_agent` returns early. Any sensor change in that window is dropped on the floor for rule-action purposes — no deferred re-evaluation, no queue. If the sensor change crossed a threshold during the cooldown and stays steady afterwards, the rule will only fire on the *next* update after the cooldown ends. If the next update never comes (because the sensor value is unchanged), the rule never fires. Observable: missed transitions when sensor values move once during cooldown and then stabilize.

6. **`apply_inline` whose `light_ids` differ slightly from previous rule.**
   `apply_inline` `clear_rules` then `load_rules_from_content` then `_apply_rules_immediately_for_known_agents`. The reconciliation enters `_apply_rules_for_agent` with the *current* `state.power`, which is whatever the agent last reported. If the previous rule had set `power=on` with `light_ids=A` and the new rule wants `power=on` with `light_ids=B`, the `final_power_decision == current_power` branch triggers the params-changed re-dispatch — but only if `last_action_commands[agent_id]` retained the old `parameters`. If the previous publish was a manual one (which does not record), `last_action_commands` may still hold an even older entry with `light_ids=C` or no `light_ids` at all, and the comparison may incorrectly treat them as "different" and re-dispatch, or as "same" and skip when it should re-dispatch. Observable: stale `light_ids` subset remains active.

7. **Manual `change_mode` follow-up vs concurrent rule reconciliation.**
   `_handle_dashboard_control_command`'s follow-up `_send_action_command(turn_on, parameters=None, is_manual=False)` records into `last_action_commands` with `parameters=None`. If a rule-driven dispatch with `parameters={"light_ids": [...]}` follows shortly, the `_is_action_redundant` check sees a parameter mismatch (None vs dict) and lets it through — fine. But if the rule-driven dispatch is *also* `turn_on` with `parameters=None`, the manual-command record will block it for 2.0s.

8. **`_normalized_control_agents` vs lock recursion.**
   `_validate_control_topology` calls `_normalized_control_agents` which acquires `context_lock`. `_publish_dashboard_state_update` already holds `context_lock` and calls `_validate_control_topology` directly with a precomputed `running_agents` set ([context_rule_manager.py:686-688](lapras_middleware/context_rule_manager.py#L686-L688)) to avoid re-entering. Other call sites (e.g. `_apply_rules_for_agent`, `_handle_dashboard_control_command`) call without arguments — those paths must NOT be holding `context_lock`, and are not in the current code, but this is a fragile invariant.

---

## 6. Black-box assumptions about agents

These are assumptions the CM makes implicitly. Each is a place where divergent agent behavior would silently break the CM's intent.

1. **Same-thread ordering of action commands.** The CM publishes `change_mode` and 0.2s later `turn_on`, and assumes the agent applies them in publish order, with the mode-change taking effect *before* the `turn_on` is processed. If the agent processes `applyAction` messages on a thread pool or has any reordering, the `turn_on` may apply the old profile.

2. **Synchronous `change_mode` apply within ≤0.2s.** The 0.2s sleep is the entire settle budget. If a real bridge call inside the agent's `change_mode` takes longer (network latency, IR retries), the follow-up `turn_on` runs against an in-progress profile.

3. **`light_ids` is honored on every `turn_on`.** When the rule decision carries `light_ids`, the CM forwards `parameters={"light_ids": [...]}` and assumes the agent both (a) understands the parameter, (b) reconciles to *exactly* that set (turning off any non-included Hue lights it controls). If the agent ignores the param or only adds-without-removing, the previous subset stays lit and the CM has no way to detect this.

4. **`change_mode` is idempotent and cheap.** `_detect_and_apply_modes_from_rules` always sends `change_mode` after a rule load/switch even when the resolved mode equals the current mode. The CM assumes the agent treats this as a no-op without flashing lights, restarting timers, or otherwise producing visible side effects. If not, every rule reload visibly perturbs the device.

5. **`actionReport` is the only authoritative `power` update.** `_handle_action_report` merges `new_state` into `context_map`. If an agent silently changes its `power` (e.g. an internal failsafe turns it off without reporting), `context_map` will hold stale `power` and rule decisions will be made against it — including the "no change needed" branch that suppresses dispatch.

6. **`updateContext` is sent at least once after any threshold change before any sensor update could trigger a wrong rule decision.** The CM uses the agent-side reclassification visible in `state.{light,temperature,door,…}_status` to drive rules. If the agent applies a new threshold but does not push a fresh `updateContext`, rule evaluation continues to use the cached pre-threshold classification.

7. **`agent_id` in `source.entityId` is stable and matches the canonical identifiers used in `_canonical_control_agent_id`.** The mapping uses substring rules (`"aircon" in aid`, `"hue" in aid or "light_hue" in aid or "hue_light" in aid`). An agent that publishes under, e.g., `entityId="aircon_north"` and `entityId="aircon_south"` would both canonicalize to `aircon` and collide in the topology check; an agent named `light_main` (no `"hue"`) is invisible to the canonical mapping and to all rule-evaluation gating.

8. **Agent applies `skip_verification: True` semantics.** Manual paths always inject this flag. If an agent ignores it and performs a slow verification pass, the manual command appears unresponsive.

9. **Agent processes `change_mode` separately from `turn_on`, not as part of `turn_on`'s implicit profile-reapply.** The clubhouse-vs-split branch in `_detect_and_apply_modes_from_rules` ([context_rule_manager.py:1471](lapras_middleware/context_rule_manager.py#L1471)) explicitly opts clubhouse agents (`all`/`back`/`front`) out of the follow-up `turn_on`, citing internal reapply behavior; this only holds if the agent really does reapply on `change_mode`. If a clubhouse agent stops reapplying internally (regression or refactor), the new mode's profile would never be visibly applied for those agents.

10. **`pending_mode_detection` is only consumed when the agent's *first* `updateContext` arrives.** The check is gated on `is_new_agent` ([context_rule_manager.py:597](lapras_middleware/context_rule_manager.py#L597)) — the *first* time a given `agent_id` appears in `known_agents`. If the agent reconnects (broker session expires, agent restart) after `known_agents` is already populated, the pending mode is silently lost, so the agent comes up without the rule-implied mode applied.

11. **MQTT QoS 1 at-least-once is sufficient for `applyAction` semantics.** All publishes use QoS 1. The CM does not deduplicate at the agent end and assumes the agent does (or that duplicate `turn_on`/`turn_off`/`change_mode` is harmless).

End of summary.
