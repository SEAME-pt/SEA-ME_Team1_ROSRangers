---
title: Height Sensor Study
type: hardware
status: proposed
date: 2026-11-06
project: SEA autonomous drone
tags:
  - hardware
  - sensors
  - altitude-hold
  - optical-flow
---

# Height Sensor Study

> [!info] Status: proposed (2026-11-06)
> Background for the [[ADR-016 Flow Sensor Path|ADR-016]] update of 2026-11-06. It compares four sensors for holding height (and position) on the 2.5" drone. Nothing here is decided or available in the lab yet.

## 1. The problem

We fly the 2.5" drone in a clear area inside a lab room, with no net and people nearby ([[Safety Rules]] #15, #18). The Module 1 goal is a drone that takes off, holds its height, stays in place and lands **without depending on the pilot's skill**.

Today it cannot:

- **Height comes from the barometer** (DPS310 on the FC, [[ADR-016 Flow Sensor Path|ADR-016]] §2.4). A barometer measures air pressure, which changes with the propeller airflow and the room air, so its height drifts.
  - 2026-10-28: the drone climbed into a steel ceiling beam at about 2 m (GitHub #126).
  - 2026-10-29 blackbox: the DPS310 sits uncovered under the props and its reading jumps when the props blow on it; ALTHOLD engaged during a climb kept climbing twice; the drone drifts sideways (GitHub #134 "Known limits").
- **Nothing measures drift.** The ThoneFlow sees image motion but no height. Image motion is roughly speed ÷ height, so without a height it cannot give a real speed ([[ADR-016 Flow Sensor Path|ADR-016]] §2.4). It also needs a reader on the ESP that is not built yet (GitHub #107, #108).
- **The only spare height sensor is an HC-SR04 (ultrasonic).** §3.1 explains why ultrasound under our props is a weak choice.

A sensor that measures height and drift directly, and that Betaflight already uses, removes most of the pilot's work of keeping the drone still, without new code.

## 2. Method

Four sensors: the two ultrasonic ones we could use or get (HC-SR04, SRF08) and two optical ones from the same maker (MTF-01, MTF-02P). Every claim comes from a source in §6, marked:

- **[DS]** manufacturer datasheet or product page
- **[PR]** peer-reviewed paper or conference proceedings
- **[DOC]** autopilot documentation (ArduPilot, PX4)
- **[SRC]** Betaflight 2026.6.2 source code, the version on our FC
- **[F]** forum, shop or blog, only where nothing better exists

No published study tests any of these sensors under a 2.5" drone. The closest studies are listed and the gaps stated.

## 3. The four sensors

### 3.1 HC-SR04 (ultrasonic): the one we have

Sends eight 40 kHz pulses and times the echo: distance (cm) = time (µs) / 58 [DS 6].

| Specification | Value | Source |
|---|---|---|
| Range | 2 cm to 4 m; ArduPilot says use it only up to 2 m | [DS 6], [DOC 10] |
| Resolution | "up to 3 mm" (bench) | [DS 6] |
| Beam angle | 15° | [DS 6] |
| Update rate | One reading every 60 ms or more (about 16 Hz) | [DS 6] |
| Power | 5 V, 15 mA | [DS 6] |
| Interface | Trig and Echo pins; the 5 V Echo needs a divider for 3.3 V boards | [DS 6] |
| Betaflight 2026.6.2 | Has an `HCSR04` driver. Not checked whether our build includes it or whether the FC has two free pins | [SRC 8] |

- ✅ Accurate on a bench on hard surfaces. On a 1310 g quadcopter outdoors below 4 m it correlated 0.96 with the reference altitude, close to a LIDAR (0.97) [PR 9]. Measures black and transparent targets well [PR 11]. Already in the lab.
- ❌ **Propeller noise** is strongest in the ultrasonic band: on a 460 g quadrotor at hover, the echo from 1 m was below the prop noise (PSNR −4.9 dB); a shield doubled the range [PR 1]. That sensor faced forward; ours would face down, into the downwash.
- ❌ ArduPilot: keep sonar out of the prop wash, in a foam-lined tube, at least 10 cm from the ESCs [DOC 2]; not possible on a 2.5" frame. PX4 recommends LIDAR over sonar for flow sensors [DOC 3].
- ❌ Depends on temperature and humidity [PR 9, PR 11]; fails on soft surfaces [PR 11]; measures height only, not drift.

**Verdict:** a good bench sensor, the weakest option under our props, and it still needs the ThoneFlow for drift.

### 3.2 Devantech SRF08 (ultrasonic, I2C)

Same 40 kHz principle; times the echo on its own processor and reports over I2C [DS 7].

| Specification | Value | Source |
|---|---|---|
| Range | About 3 cm to 6 m | [DS 7] |
| Resolution | 3–4 cm | [F 7b] |
| Ranging time | 65 ms by default (about 15 Hz) | [DS 7] |
| Gain | 32 steps; stores up to 17 echoes per ping | [DS 7] |
| Power | 5 V, about 12 mA ranging | [DS 7] |
| Availability | Listed as discontinued by some shops | [F 7c] |
| Betaflight 2026.6.2 | **No driver** | [SRC 8] |

- ✅ Rejects false echoes better than the HC-SR04.
- ❌ Same physics, same prop-noise and airflow problems [PR 1, DOC 2]; higher gain is reported to be more sensitive to prop noise [F 12].
- ❌ **The FC cannot read it.** The ESP would have to read it and forward the data: new firmware and protocol work on the ESP that carries the control link. Height only, not drift.

**Verdict:** a better ultrasonic sensor, but it does not fix the prop problem and does not work with our FC.

### 3.3 MicoAir MTF-01 (optical flow + ToF height)

Two sensors in one module: a time-of-flight (ToF) sensor measures height with infrared light, and a small downward camera measures how the floor moves. With the height, the FC turns that motion into a speed [DS 5].

| Specification | Value | Source |
|---|---|---|
| Height range | 2 cm to 8 m (600 lux); 5 m (60K lux) | [DS 5] |
| Height accuracy | 4 cm below 2 m; 2 % above | [DS 5] |
| Light source | LED, 850 nm, about 6° beam | [DS 5] |
| Optical flow | 42° FOV, up to 7 m/s at 1 m; needs > 60 lux, works from 8 cm | [DS 5] |
| Update rate | 100 Hz | [DS 5] |
| Interface | UART 115200, 3.3 V logic; MSP, MAVLink or Micolink | [DS 5] |
| Power, size, weight | 5 V 500 mW; 29.3 × 17 × 14.5 mm; 4.5 g | [DS 5] |
| Betaflight 2026.6.2 | **Supported:** `rangefinder_hardware = MTF01`, `opticalflow_hardware = MT` | [SRC 8] |

- ✅ Light, so prop noise and airflow do not affect it, and no temperature dependence [PR 11]. Height and drift in one module. Fastest (100 Hz), longest range (8 m).
- ❌ Half as accurate below 2 m as the MTF-02P; three times heavier, 2.5 times more power. Flow needs a textured floor and light; fails on glass and glossy or plain floors [PR 13, F 14].

**Verdict:** valid; its advantages (8 m, 100 Hz) matter for high or outdoor flight, not below 2 m in a room.

### 3.4 MicoAir MTF-02P (optical flow + ToF height): best match

The same two-in-one design, smaller and lighter [DS 4].

| Specification | Value | Source |
|---|---|---|
| Height range | 2 cm to 6 m (1K lux); 4 m (70K lux) | [DS 4] |
| Height accuracy | **2 cm below 2 m**; 1.5 % above | [DS 4] |
| Light | 940 nm, **2° beam** | [DS 4] |
| Optical flow | 42° FOV, up to 7 m/s at 1 m; needs > 60 lux, works from 8 cm | [DS 4] |
| Update rate | 50 Hz | [DS 4] |
| Interface | UART 115200; MSP, MAVLink or Micolink (jumper) | [DS 4] |
| Power, size, weight | 5 V 200 mW; 21.6 × 16 × 6.5 mm; **1.5 g** | [DS 4] |
| Betaflight 2026.6.2 | **Supported:** `rangefinder_hardware = MTF02P`, `opticalflow_hardware = MT` | [SRC 8] |

What Betaflight 2026.6.2 does with it [SRC 8]:

- A position estimator (Kalman filter) combines the accelerometer, the barometer, the rangefinder and the optical flow (sideways speed scaled by the rangefinder height).
- ALTHOLD can prefer the rangefinder: `altitude_source = RANGEFINDER_PREFER`.
- Position Hold can use the flow without GPS: `poshold_position_source = OPTICALFLOW_ONLY`.
- Flow is invalid below 80 mm or when the rangefinder data is old, so flow needs a rangefinder; this module has both.
- Our cloud build already includes Range Finder, Optical Flow, Altitude Hold and Position Hold (`drone/config/betaflight/2026.6.2/CHANGELOG.md`).

- ✅ Light, so prop noise and airflow do not affect it [PR 1, PR 11]. Most accurate below 2 m, where we fly; narrowest beam, so it sees only the floor under the drone. Lightest, smallest, lowest power. No ESP code: a free FC UART and features already in our firmware.
- ❌ **No published flight test of Betaflight Position Hold with this sensor.** The developer of optical-flow Position Hold tested another sensor (Caddx Protos), so tuning may be needed. ALTHOLD with a rangefinder is simpler and more established.
- ❌ Flow needs a textured floor, > 60 lux and at least 8 cm of height [DS 4]; fails on glass [PR 13]. Dark floors and strong sunlight reduce the range of 940 nm sensors [DS 15]; in a lit room below 2 m this should not matter.

**Verdict:** the best match for indoor flight below 2 m on a 2.5" drone with our Betaflight version.

## 4. Side by side

| | HC-SR04 (have) | SRF08 | MTF-01 | **MTF-02P** |
|---|---|---|---|---|
| Height method | Ultrasound | Ultrasound | Light (ToF) | **Light (ToF)** |
| Affected by prop noise and airflow | **Yes** | **Yes** | No | **No** |
| Beam | 15° | Conical | About 6° | **2°** |
| Accuracy below 2 m | 3 mm (bench) | 3–4 cm | 4 cm | **2 cm** |
| Measures drift | No | No | Yes | **Yes** |
| Update rate | ~16 Hz | ~15 Hz | 100 Hz | 50 Hz |
| Works with Betaflight 2026.6.2 | Yes (build and pins not checked) | **No** | Yes | **Yes** |
| Needs ESP code | Possibly | **Yes** | No | **No** |
| Weight | Not stated | Not stated | 4.5 g | **1.5 g** |
| Power | 75 mW | ~60 mW | 500 mW | 200 mW |

## 5. How it would be used (if accepted)

1. **Bench, props off ([[Safety Rules]] #1):** connect to a free FC UART (UART1 is free, and is also the ESP's spare in [[ADR-011 FC UART Allocation|ADR-011]]), set MSP, check height and flow in the Configurator Sensors tab. Dated entries in ADR-011 and ADR-016.
2. **Flight area, pilot plus one more person ([[Safety Rules]] #4, #15, #18):** ALTHOLD with `altitude_source = RANGEFINDER_PREFER`; compare the blackbox with the barometer-only flights (GitHub #134).
3. **Then Position Hold** with the flow: measure drift over a fixed time and tune.
4. Then the throttle-only MSP Override stage can be proposed again ([[ADR-010 MSP Override|ADR-010]] update 2026-11-06).

The sensor improves stability; it does not replace the safety rules. The safety pilot and the kill switch always win over code, and nobody touches an armed drone ([[Safety Rules]] #4, #18).

## 6. Sources

1. [PR] Velmurugan, Brush, Balfour, Przybyla, Sanket, "Saranga: MilliWatt Ultrasound for Navigation in Visually Degraded Environments on Palm-Sized Aerial Robots", *Science Robotics* 11(112), 2026. https://arxiv.org/abs/2603.24699
2. [DOC] ArduPilot, Sonar. https://ardupilot.org/copter/docs/sonar.html
3. [DOC] PX4, Optical Flow. https://docs.px4.io/main/en/sensor/optical_flow.html
4. [DS] MicoAir MTF-02P. https://micoair.com/optical_range_sensor_mtf-02p/
5. [DS] MicoAir MTF-01. https://micoair.com/optical_range_sensor_mtf-01/
6. [DS] HC-SR04 datasheet (Elecfreaks). https://cdn.sparkfun.com/datasheets/Sensors/Proximity/HCSR04.pdf
7. [DS] Devantech SRF08 technical specification. https://www.robot-electronics.co.uk/htm/srf08tech.html
   - 7b. [F] RobotShop, SRF08. https://eu.robotshop.com/products/devantech-ultrasonic-range-finder-srf08
   - 7c. [F] The Pi Hut, SRF08. https://thepihut.com/products/srf08-ultrasonic-sensor
8. [SRC] Betaflight 2026.6.2 source. Rangefinder driver: https://github.com/betaflight/betaflight/blob/2026.6.2/src/main/drivers/rangefinder/rangefinder_lidarmt.c · Position estimator: https://github.com/betaflight/betaflight/blob/2026.6.2/src/main/flight/position_estimator.c · Settings: https://github.com/betaflight/betaflight/blob/2026.6.2/src/main/cli/settings.c · Optical-flow Position Hold: https://github.com/betaflight/betaflight/pull/14922
9. [PR] Ranđelović, Vorotović, Bengin, Petrović, "Quadcopter Altitude Estimation Using Low-Cost Barometric, Infrared, Ultrasonic and LIDAR Sensors", *FME Transactions* 49(1):21–28, 2021. https://www.mas.bg.ac.rs/_media/istrazivanje/fme/vol49/1/2_d_djordjevic_et_al.pdf
10. [DOC] ArduPilot, HC-SR04. https://ardupilot.org/sub/docs/common-rangefinder-hcsr04.html
11. [PR] Komarizadehasl, Mobaraki, Lozano-Galant, Turmo, "Practical Application of Low-Cost Sensors for Static Tests", XV DBMC, 2020. https://upcommons.upc.edu/bitstreams/e84ecde8-9f30-4be8-98db-6a0e4df62982/download
12. [F] DIY Drones, "Sonar alt hold issue". https://diydrones.com/profiles/blogs/sonar-alt-hold-issue
13. [PR] Yi, Lee, Cho, "Afocal Optical Flow Sensor for Reducing Vertical Height Sensitivity in Indoor Robot Localization and Navigation", *Sensors* 15(5), 2015. https://pmc.ncbi.nlm.nih.gov/articles/PMC4481980/
14. [F] Unmanned Tech, optical flow sensors for indoor flight. https://www.unmannedtechshop.co.uk/blogs/knowledge-base/optical-flow-sensors-drones-autonomous-indoor-flight
15. [DS] ST VL53L1X datasheet (940 nm ToF, same sensor class). https://www.st.com/resource/en/datasheet/vl53l1x.pdf

## 7. Related pages

- [[ADR-016 Flow Sensor Path|ADR-016]] · [[ADR-010 MSP Override|ADR-010]] · [[ADR-014 Betaflight Version|ADR-014]] · [[ADR-011 FC UART Allocation|ADR-011]] · [[ThoneFlow]] · [[Safety Rules]] · [[Hardware Inventory]]

## 8. Glossary

| Term | Meaning |
| --- | --- |
| **ALTHOLD** | Betaflight altitude-hold mode: the throttle stick becomes a climb/descend command and centre holds the height. |
| **Barometer (DPS310)** | Pressure sensor on the FC; Betaflight turns pressure into height. Disturbed by prop airflow. |
| **Blackbox** | The FC's flight log of sensors, commands and motor outputs. |
| **FC** | Flight Controller: the OddityRC F7 AIO running Betaflight 2026.6.2. |
| **FOV** | Field of view of the flow camera. |
| **Kalman filter** | An estimator that combines several noisy sensors into one best estimate (here height and velocity). |
| **Optical flow** | Measuring how the floor image moves under a downward camera; with the height, it gives sideways speed. |
| **Position Hold** | Betaflight mode that keeps the drone in place; without GPS it needs optical flow and a rangefinder. |
| **Rangefinder** | A sensor that measures the distance to the floor (height). |
| **ToF** | Time-of-Flight: measures distance by timing reflected light. |
| **UART** | Universal Asynchronous Receiver/Transmitter: the serial port the sensor plugs into. |
