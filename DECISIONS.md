# #103 `flight_interface`: open decisions

State on 2026-11-12. This file lists the decisions that are **still open** for the `flight_interface` lifecycle node (#103) and its ESP backend. It also lists the decisions already taken, as constraints that every proposal has to respect.

The code lives in `drone/ground/src/flight_interface/`. `guard.py` holds the safety logic, `link_node.py` is the ROS node, and `backends.py` / `esp_backend.py` talk to the drone.

## How to use this file

- Each open decision has the same fields: question, who decides, depends on / blocks, facts, options, proposal and why.
- The proposal is the author's recommendation. Nothing is decided until the people under **Decide with** agree.
- A proposal must not break a rule in §3 (fixed rules) or §4 (decided). Changing a rule in §4 needs the safety pilot (BrunoBMFT) first.
- To record a decision: write `ID = option, by <who>, <date>`, move it to §4 as one line, and update the code comment or ADR that refers to the ID.

## 1. Summary

| ID | Question | Decide with | Proposal | Blocks |
|---|---|---|---|---|
| C1 | How the velocity target becomes stick commands (controller node) | ThiagoTEP, Track E owner, BrunoBMFT | `/control/attitude_setpoint` JSON topic, built by Track E 16–20 Nov | Any non-neutral setpoint; PX4 backend |
| C2 | When a setpoint is mode 0 (neutral) or mode 1 (active) | Same as C1 | Neutral when there is no command; active when the controller commands, including an active zero | C1 implementation |
| C3 | How to measure the E-stop in the #109 demo | Dot99, BrunoBMFT, ptorrao-git, ThiagoTEP | Measure both; end to end (until the FC gets safe sticks) is the pass number | #109 |
| D1 | `armed` vs. `airborne` in `/flight/state` | BrunoBMFT, ThiagoTEP | Add an `armed` field (true/false/null); `airborne` = armed for now | `FC_HEALTH` decoding after PR #183 |
| D2 | What #103 criterion 5 ("uses the MSP client") means | Dot99, BrunoBMFT, ThiagoTEP | ESP backend publishes `/fc/attitude` and `/fc/battery` from `STATE`; reword the criterion | Closing #103 |
| D3 | Where the "Simulation" box of #103 goes | Dot99, BrunoBMFT | Move it to the PX4 backend issue | Closing #103 |
| D4 | When `ground_link.py` moves onto `sd_protocol.ground` | ptorrao-git, Dot99 | Track B, right after PR #183 merges | #60 |
| E1 | Who can call the node's services | BrunoBMFT, Dot99 | Localhost discovery + team `ROS_DOMAIN_ID` now; SROS2 later if needed | First override flight |
| E2 | The operator never sees the ESP's answer to ARM / RESUME | BrunoBMFT, ThiagoTEP | Log every answer now; add `last_ack` to `/flight/state` later | Readable bench sessions (#104, #109) |
| E3 | How the PSK is protected on the ground | ptorrao-git, BrunoBMFT | Warn on loose file permissions; mount the file in Docker | none |
| E4 | Ground side of ADR-005's replayed-`HELLO_ACK` rule | BrunoBMFT, ptorrao-git | Wait for the first `CONFIG_ACK`, redo the handshake if it is missing | none |
| E5 | Test ROS domain IDs used twice | Dot99 | Give the duplicates free IDs and add a test that catches repeats | none |

## 2. Open decisions

### C1. Who turns the velocity target into stick commands

- **Question:** what sits between `/mission/velocity_target` and the ESP's stick setpoints, and when it is built.
- **Decide with:** ThiagoTEP (author of the velocity input and of `px4_offboard_bridge`), the Track E owner for week 5, BrunoBMFT.
- **Already agreed:** a separate controller node: `/mission/velocity_target` → controller → `flight_interface` (Mission Node §6.5, 05 Future Nodes §5, ADR-019, Study Plan week 5).
- **Facts:**
  - Today the ESP backend sends every setpoint as mode 0 (neutral). It keeps the link alive and never moves the drone.
  - Nothing can pass the gate before #176 anyway: the gateway reports `override_active = 0`.
  - The gateway accepts only mask 8 (yaw) today (`kOverrideMask`).
  - x/y control needs a velocity estimate (ThoneFlow, #107/#108).
- **Sub-decisions:**

| # | Point | Options | Proposal | Why |
|---|---|---|---|---|
| C1.1 | Topic and message | a) JSON in `std_msgs/String`; b) a new message package; c) an existing type | **a**: `/control/attitude_setpoint` with `roll`, `pitch`, `yaw_rate`, `throttle`, `active` | No new package; same style as `/flight/state` |
| C1.2 | Who builds it, when | a) Track E, week 5; b) Track C; c) later | **a**, 16–20 Nov 2026 | Study Plan week 5 (C + E) |
| C1.3 | Until the controller exists | a) neutral only; b) yaw-only pass-through for mask 8 | **b** | The gateway runs mask 8 only; x/y has no velocity estimate yet |
| C1.4 | Guard limits | a) keep the velocity ceilings; b) limits in attitude units | **b**: yaw rate ≤ 45 °/s; roll/pitch from Betaflight `angle_limit` plus a ground cap; throttle only under mask 15 | The guard should check what is actually sent |
| C1.5 | The velocity input of `flight_interface` | a) moves to the controller; b) stays | **a** | One input contract for every backend (ADR-019) |
| C1.6 | Before or after #109 | a) before; b) after | **b** | #109 needs only neutral sticks |

