# flight_interface

ROS 2 package for the ground ↔ flight controller link.

- `fc_telemetry` node (#96): polls the FC over MSP, read-only, through the FC USB (`transport:=usb`) or the ESP transparent bridge (`transport:=udp`, UDP 5760). Publishes `/fc/attitude` (`Vector3Stamped`, rad, REP-103) and `/fc/battery` (`BatteryState`).
- `msp.py`: the MSP v1 client. The bench tools in `scripts/` import it from here.
- `flight_interface` lifecycle node (#103): `/mission/velocity_target` → backend → drone, drone state → `/flight/state`.
  - Starts unconfigured. `ros2 lifecycle set /flight_interface configure`, then `activate`: setpoints go out only while active, at `rate_hz` (`50.0`; ROS double parameters need the decimal point: `-p rate_hz:=50.0`), and only from a target that arrives after the activation. `deactivate` sends one last zero.
  - Watchdog: no valid drone state for `link_timeout_sec` (0.5 s, never longer) → zero setpoints and `link_ok` false, so `mission_node` goes IDLE. No latch: the way back after the ESP's link failsafe is the operator's `~/resume`. A target older than 0.25 s by the next tick → zero (the rule of `mission_node`'s REQ-SAF-017).
  - A non-zero setpoint also needs the drone to report `STREAMING`, override on and a known mask (the same gate as `mission_node`); axes outside the mask are zero (the rule of `mission_node`'s REQ-SAF-018).
  - Services (`std_srvs/Trigger`, from `configure` on): `~/estop`, `~/reset_estop`, `~/resume`, `~/arm_control`.
  - ARM: only the operator's `~/arm_control` sends `ARM_CONTROL_REQUEST`, with the `mask` parameter (8, 11 or 15, default 8; another value fails `configure`). Refused unless the node is active, not in E-stop, the link is fresh, the ESP reports `SESSION_IDLE` and the FC link is not reported lost. The node never arms by itself, also not after a reconnect or a reset.
  - E-stop: sends one neutral setpoint (mode 0), then `BYE`, so the ESP starts its ADR-004 output at once; then nothing more. Latched on the node: deactivate, cleanup and configure keep it; only `~/reset_estop` clears it. Control then needs a new session, an operator ARM and a new target.
  - Resume: `~/resume` is refused unless the node is active, not in E-stop, the link is fresh and the ESP reports `LINK_FAILSAFE`. Then `RESUME_REQUEST` goes out after 0.6 s of neutral setpoints. The node never resumes by itself.
  - A process restart clears the E-stop latch. The launch file (`ros2 launch flight_interface flight_interface.launch.py`) has no respawn on purpose; after a restart nothing is sent before configure, activate and an operator ARM.
  - REQ-SAF-023: tests drive every transition, link loss, E-stop, reset, resume and reconnect and check that no ARM goes out (calls on the backend and frames the fake ESP receives); a static test checks that ARM is built only in `EspBackend.arm_control()`, called only from `~/arm_control`.
  - Backends (`backend` parameter): `loopback` (no drone; reports SESSION_IDLE, so nothing engages) and `esp` (the gateway firmware over `sd_protocol`, `esp_host`, `esp_port` 5759). `esp` needs `pip install -e drone/ground/src/sd_protocol_py` and the PSK in `SEAME_PSK` or `~/.config/seame-drone/secrets.json` (never a ROS parameter). A missing PSK, a bad `esp_port` or an `esp_host` that does not resolve fails `configure`. It opens a new session by itself when the old one is gone, never with ARM, and confirms it at once with a read-only `CONFIG_GET` (the ESP drops an unconfirmed new session after 2 s).
  - `esp` for now: every setpoint goes out as mode 0 (neutral) until the velocity → stick conversion is decided. `mask` is the one the ESP confirmed with `ARM_CONTROL_ACK` OK in this session (`null` before); `airborne` will come from `FC_HEALTH`'s armed flag and is `false` until that message is decoded. The session code is `sd_protocol.ground`. While the node is inactive it sends nothing, so the ESP sends no `STATE` (it answers the address of the last setpoint).
  - `/flight/state` adds `active`, `estop`, `estop_reason`, `resume_pending`, `failsafe_output` (the ESP is sending its failsafe output: `EVENT 0x0104` / `0x0102` or `LINK_FAILSAFE`) and `fc_link_ok` (#179: `false` after `EVENT 0x0201` FC link lost, `true` after `0x0202`, `null` until the ESP says) to `mission_node`'s fields. While `fc_link_ok` is false no non-zero setpoint goes out and `link_ok` is false, so `mission_node` goes IDLE.
  - `FC_HEALTH` (0x81, #179) is not decoded yet (no byte layout): it is dropped and never counts as a `STATE`.
  - Code: `guard.py` (logic, no ROS), `backends.py`, `esp_backend.py`, `link_node.py`. Test doubles: `test/fake_esp.py` (an ESP on localhost built on `sd_protocol`).

The topic contract, parameters, exit codes and bench results are in the vault: `docs/PraticalPlan/03 Architecture/Architecture Document.md` §2. Tests: `docs/PraticalPlan/04 CI-CD/Testing Strategy.md` §5.

Run: `ros2 run flight_interface fc_telemetry --ros-args -p transport:=udp`, `ros2 launch flight_interface flight_interface.launch.py backend:=esp`
Test: `colcon test --packages-select flight_interface`
