# flight_interface

ROS 2 package for the ground ↔ flight controller link.

- `fc_telemetry` node (#96): polls the FC over MSP, read-only, through the FC USB (`transport:=usb`) or the ESP transparent bridge (`transport:=udp`, UDP 5760). Publishes `/fc/attitude` (`Vector3Stamped`, rad, REP-103) and `/fc/battery` (`BatteryState`).
- `msp.py`: the MSP v1 client. The bench tools in `scripts/` import it from here.
- `flight_interface` lifecycle node (#103, first part): `/mission/velocity_target` → backend → drone, drone state → `/flight/state`.
  - Starts unconfigured. `ros2 lifecycle set /flight_interface configure`, then `activate`: setpoints go out only while active, at `rate_hz` (`50.0`; ROS double parameters need the decimal point: `-p rate_hz:=50.0`), and only from a target that arrives after the activation. `deactivate` sends one last zero.
  - Watchdog: no valid drone state for `link_timeout_sec` (0.5 s) → zero setpoints and `link_ok` false, so `mission_node` goes IDLE. A target older than 0.25 s by the next tick → zero (the rule of `mission_node`'s REQ-SAF-017). `link_timeout_sec` can be shorter than 0.5 s, never longer (a longer one would switch the watchdog off).
  - A non-zero setpoint also needs the drone to report `STREAMING`, override on and a known mask (the same gate as `mission_node`); axes outside the mask are zero (the rule of `mission_node`'s REQ-SAF-018).
  - The services `~/estop` and `~/reset_estop` exist from `configure` on (before that nothing is sent anyway).
  - E-stop: `ros2 service call /flight_interface/estop std_srvs/srv/Trigger` sends one zero and then nothing more (the ESP then enters its 250 ms link failsafe, ADR-004). Latched on the node: deactivate, cleanup and configure keep it; only `~/reset_estop` clears it, and a new target is needed afterwards. A process restart starts without it.
  - Never sends `ARM_CONTROL_REQUEST` (REQ-SAF-023): in this part the backends have no way to send it. A test drives every transition, a link loss, an E-stop and a reset, and fails if the node touches anything on the backend but `send_setpoint`, `poll`, `close`.
  - Logic without ROS: `guard.py`. Backends: `backends.py` (only `loopback` for now; the ESP backend over `sd_protocol` and the PX4 SITL backend come next).
  - Open for the next parts: who turns the velocity target into the ESP's `SETPOINT` (attitude/throttle, not velocity), neutral (`mode 0`) vs. a zero velocity, where `mask` and `airborne` come from (not in `STATE`), the handshake retry of #156, an operator-only path for ARM (and whether RESUME is one too), and whether E-stop "one zero, then silence" is the agreed safe stop (REQ-SAF-021, ADR-004).

The topic contract, parameters, exit codes and bench results are in the vault: `docs/PraticalPlan/03 Architecture/Architecture Document.md` §2. Tests: `docs/PraticalPlan/04 CI-CD/Testing Strategy.md` §5.

Run: `ros2 run flight_interface fc_telemetry --ros-args -p transport:=udp`, `ros2 run flight_interface flight_interface`
Test: `colcon test --packages-select flight_interface`