### C2. Neutral (mode 0) vs. active (mode 1) setpoints

- **Question:** when each setpoint mode is used, once C1 exists.
- **Decide with:** same as C1. BrunoBMFT chose option c in principle; the team confirms it and the details are written with C1.
- **Options:**
  - a) Always neutral.
  - b) Always active.
  - c) Neutral when there is no command (no target, stale target, link lost, before ARM, the RESUME window, the E-stop's neutral, FC link lost). Active when the controller commands, including an active zero.
- **Proposal:** **c**, carried as the `active` field of the C1.1 topic.
- **Why:** RESUME needs neutral setpoints, and the ESP reads neutral as "nobody is driving". An active zero can brake drift once there is a velocity estimate.

### C3. How to measure the E-stop for the #109 demo

- **Question:** what start and end point the E-stop time is measured between.
- **Decide with:** Dot99 and BrunoBMFT (assignees), ptorrao-git (gateway), ThiagoTEP. OD-12 says "safe" is a team judgement.
- **Depends on:** #184 (FC USB connector repair). #109 cannot run before it.
- **Options:**
  - a) Ground side only.
  - b) End to end: from the fault until the FC receives the safe sticks, with the HIL Pi acting as the FC.
  - c) Both.
- **Proposal:** **c**, with **b** as the pass/fail number.
- **Why:**
  - REQ-SAF-021 asks for the time to the safe state, and that state is on the FC side.
  - Measure from the fault the requirement names. In #86, stopping the gestures measured `intent_node`'s 0.3 s filter instead of the 0.25 s rule.

### D1. `armed` and `airborne` in `/flight/state`

- **Question:** how the FC's armed flag (from `FC_HEALTH`, PR #183) appears on `/flight/state`.
- **Decide with:** BrunoBMFT, ThiagoTEP (`mission_node`).
- **Depends on:** PR #183 merged and `FC_HEALTH` (0x81) decoded in `sd_protocol`. Until then `airborne` is always `false`.
- **Facts:** the armed flag is 0, 1 or unknown. "Armed" is not "airborne". `mission_node` reads `airborne`, for display only today.
- **Options:**
  - a) `airborne` = armed (what B1 says today).
  - b) A new `armed` field; `airborne` stays `false` until there is a real source.
  - c) Both: a new `armed` field (true / false / null), and `airborne` = armed for now.
- **Proposal:** **c**.
- **Why:** nothing breaks in `mission_node`, and an honest field exists for later landing logic. "Unknown" must never read as "not armed".

### D2. #103 criterion 5: "uses the MSP client (#96)"

- **Question:** what "uses the MSP client" means now that the ground no longer speaks MSP to the real drone.
- **Decide with:** Dot99 (author of #103), BrunoBMFT, ThiagoTEP.
- **Facts:**
  - With the gateway (ADR-009), the ESP polls the FC over MSP and sends `STATE` to the ground. The lifecycle node has no use for the MSP client.
  - The Architecture Document says every backend must deliver `/fc/attitude` and `/fc/battery`. Today only `fc_telemetry` does (MSP over USB or the ESP bridge).
- **Options:**
  - a) The ESP backend publishes `/fc/attitude` and `/fc/battery` from `STATE`, using the same conversion code as `fc_telemetry` (`telemetry.py`). The MSP client stays the path for USB and the bench. Reword criterion 5.
  - b) The lifecycle node also uses the MSP client over USB.
  - c) Leave it, and count criterion 5 as met by the package.
