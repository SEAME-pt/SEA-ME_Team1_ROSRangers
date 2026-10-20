# PX4 SITL + Gazebo + ROS 2 (issue #29)

Ubuntu 24.04 + ROS 2 Jazzy + Gazebo Harmonic + PX4 v1.17.0. Setup and test steps: vault page "PX4 SITL Setup".

```sh
docker build -t seame-px4-sim:jazzy-1.17 drone/simulation/px4
```

Run the sim with the Gazebo GUI (software rendering) and the ROS 2 offboard example: see the vault page. Start `offboard_control` only after PX4 prints `Ready for takeoff!`.
