# #103 `flight_interface`: decisions

State on 2026-11-12. The open decisions of the `flight_interface` lifecycle node (#103) and its ESP backend were put to the team on draft PR #187. This file records what was decided there, who decided it, and what is still left.

The code lives in `drone/ground/src/flight_interface/`. `guard.py` holds the safety logic, `link_node.py` is the ROS node, and `backends.py` / `esp_backend.py` talk to the drone.

## How to use this file

- §1 lists what is **still open**. Each item names who decides and what is proposed.
- §2 lists what was **decided on PR #187**, with who decided and the details given in the comments. These details are part of the decision.
- §3 lists the **work that follows** from the decisions, and who does it.
- §4 lists **fixed rules** and §5 the **earlier decisions**. A new proposal must respect both. Changing a decision needs the safety pilot (BrunoBMFT) first.
- To record a new decision: write `ID = option, by <who>, <date>`, move it from §1 to §2, and update the code comment or ADR that refers to the ID.

## 1. Still open

Nothing for #103 (2026-11-12). BrunoBMFT's rule: a colleague's answer on PR #187 is used, and where nobody answered, the proposal is used. The Track E owner and the C1.4 roll/pitch cap value were dropped as not needed now. The cap value is chosen when roll/pitch control starts (`MISSING_FROM_NODE.md` M3). The choices Claude made under that rule are in `MISSING_FROM_NODE.md` §2.

### E6.1. An unknown FC state (decided: b, see §2)

- **Question:** should `fc_link_ok: null` (unknown) block ARM, RESUME and the gate, and make `link_ok` false, like `false` does? ptorrao-git suggested "unknown = not OK until `FC_HEALTH` is decoded" (PR #187, 2026-11-12).
- **Facts:**
  - Today only `false` blocks anything. `null` passes `guard.arm_allowed()` and `control_possible()`.
  - The ESP sends `FC_LINK_LOST` / `FC_LINK_RESTORED` only on a change. A session that starts while the FC is steady (alive or silent) gets no event, so **until the ground decodes `FC_HEALTH`, `fc_link_ok` stays `null` for the whole session**.
  - Once `FC_HEALTH` is decoded, `fc_alive` arrives from the first UDP frame, so `null` lasts at most about 200 ms after a session start.
  - Until then, the ESP refuses ARM and RESUME itself while the FC is silent (result 4, PR #183).
- **Options:**
  - a) "Unknown = not OK" **now**: ARM is always refused by the ground until the `FC_HEALTH` decoding lands.
  - b) "Unknown = not OK" **from the `FC_HEALTH` decoding on** (the same change): then `null` only covers the first ~200 ms of a session. The loopback backend reports `fc_link_ok: true` so dry runs still work.
  - c) Keep `null` as not blocking; rely on the ESP's result 4.
- **Proposal:** **b**.
- **Why:** before the decoding, a would lock out ARM for no gain, because the ESP already refuses when the FC is silent, and no control can pass the gate before #176 anyway. After the decoding, b makes the ground's own check complete at no cost.

## 2. Decided on PR #187 (2026-11-12)

BrunoBMFT (safety pilot) took the proposal, or the other deciders' answer, on every item where they are named.