- **Proposal:** **a**.
- **Why:** one telemetry contract for every backend, as the Architecture Document already says. Option b talks MSP past the gateway, which is not the real drone's path.
- **If a is chosen:**
  - zero the yaw when the node is activated, not at node start (#96 T5 recommendation; `fc_telemetry` zeroes at its first sample today);
  - the recorded-input → expected-topic tests of `fc_telemetry` can be reused to prove that the MSP and `STATE` paths give the same topics.

### D3. The "Simulation" box of #103

- **Question:** where the simulation check of #103 belongs.
- **Decide with:** Dot99 (author of #103, Track D), BrunoBMFT.
- **Options:**
  - a) Move it to the week-5 PX4 backend issue (B3). #103 is verified by unit tests, the fake ESP and the HIL bench.
  - b) Keep it in #103, with the PX4 backend inside.
  - c) Count the fake-ESP tests as simulation.
- **Proposal:** **a**.
- **Why:**
  - It matches B3.
  - The fake ESP is a unit-test double, not a simulator.
  - The gateway firmware cannot run in SITL.
  - The MSP client has no TCP transport, so it cannot reach Betaflight SITL (TCP 5761) either.

### D4. When `ground_link.py` moves onto `sd_protocol.ground`

- **Question:** who moves the bench tool `scripts/ground_link.py` onto the shared session code (B2), and when.
- **Decide with:** ptorrao-git (Track B, owner of `ground_link.py` and PR #183), Dot99 (#60, moving the bench tools under `drone/`).
- **Options:**
  - a) Track B, right after PR #183 merges.
  - b) As part of #60's move.
  - c) BrunoBMFT, inside #103.
- **Proposal:** **a**.
- **Why:** PR #183 changes `ground_link.py` heavily, so moving it earlier means conflicts. #60 is a file move, and mixing in a code change makes both harder to review.

### E1. Who can call the node's services

- **Question:** how to stop other machines from calling `~/arm_control`, `~/reset_estop` and `~/resume`, or publishing `/mission/velocity_target`.
- **Decide with:** BrunoBMFT (safety pilot, Track A), Dot99 (Track D: CI, Docker, launch), whoever sets up the ground PC.
- **Facts:**
  - ROS 2 has no access control here. Any process on the same ROS domain and network can call the services, for example a laptop on the drone's Wi-Fi with the default domain.
  - Effect: someone else could ARM (hand the masked sticks to the ground), clear an E-stop or start a resume.
  - The radio and the SA switch still win, and ARM never arms the motors.
  - CI already uses `ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST`.
- **Options:**
  - a) Keep ROS traffic on the ground PC: `ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST` and a team `ROS_DOMAIN_ID` in the launch file and the Docker setup.
  - b) SROS2 (DDS security: keys and per-topic permissions).
  - c) Accept it and document it as a known limit.
- **Proposal:** **a** now; **b** in Module 2 if the ground ever spans more than one machine.
- **Why:** a needs only environment variables and stops the realistic case. b is the real fix but adds a key store and setup for every teammate.

### E2. The operator never sees the ESP's answer to ARM or RESUME

- **Question:** how the ESP's `ARM_CONTROL_ACK` / `RESUME_ACK` result reaches the operator.
- **Decide with:** BrunoBMFT, ThiagoTEP (`mission_node`, the `/flight/state` reader).
- **Facts:**
  - `~/arm_control` and `~/resume` reply as soon as the request is **sent**.
  - The ESP's answer is kept only in the backend (`last_ack`). It is not logged or published.
  - These refusals are invisible except as `mask: null` or an unchanged `esp_state`:
    - `1` wrong state;
    - `2` mask mismatch (for example `mask:=11` while the gateway runs 8). The Protocol Spec says "the ground must not fly" after this one;
    - `3` resume condition not met;
    - `4` FC not answering.
- **Options:**
  - a) The node logs every answer (a warning with the reason for a refusal). Needs `last_ack` or a callback in the backend interface; no topic change.
  - b) a, plus a `last_ack` field on `/flight/state` (`{"type": "ARM_CONTROL_ACK", "result": 2}`). `mission_node` ignores unknown fields.
  - c) The service waits for the answer before it replies. Needs a second executor thread so `~/estop` is never blocked behind it.
  - d) Leave it.
