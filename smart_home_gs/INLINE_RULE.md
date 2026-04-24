# Task: Inline rule content delivery over MQTT for the failover demo

## TL;DR

Fix a cross-machine bug in the failover/repair demo. Today the simulators write
a generated `.ttl` file to local disk, then tell the `ContextRuleManager` (CM)
to load it *by path*. The CM runs on a different machine, so the file doesn't
exist there and the rule switch silently loads nothing meaningful.

Change the protocol so the simulators send the **entire rule text** inside the
MQTT message, and the CM loads rules from that inline content instead of from
disk. Also delete the "failure state visibility" pause in `simulate_light_failure.py`
— the LLM generation latency already provides that dwell time.

---

## Scope — what changes

1. `lapras_middleware/context_rule_manager.py` — new MQTT action `apply_inline`
   that loads rule text from the message payload.
2. `smart_home_gs/simulate_light_failure.py` — Stage D uses the new action;
   delete Stage B (`--pause-sec`) entirely.
3. `smart_home_gs/simulate_light_repair.py` — Stage 2 uses the new action.

**Do NOT change:**

- The existing `switch` / `load` / `reload` / `switch_preset` actions. The
  dashboard UI uses `switch` with pre-deployed file paths and that path stays
  intact. `apply_inline` is strictly additive.
- The rule evaluation pipeline (`evaluate_rules`, `_apply_rules_for_agent`,
  the `light_ids` plumbing into `_send_action_command`, the agent-side
  `__turn_on_lights_by_ids` reconciler, the filename-prefix heuristics in
  `_rule_file_applies_to_agent` and `_detect_and_apply_modes_from_rules`).
  All of that is already working and must keep working.
- `ClubHouseAgent` / `light_hue_agent` — no changes at all.
- Rule files in `rules/demo_rules/` (`repair-clean.ttl`, `failure-clean.ttl`)
  — they stay pre-deployed on the CM machine for Stage A of the failure sim
  and remain the fallback LLM input for the repair sim.

---

## Design

### New MQTT action: `apply_inline`

Payload shape on `context/rules/command`:

```json
{
  "event": {
    "id": "sim-fail-...",
    "type": "rulesCommand",
    ...
  },
  "source": {"entityType": "dashboard", "entityId": "simulate-light-failure"},
  "payload": {
    "action": "apply_inline",
    "rule_content": "<full TTL text of the rule>",
    "virtual_path": "rules/generated/failover-clean.ttl"
  }
}
```

- `rule_content` — required. The complete TTL text the CM should parse.
- `virtual_path` — required. A path-like string used **only** for filename
  heuristics (mode detection and agent routing). It does NOT have to exist on
  disk. Basename must follow the same conventions used elsewhere
  (`failover-clean.ttl`, `repair-clean-restored.ttl`, `all-normal.ttl`, etc.)
  so that `_detect_and_apply_modes_from_rules` and
  `_rule_file_applies_to_agent` route it correctly without modification.

Result published on `context/rules/result` follows the existing
`rulesCommand` result shape (same `command_id`, `success`, `message`,
`loaded_files` pattern used by `switch`).

### Semantics — `apply_inline` behaves like `switch`, inline

Exact same behavior as `switch`, substituting content for a file read:

1. Clear existing rules (same `clear_rules()` call the `switch` path uses).
2. Parse TTL from `rule_content` via `self.rules_graph.parse(data=rule_content, format="turtle")`.
3. Add `virtual_path` to `self.loaded_rule_files` so downstream heuristics see it.
4. Call `_detect_and_apply_modes_from_rules([virtual_path])` — the filename
   triggers the existing `failover-` / `repair-` / `failure-` + `clean`
   special case and the `(all, clean)` mode routing.
5. Call `_apply_rules_immediately_for_known_agents("apply_inline")` for
   immediate reconciliation.
6. Publish the result event.