| ID | Decision | Decided by | Details from the comments (binding) |
|---|---|---|---|
| C1.1 | **a**: JSON on `std_msgs/String`, topic `/control/attitude_setpoint`, fields `roll`, `pitch`, `yaw_rate`, `throttle`, `active` | ThiagoTEP, BrunoBMFT | Units: roll/pitch in degrees, `yaw_rate` in °/s, throttle 0–1, body frame FLU like `/mission/velocity_target` |
| C1.2 | **a**: Track E builds the controller, 16–20 Nov 2026 | ThiagoTEP, BrunoBMFT | That week: **yaw only** (mask 8), tested in SITL. Roll/pitch only after a velocity estimate (flow + height, ADR-016); before that x/y would be open loop |
| C1.3 | **b**: yaw-only pass-through for mask 8 until the full controller exists | ThiagoTEP, BrunoBMFT | The yaw rate → stick mapping uses the FC's rate curve (`rates_type`, `rc_rate`, expo) from the baseline in `drone/config/betaflight`, with a unit test that 45 °/s stays inside the stick limit. First used on the props-off bench (#104) |
| C1.4 | **b**: the guard checks limits in attitude units: yaw rate ≤ 45 °/s; roll/pitch from Betaflight `angle_limit` plus a ground cap; throttle only under mask 15 | ThiagoTEP, BrunoBMFT | Keep the velocity ceilings in `mission_node` too, so there are two independent limits. The cap value is open (§1) |
| C1.5 | **a**: the velocity input moves from `flight_interface` to the controller | ThiagoTEP, BrunoBMFT | `px4_offboard_bridge` stays velocity-based and sim-only until the PX4 backend (B3) replaces it |
| C1.6 | **b**: the controller comes after #109 | ThiagoTEP, BrunoBMFT | #109 needs only neutral sticks |
| C2 | **c**: neutral (mode 0) when there is no command; active (mode 1) when the controller commands, including an active zero | ThiagoTEP, BrunoBMFT | Until there is a velocity estimate, "no command" on x/y is neutral, not an active zero |
| C3 | **c**: measure ground side and end to end; **b (end to end) is the pass/fail number** | ptorrao-git, Dot99, ThiagoTEP, BrunoBMFT | Start: the injected fault itself (E-stop call, link cut, Wi-Fi drop, first garbage frame), not a node's reaction (the #86 mistake). End: the first neutral `MSP_SET_RAW_RC` on the FC UART, with the HIL Pi acting as the FC (the HIL link-loss test already measures it). Stamp start and end on the HIL Pi's clock |
| D1 | **c**: a new `armed` field (true / false / null) on `/flight/state`, and `airborne` = armed for now | ThiagoTEP, BrunoBMFT | Checked in `mission_logic.py`: `airborne` is display-only, null reads as false, unknown fields are ignored |
| D2 | **a**: the ESP backend publishes `/fc/attitude` and `/fc/battery` from `STATE` with the `fc_telemetry` conversion (`telemetry.py`); the MSP client stays the USB/bench path | Dot99, ThiagoTEP, BrunoBMFT | Dot99 rewords #103 criterion 5 to that. Zero the yaw on activate. Reuse the `fc_telemetry` recorded-input tests to show both paths give the same topics |
| D3 | **a**: #103's Simulation box moves to the PX4 backend issue | Dot99, BrunoBMFT | Dot99 moves it once that issue exists |
| D4 | **a**: Track B moves `ground_link.py` onto `sd_protocol.ground` right after PR #183 merges | ptorrao-git, Dot99 | The bench-only keys (garbage, HELLO replay, t/R) stay in the tool, not in `sd_protocol.ground`. #60 waits for it and stays a pure file move |
| E1 | **a** now, **b** (SROS2) only if the ground ever spans more than one PC | Dot99, BrunoBMFT | Team `ROS_DOMAIN_ID = 42` (not 0, outside the test range 81–98), set as the default in `drone/ground/docker-compose.yml` (today `${ROS_DOMAIN_ID:-0}`) and in the launch file, together with `ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST` |
| E2 | **a** now (log every ARM/RESUME answer), **b** (`last_ack` on `/flight/state`) with #98 | ThiagoTEP, BrunoBMFT | b needs no change in `mission_node` (unknown fields are ignored) |
| E3 | **a** (warn only) + **b** | ptorrao-git, BrunoBMFT | Matches the gateway's `--gen-psk`, which already writes the file as 600 in a 700 folder |
| E4 | **a**: wait for the first `CONFIG_ACK`; if none within `HANDSHAKE_TIMEOUT` (1 s), drop the session and redo the handshake, never with ARM | ptorrao-git, BrunoBMFT | No wire change; the ESP side needs nothing (`CONFIG_GET 0x01` already confirms the session) |
| E5 | **a + b**: the `fc_telemetry` tests get IDs 97/98, plus a check that fails when two test files share a `TEST_DOMAIN_ID` | Dot99 | Done by Track D in its own PR. The check also rejects 42, the team domain |
| E6 | **c**: after a reconnect the ground takes `fc_link_ok` from `FC_HEALTH`'s `fc_alive` (5 Hz), so a new session knows the FC state within 200 ms. Until the ground decodes `FC_HEALTH`, it relies on the ESP refusing ARM and RESUME while the FC is silent (result 4) | BrunoBMFT, ptorrao-git | ptorrao-git confirmed on PR #187: "Events report changes, `FC_HEALTH` reports the current state"; `FC_HEALTH` carries `fc_alive = 0` from a new session's first UDP frame on. The ESP sends `EVENT 0x0201` / `0x0202` only on a change and only to the current session; "a ground that connects later reads `FC_HEALTH`" (Protocol Spec v1.4 §3.1 and ESP Link State Machine §1.1 in PR #183; `Link::set_fc_alive`). `EspBackend._open()` resets `fc_link_ok` to unknown on every new session, and unknown does not block ARM on the ground |
| TCP send | Fix the partial write in `EspBackend._send_tcp` in this PR: check the return of `send()` and close TCP on a short write, with the reason in `last_error` | BrunoBMFT | Found in the review of 2026-11-12. Only touches the ESP backend |
| E6.1 | **b**: "unknown FC state = not OK" for ARM, RESUME, the gate and `link_ok`, applied in the same change that decodes `FC_HEALTH` | The proposal (BrunoBMFT's rule, 2026-11-12); suggested by ptorrao-git | See E6.1 in §1 for the facts and options |
| D2.1 | `/fc/*` from `STATE` only while `fc_link_ok` is true; after `FC_HEALTH` is decoded, per-value age checks instead | Claude, under BrunoBMFT's rule | `STATE` carries no age: zero before the FC's first reply, frozen up to 10 s after it goes silent. Details in `MISSING_FROM_NODE.md` §2 |

## 3. Work that follows

**In #103 (PR #187 or its follow-up, BrunoBMFT).** What is left is written for the next implementer in `MISSING_FROM_NODE.md` (repo root), items M1–M11.

| From | Work | Status (2026-11-12) |
|---|---|---|
| TCP send | Handle a short `send()` in `_send_tcp`; test it | ✅ done, not committed yet |
| E2 (a) | The node logs every `ARM_CONTROL_ACK` / `RESUME_ACK` result (warning with the reason on a refusal); `take_acks()` in the backend interface | ✅ done, not committed yet. (b) waits for #98: M6 |
| E4 | Wait for the first answer; redo the handshake if missing. Test: the fake ESP replays an old `HELLO_ACK` | ✅ done, not committed yet |
| E1 | `ROS_DOMAIN_ID=42` and `ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST` as defaults in `flight_interface.launch.py` and `drone/ground/docker-compose.yml` | ✅ done, not committed yet. Other packages' launch files: M5 |
| E3 | `load_psk` warns when `secrets.json` is readable by the group or others; Docker mounts the file read-only instead of passing `SEAME_PSK` | ✅ warning done, not committed yet. Docker half waits for a `flight_interface` compose service: M4 |
| D2 | `/fc/attitude` and `/fc/battery` from `STATE` through `telemetry.py`; yaw zeroed on activate; shared conformance tests with `fc_telemetry` | ✅ done (with the D2.1 gate), not committed yet |
| #183 follow-up, ground half | Refuse `~/resume` while the ESP reports the FC lost | ✅ done, not committed yet |
| D1, E6, E6.1, D2.1 | Decode `FC_HEALTH` (0x81, 42 bytes, Protocol Spec v1.4 from PR #183): `armed` on `/flight/state` with `airborne` = armed (D1), `fc_link_ok` from `fc_alive` (E6), unknown = not OK (E6.1), per-value ages for `/fc/*` (D2.1); `sd_protocol` names instead of local constants; rewrite `test_fc_health_without_a_layout_is_ignored` | Waits for PR #183: M1, in a new issue blocked by #179 (not created yet) |

**Other people:**

| Who | Work | Waits for |
|---|---|---|
| Track E | Controller node, yaw only (C1.1–C1.3, C2), 16–20 Nov 2026; then `flight_interface` switches its input to `/control/attitude_setpoint`, sends mode 1, and its guard limits move to attitude units (C1.4, C1.5): `MISSING_FROM_NODE.md` M3 | — |
| Track E | PX4 backend issue (B3); takes #103's Simulation box (D3) | Controller |
| Dot99 | E5 in its own PR; reword #103 criterion 5 (D2) | — |
| ptorrao-git | Move `ground_link.py` onto `sd_protocol.ground` (D4) | PR #183 |
| ptorrao-git (#188, `safety-critical`, Sprint 3) | On the ESP: ARM and RESUME need good `MSP_SET_RAW_RC` acks, continuously for ≥ 1 s; `FC_LINK_RESTORED` only after 250 ms of continuous replies. Accepted by BrunoBMFT on PR #183 (2026-11-10). It means fewer LOST / RESTORED flips reach `/flight/state` | #179 / PR #183; blocks #104 |
| Dot99, BrunoBMFT | #109 demo with the C3 measurement | #184 (FC USB repair) |
| shared (#98) | Docs PR: Architecture Document §2 (`flight_interface` row, services, parameters, `/flight/state` fields incl. `armed`, `last_ack`), Testing Strategy §5 (the #103 test files), and these decisions as dated ADR entries. This file then moves into the vault or is deleted before #187 leaves draft | — |

## 4. Fixed rules every proposal must respect

These come from requirements, ADRs and the hardware.

- The node never sends ARM or RESUME on its own (REQ-SAF-023). ARM is built only in `EspBackend.arm_control()`, called only from `~/arm_control`, and a static test enforces this.
- The radio and the safety pilot always win. ARM only hands the masked stick channels to the ground. It never arms the motors: only the pilot's SA switch does that.
- Limits in `guard.py` (they repeat `mission_node`'s):
  - a stale target is replaced by zero within 0.25 s (REQ-SAF-017);
  - no drone `STATE` for 0.5 s means the link is lost (REQ-SAF-012);
  - axes outside the mask are zero (REQ-SAF-018);
  - horizontal speed ≤ 0.5 m/s as a vector, vertical speed ≤ 0.5 m/s, yaw rate ≤ 45 °/s.
- A non-zero setpoint needs the drone to report `STREAMING`, override on and a known mask (8, 11, 15), and the FC link must not be reported lost.
- Hardware and firmware facts today:
  - the gateway accepts only mask 8;
  - `override_active` is 0 until #176, so no control passes the gate;
  - the bench test of the node on the real gateway is blocked by #184.
- Never run `px4_offboard_bridge` together with `flight_interface`: both publish `/flight/state`.

## 5. Earlier decisions (implemented)

Decided by BrunoBMFT (safety pilot) on 2026-11-09 and 2026-11-10. The IDs appear in code comments.

| ID | Decision | Main reason |
|---|---|---|
| A1 | E-stop: latch first, then one neutral setpoint (only if setpoints were going out), then `BYE`, then nothing until `~/reset_estop`. Afterwards control needs a new session, an operator ARM and a new target. | `BYE` starts the ESP's ADR-004 failsafe output at once instead of 250 ms later. |
| K4 | E-stop while inactive: `BYE` only, no neutral. | No setpoint is ever sent while inactive. |
| A2 | Link loss: zero setpoints and `link_ok: false`, no latch. | The ESP's LINK_FAILSAFE plus the operator's RESUME already gate the way back. |
| A3 | RESUME: operator-only `~/resume`, accepted only in LINK_FAILSAFE. | Control never returns without someone deciding. |
| K6 | RESUME timing: 0.6 s of neutral setpoints with gaps under 0.1 s; a pending request is dropped after 2 s. | Margin over the ESP's 0.5 s / 0.15 s so Wi-Fi jitter does not cause a refusal. |
| A4 | ARM: `std_srvs/Trigger` `~/arm_control`, mask from the `mask` parameter (8/11/15, checked at configure). Accepted only when the node is active, not in E-stop, the link is fresh, the ESP is in SESSION_IDLE and the FC link is not lost. | No new interface package; a topic could be replayed into an accidental ARM. |
| A5 | The ESP backend reconnects by itself (one attempt every 2 s), never with ARM. | Otherwise the ground is blind after every Wi-Fi drop. |
| K2 | Each new session is confirmed with a read-only `CONFIG_GET 0x01` (E4 adds the check of its answer). | The gateway drops an unconfirmed session after 2 s (#156). |
| K5 | "No `STATE` for 3 s" means a lost session only while setpoints are going out. | The ESP sends `STATE` only to the address of the last setpoint. |
| K3 | `cleanup` and `shutdown` send `BYE`. | Starts the ESP's failsafe at once. |
| K1 | FC link lost (`EVENT 0x0201`): zero setpoints and `link_ok: false`; no E-stop. | `mission_node` goes IDLE; a short FC hiccup should not cost a reset and an ARM. |
| A6 | A process restart clears the E-stop latch; the launch file has no respawn (a test checks it). | Saving the latch to a file adds code and failure modes to the safety path. |
| — | `/flight/state` stays `mission_node`'s JSON, with `active`, `estop`, `estop_reason`, `resume_pending`, `failsafe_output`, `fc_link_ok` added (D1 adds `armed`, E2 later `last_ack`). | A typed message needs an interface package (Module 2). |
| B1 | `mask` = the mask this session sent and the ESP confirmed with `ARM_CONTROL_ACK` OK. It resets every session, and a refused ARM does not change it. `airborne` per D1. | No wire change; the ground already has the data. |
| B2 | One ground session implementation: `sd_protocol.ground` in `sd_protocol_py` (D4 moves `ground_link.py` onto it). | Two copies had already drifted. |
| B3 | The PX4 backend gets its own issue in week 5, together with the controller. | #103 would otherwise wait on C1. |

## Glossary

| Term | Meaning |
|---|---|
| **ARM (`ARM_CONTROL_REQUEST`)** | The ground asks the ESP to pass its sticks to the FC on the masked axes. It does not arm the motors. |
| **Mask** | Which stick channels the ground controls: 8 = yaw, 11 = roll + pitch + yaw, 15 = all four (ADR-010). |
| **mode 0 / mode 1** | `SETPOINT` mode: 0 = neutral ("no command"), 1 = apply these sticks. |
| **Gate** | The guard's condition for a non-zero setpoint: STREAMING, override on, a known mask, FC link not lost. |
| **`FC_HEALTH`** | A 5 Hz ESP → ground message (0x81, 42 bytes, Protocol Spec v1.4 in PR #183): FC alive, armed flag, arming flags, value ages, counters. The ground decodes it after PR #183 merges. |
| **Fake ESP** | The test double in `flight_interface/test/fake_esp.py`: the gateway's session rules on localhost. |
| **`px4_offboard_bridge`** | The simulation-only adapter (PR #154) that sends `/mission/velocity_target` to PX4 SITL. |
| **`last_ack`** | The ESP backend's record of the last `ARM_CONTROL_ACK` / `RESUME_ACK`: message type and result code. |
| **PSK** | The pre-shared key (ADR-002). The ground and the ESP derive every session key from it. |
| **ROS domain** | `ROS_DOMAIN_ID`: nodes only see nodes with the same number. Not a security boundary. |
| **SROS2** | ROS 2's security layer (DDS Security): signed and encrypted traffic, per-topic and per-service permissions. |
| **Short write** | A non-blocking `send()` that sends only part of the bytes it was given. |