- **Proposal:** **a** now; **b** together with the Architecture Document update (#98).
- **Why:** a is a few lines and makes bench sessions readable. c adds threads to the safety path for little gain.

### E3. How the PSK is protected on the ground

- **Question:** whether `load_psk` should check how the PSK is stored.
- **Decide with:** ptorrao-git (Track B, owner of the PSK and the gateway build), BrunoBMFT.
- **Facts:**
  - `load_psk` reads `SEAME_PSK` or `~/.config/seame-drone/secrets.json` (ADR-002).
  - The file's permissions are not checked.
  - `SEAME_PSK` can be read from `/proc/<pid>/environ` by the same user, and with `docker inspect` when it is passed to a container.
  - Whoever has the PSK can open a session and send ARM, RESUME and setpoints like the ground.
- **Options:**
  - a) Warn (or refuse) when `secrets.json` is readable by the group or others, as `ssh` does with keys.
  - b) In Docker, mount the file read-only (or use a Docker secret) instead of passing `SEAME_PSK`.
  - c) Leave it: the ground PC is a team machine.
- **Proposal:** **a** (warn only) and **b**.
- **Why:** both are small. Refusing could block a teammate at the bench, so warn first.

### E4. The ground side of ADR-005's replayed-`HELLO_ACK` rule

- **Question:** should the backend check that the first frame of a new session gets an answer.
- **Decide with:** BrunoBMFT (author of the ESP backend), ptorrao-git (Track B, gateway side).
- **Facts:**
  - `HELLO_ACK` carries no copy of the ground's nonce, so a recorded one can be replayed into a new connection.
  - ADR-005 (entry of 2026-11-06) accepts this because such a ground "has the wrong keys, gets no answer to its first frame and redoes the handshake". Session takeover is impossible.
  - The backend's first frame is the `CONFIG_GET 0x01` of K2, but it never checks that `CONFIG_ACK` comes back. An active node notices only after 3 s without `STATE`; an inactive node never notices.
  - Risk is low: the attacker must inject into the TCP stream on the Wi-Fi, and could just as well reset it.
- **Options:**
  - a) Wait for the first `CONFIG_ACK`. If none comes within `HANDSHAKE_TIMEOUT` (1 s), drop the session and redo the handshake (never with ARM). Test with the fake ESP replaying an old `HELLO_ACK`.
  - b) Leave it: the 3 s `STATE` rule covers the active node.
  - c) Spec change: `HELLO_ACK` carries the ground's nonce. Needs the firmware, both protocol implementations and the golden vectors.
- **Proposal:** **a**.
- **Why:** it is what ADR-005 already describes, needs no wire change, and also catches a gateway that took `HELLO` but lost the session.

### E5. Test ROS domain IDs used twice

- **Question:** fix the duplicate `TEST_DOMAIN_ID`s, and stop new duplicates.
- **Decide with:** Dot99 (Track D).
- **Facts:**
  - The convention is one domain ID per test file.
  - ID 89 is used by `flight_interface/test/test_fc_telemetry_ros.py` and `drone/tests/ros/test_mission_pipeline.py`.
  - ID 90 is used by `flight_interface/test/test_fc_telemetry_process.py` and `drone/tests/ros/test_mission_launch.py`.
  - CI runs them one after another today, so nothing breaks. A parallel run (`pytest -n`, two terminals) would mix their topics.
  - IDs in use: 83, 89–96.
- **Options:**
  - a) Give the two `fc_telemetry` tests free IDs (for example 97 and 98).
  - b) Add a test that fails when two files share a `TEST_DOMAIN_ID`.
  - c) Leave it.
- **Proposal:** **a** and **b**.
- **Why:** both are trivial, and b stops it from coming back.

## 3. Fixed rules every proposal must respect

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
  - `override_active` is 0 until #176;
  - the bench test of the node on the real gateway is blocked by #184.
- Never run `px4_offboard_bridge` together with `flight_interface`: both publish `/flight/state`.

## 4. Decided (do not reopen without the safety pilot)

Decided by BrunoBMFT (safety pilot) on 2026-11-09 and 2026-11-10, and implemented. The IDs appear in code comments.