Honor the same demo-source extended-window behavior as `switch`: if
`event.source.entityId` indicates a demo source, skip the global
fast-response window override. The simulators use entity IDs
`"simulate-light-failure"` and `"simulate-light-repair"` — treat these
as demo sources equivalent to `"sensor-3d-ui"` for extended-window purposes
(i.e. don't force 5-second windows across all agents during an inline apply).

### Implementation notes

- Add a helper `load_rules_from_content(self, rule_content: str, virtual_path: str)`
  next to `load_rules`. It parses `data=` instead of a filesystem path and
  registers `virtual_path` in `self.loaded_rule_files`. Skip the "already
  loaded" dedupe check — inline content doesn't have stable identity across
  calls.
- In `_handle_rules_management_command`, add an `elif command_action == "apply_inline":`
  branch. Extract `rule_content` and `virtual_path` from the payload. Reject
  with `success=False` if either is missing or empty. Implement the six-step
  flow above. Reuse the exact error-handling pattern from the `switch` branch.
- The `apply_inline` branch must NOT honor `preset_name` or `rule_files` fields
  — those are for the other actions. Reject payloads that try to mix them.

### Simulator changes

Both simulators today call `publish_rule_switch(broker, port, rule_file)`
which publishes `action: "switch"` with `rule_files: [rule_file]`. Add a
new helper alongside it (or parameterize it):

```python
def publish_rule_apply_inline(
    mqtt_broker: str,
    mqtt_port: int,
    rule_content: str,
    virtual_path: str,
    timeout_sec: float = 15.0,
) -> dict:
    # Same structure as publish_rule_switch, but payload is:
    # {"action": "apply_inline", "rule_content": rule_content, "virtual_path": virtual_path}
```

Keep `publish_rule_switch` intact — Stage A of the failure sim still uses it
to switch to the pre-deployed `rules/demo_rules/failure-clean.ttl` by path.

#### `simulate_light_failure.py`

- **Stage A** — unchanged. Still `publish_rule_switch(..., FAILURE_RULE_FILE)`
  where `FAILURE_RULE_FILE = "rules/demo_rules/failure-clean.ttl"`. This file
  exists on the CM machine.
- **Stage B** — delete entirely. Remove the `time.sleep(args.pause_sec)` call
  and remove the `--pause-sec` CLI argument. The LLM load + generation in
  Stage C provides the dwell time naturally.
- **Stage C** — unchanged. LLM generates, `strip_markdown_fences`, `validate_ttl`.
- **Stage D** — two changes:
  1. Keep the local write to `rules/generated/failover-clean.ttl` (the repair
     sim reads it later as LLM input). Add a comment clarifying this file is
     a sim-to-sim handoff artifact, NOT consumed by the CM.
  2. Replace `publish_rule_switch(..., GENERATED_FAILOVER_FILE)` with
     `publish_rule_apply_inline(..., rule_content=new_ttl, virtual_path=GENERATED_FAILOVER_FILE)`.
     Pass the virtual path as-is (`"rules/generated/failover-clean.ttl"`) —
     the CM will basename it for routing.

#### `simulate_light_repair.py`

- **Stage 1** — unchanged. Still reads `rules/generated/failover-clean.ttl`
  (or fallback) from local disk as LLM input. LLM generates the repair TTL.
  Keep the local write to `rules/generated/repair-clean-restored.ttl` for
  debugging (comment it as a debug artifact, not CM input).
- **Stage 2** — replace `publish_rule_switch(..., GENERATED_REPAIR_FILE)` with
  `publish_rule_apply_inline(..., rule_content=new_ttl, virtual_path=GENERATED_REPAIR_FILE)`.

### Exit codes — unchanged

The existing exit code mapping stays identical:
`1 = rule file missing`, `2 = llms.init_model not importable`,
`3 = empty LLM output`, `4 = MQTT rule delivery failed/timed out`,
`5 = rdflib rejected the TTL`. Exit 4 now also covers `apply_inline` timeout
or failure result.

---

## Backward compatibility checklist

- [ ] Dashboard UI's `switch`-by-path still works end-to-end for
  `all-normal.ttl`, `all-clean.ttl`, `back-read.ttl`, etc.
- [ ] Dashboard UI's `switch` to `rules/demo_rules/repair-clean.ttl` still
  works (Stage A precondition for the demo).
- [ ] `_rule_file_applies_to_agent` still correctly routes the
  `failover-`/`repair-`/`failure-` filenames to the `all` agent — the
  virtual path preserves the basename, so this is untouched.
- [ ] `_detect_and_apply_modes_from_rules` still emits `(all, clean)` for the
  virtual paths — same mechanism as before.
- [ ] `_apply_rules_for_agent`'s params-changed branch still re-dispatches
  `turn_on` when the inline rule's `light_ids` differ from the last command.
- [ ] The agent-side `_change_mode` no-op short-circuit still prevents the
  all-8-lights flash when switching between same-mode rules.

---

## Verification

1. Preconditions: broker up, CM local, `ClubHouseAgent preset=all` on machine 1,
   `start_3d_visualization_stream` on machine 3. From the UI, switch to
   `repair-clean.ttl`. Confirm 6 lights `[1, 2, 3, 5, 8, 10]` in clean color.

2. Run `python smart_home_gs/simulate_light_failure.py --gpu-id 1` **from a
   machine that is NOT the CM machine** (this is the scenario that's broken
   today). Expect:
   - 6 → 5 lights (light 3 drops) when Stage A lands.
   - No pause — LLM gen is the visible dwell.
   - 5 → 6 lights (light 7 added) when Stage D lands.
   - CM logs: `apply_inline` action received, `(all, clean)` mode detected,
     `light_ids=[1,2,5,7,8,10]`, `PARAMS CHANGED … re-dispatching turn_on`.

3. Run `python smart_home_gs/simulate_light_repair.py --gpu-id 1` from the
   same non-CM machine. Expect:
   - LLM generates repair TTL (input read from local disk — failover sim's
     output from the previous run).
   - `apply_inline` delivered to CM.
   - 6-with-replacement → 6-original (light 3 back, light 7 off).

4. Negative check: after each simulator run, confirm the CM machine's
   `rules/generated/` directory has NOT been updated by the sim. The CM
   should have the new rules loaded in-memory only, keyed by the virtual
   path string. `loaded_rule_files` on the CM side should contain
   `rules/generated/failover-clean.ttl` (or `repair-clean-restored.ttl`)
   as a string — but no such file needs to exist on the CM's filesystem.

---

## What Claude Code should output

Claude Code's job is to **write the updated code and describe what was
changed**. Claude Code should NOT run the demo, start the broker, run the
simulators end-to-end, or make MQTT calls. The human will run and verify
everything manually.

Deliverables from Claude Code, in order:

1. **Updated code in the three files** listed in the Scope section. Edit
   the files in place (via the editor tools) — do not paste the full files
   into chat. Keep diffs minimal and surgical; do not reformat untouched
   code, rename unrelated variables, or refactor anything outside the
   changed regions.

2. **Basic syntax / import sanity checks only.** Allowed checks:
   - `python -c "import ast; ast.parse(open('<path>').read())"` on each
     edited `.py` file to confirm it parses.
   - `python -c "import py_compile; py_compile.compile('<path>', doraise=True)"`
     as an alternative.
   - `python -m py_compile <path>` on each edited file.
   - Reading the edited files back to visually confirm the edits landed
     where intended.

   **Not allowed:** starting the MQTT broker, running the `ContextRuleManager`,
   running either simulator, loading the LLM, making any network calls,
   publishing any MQTT messages, or invoking `paho.mqtt` / `rdflib`
   beyond what the syntax check implicitly imports.

3. **A summary message at the end** with three short sections:
   - **Files edited** — bullet list of file paths and a one-line description
     of what changed in each.
   - **Commands for the human to run** — the exact shell commands the human
     should run to verify the demo end-to-end, in order. Cover: Stage A
     precondition via the UI, then the two simulator invocations, then
     what to look for in the CM logs (inline action received, mode routed
     to `(all, clean)`, `PARAMS CHANGED` re-dispatch, expected `light_ids`
     progression). Do not run these commands — just print them for the
     human.
   - **Open questions / assumptions** — anything the spec left ambiguous
     that Claude Code resolved by choosing a default. Keep this section
     honest and short; if there were no ambiguities, say so explicitly.

4. **Do not write additional `.md` files, helper scripts, or docs.** The
   three code files and the summary message are the whole deliverable.

---

## Out of scope — don't do

- Do not add file-syncing between machines, shared filesystems, or
  file-distribution protocols. Inline via MQTT is the whole point.
- Do not change how rule evaluation or the `light_ids` plumbing works.
- Do not touch the agents.
- Do not add retry logic, queueing, or a "pending inline rules" cache on
  the CM side. A failed `apply_inline` returns an error result; the sim
  exits non-zero; that's the whole contract.
- Do not generalize the mode-detection filename heuristics. The virtual
  path mechanism is specifically designed to reuse them unchanged.
- Do not add a `--pause-sec` knob back or any other "ensure visibility"
  sleep anywhere.