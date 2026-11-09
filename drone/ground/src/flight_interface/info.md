# flight_interface

ROS 2 package for the ground ↔ flight controller link.

- `fc_telemetry` node (#96): polls the FC over MSP, read-only, through the FC USB (`transport:=usb`) or the ESP transparent bridge (`transport:=udp`, UDP 5760). Publishes `/fc/attitude` (`Vector3Stamped`, rad, REP-103) and `/fc/battery` (`BatteryState`).
- `msp.py`: the MSP v1 client. The bench tools in `scripts/` import it from here.
- Lifecycle node (activate/deactivate, watchdog, E-stop): not built yet (#103).

The topic contract, parameters, exit codes and bench results are in the vault: `docs/PraticalPlan/03 Architecture/Architecture Document.md` §2. Tests: `docs/PraticalPlan/04 CI-CD/Testing Strategy.md` §5.

Run: `ros2 run flight_interface fc_telemetry --ros-args -p transport:=udp`
Test: `colcon test --packages-select flight_interface`