| ID | Decision | Main reason |
|---|---|---|
| A1 | E-stop: latch first, then one neutral setpoint (only if setpoints were going out), then `BYE`, then nothing until `~/reset_estop`. Afterwards control needs a new session, an operator ARM and a new target. | `BYE` starts the ESP's ADR-004 failsafe output at once instead of 250 ms later. |
| K4 | E-stop while inactive: `BYE` only, no neutral. | No setpoint is ever sent while inactive. |
| A2 | Link loss: zero setpoints and `link_ok: false`, no latch. | The ESP's LINK_FAILSAFE plus the operator's RESUME already gate the way back. |
| A3 | RESUME: operator-only `~/resume`, accepted only in LINK_FAILSAFE. | Control never returns without someone deciding. |
| K6 | RESUME timing: 0.6 s of neutral setpoints with gaps under 0.1 s; a pending request is dropped after 2 s. | Margin over the ESP's 0.5 s / 0.15 s so Wi-Fi jitter does not cause a refusal. |
| A4 | ARM: `std_srvs/Trigger` `~/arm_control`, mask from the `mask` parameter (8/11/15, checked at configure). Accepted only when the node is active, not in E-stop, the link is fresh, the ESP is in SESSION_IDLE and the FC link is not lost. | No new interface package; a topic could be replayed into an accidental ARM. |
| A5 | The ESP backend reconnects by itself (one attempt every 2 s), never with ARM. | Otherwise the ground is blind after every Wi-Fi drop. |
| K2 | Each new session is confirmed with a read-only `CONFIG_GET 0x01` (E4 is about checking its answer). | The gateway drops an unconfirmed session after 2 s (#156). |
| K5 | "No `STATE` for 3 s" means a lost session only while setpoints are going out. | The ESP sends `STATE` only to the address of the last setpoint. |
| K3 | `cleanup` and `shutdown` send `BYE`. | Starts the ESP's failsafe at once. |
| K1 | FC link lost (`EVENT 0x0201`): zero setpoints and `link_ok: false`; no E-stop. | `mission_node` goes IDLE; a short FC hiccup should not cost a reset and an ARM. |
| A6 | A process restart clears the E-stop latch; the launch file has no respawn (a test checks it). | Saving the latch to a file adds code and failure modes to the safety path. |
| — | `/flight/state` stays `mission_node`'s JSON, with `active`, `estop`, `estop_reason`, `resume_pending`, `failsafe_output`, `fc_link_ok` added. | A typed message needs an interface package (Module 2). |
| B1 | `mask` = the mask this session sent and the ESP confirmed with `ARM_CONTROL_ACK` OK. It resets every session, and a refused ARM does not change it. `airborne` comes from `FC_HEALTH`'s armed flag (see D1). | No wire change; the ground already has the data. |
| B2 | One ground session implementation: `sd_protocol.ground` in `sd_protocol_py` (see D4 for `ground_link.py`). | Two copies had already drifted. |
| B3 | The PX4 backend gets its own issue in week 5, together with the controller. | #103 would otherwise wait on C1. |
| C1 (base) | A separate controller node between the mission and `flight_interface`. Details are open in C1. | ADR-019, Study Plan week 5. |
| C2 (principle) | Option c. Details are open in C2. | — |

## Glossary

| Term | Meaning |
|---|---|
| **ARM (`ARM_CONTROL_REQUEST`)** | The ground asks the ESP to pass its sticks to the FC on the masked axes. It does not arm the motors. |
| **Mask** | Which stick channels the ground controls: 8 = yaw, 11 = roll + pitch + yaw, 15 = all four (ADR-010). |
| **mode 0 / mode 1** | `SETPOINT` mode: 0 = neutral ("no command"), 1 = apply these sticks. |
| **Gate** | The guard's condition for a non-zero setpoint: STREAMING, override on, a known mask, FC link not lost. |
| **`FC_HEALTH`** | A 5 Hz ESP → ground message (0x81, PR #183): FC alive, armed flag, value ages, counters. Not decoded yet. |
| **Fake ESP** | The test double in `flight_interface/test/fake_esp.py`: the gateway's session rules on localhost. |
| **`px4_offboard_bridge`** | The simulation-only adapter (PR #154) that sends `/mission/velocity_target` to PX4 SITL. |
| **`last_ack`** | The ESP backend's record of the last `ARM_CONTROL_ACK` / `RESUME_ACK`: message type and result code. |
| **PSK** | The pre-shared key (ADR-002). The ground and the ESP derive every session key from it. |
| **ROS domain** | `ROS_DOMAIN_ID`: nodes only see nodes with the same number. Not a security boundary. |
| **SROS2** | ROS 2's security layer (DDS Security): signed and encrypted traffic, per-topic and per-service permissions. |
