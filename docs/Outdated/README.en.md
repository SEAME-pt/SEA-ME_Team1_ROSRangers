# Hardware: Brook 2.5 FPV drone and Raspberry Pi 5

Documentation of the hardware identified from photos and from the Betaflight console. Each board has photos with numbered parts and a table that says what each number is.

**How to read the tables**

| Certainty | Meaning |
|---|---|
| High | Read from the chip marking, from the board silkscreen, or confirmed by Betaflight |
| Likely | Deduced from appearance, position and what is usual in this family of boards |
| Uncertain | A guess with little basis |
| Unknown | I could not identify it |

The values of the small resistors and capacitors cannot be read from photos. To get them you need the manufacturer's schematic, or you have to measure each part with a multimeter while it is out of the circuit.

## Index

```table-of-contents
title:
style: nestedList # TOC style (nestedList|nestedOrderedList|inlineFirstLevel)
minLevel: 0 # Include headings from the specified level
maxLevel: 0 # Include headings up to the specified level
include:
exclude:
includeLinks: true # Make headings clickable
hideWhenEmpty: false # Hide TOC if no headings are found
debugInConsole: false # Print debug info in Obsidian console
```

---

## 1. Pilotix Brook 2.5 drone, overview

A 2.5" cinewhoop for a 4S battery, analog, PNP (no receiver included).

| Component | Model | Notes |
|---|---|---|
| Frame | Pilotix Brook 2.5" Cinewhoop | 4S, analog, PNP |
| Flight controller and ESC | OddityRC F7 40A AIO | `manufacturer_id: ODTY`, `board_name: ODDITYRCF7AIO` |
| VTX | PandaRC VT5804 BAT LITE | 5.8 GHz, up to 1300 mW, 6 to 36 V input, MMCX antenna |
| Video receiver on the PC | MacroSilicon MS210x, USB grabber | `idVendor=534d`, `idProduct=0021` |
| Firmware | Betaflight 4.5.1, from 17 Oct 2024 | MSP API 1.46 |
| Radio receiver | ELRS 2.4 GHz (BETAFPV Nano) | See section 3 |

Output of the `version` command on the flight controller:

```
Betaflight / STM32F7X2 (S7X2) 4.5.1 Oct 17 2024 / 19:49:44 (326ecdcf2) MSP API: 1.46
board: manufacturer_id: ODTY, board_name: ODDITYRCF7AIO
```

The factory configuration was saved in `~/drone-backup.txt`, with the output of the `diff all` command.

### Warnings before connecting the battery

1. **Antenna on the VTX** before any battery connection. Without an antenna, the VTX burns out.
2. **Propellers removed** for any motor test.
3. **Never connect the battery and the USB at the same time.**
4. The `BAT` pin on the 6 pin connector is the **direct battery voltage**, not 5 V.
5. There is no smoke stopper, so the first battery connection is risky.
6. The large capacitor (470 µF, 35 V) has polarity. The light stripe on the cylinder must face the black wire, the negative one. This stripe has not been confirmed yet.

### 6 pin connector, under the flight controller

This is the digital VTX connector (DJI O3, Walksnail, Vista). It is not used for the analog VTX, which connects through soldered pads. The order below comes from the manual, from top to bottom in the official diagram.

| Pin | Function |
|---|---|
| R2 | UART2 RX, or SBUS coming from the DJI |
| GND | Ground |
| R6 | UART6 RX, or TX of the digital VTX |
| T6 | UART6 TX, or RX of the digital VTX |
| GND | Ground |
| BAT | Direct battery voltage |

The connector model and the pin pitch are not confirmed.

### To be confirmed

* [ ] A 4S LiPo battery, 650 to 850 mAh, 60C or more, with the same plug as the drone
* [ ] Whether the drone plug is XT30 or XT60 (it is engraved in the plastic)
* [ ] A LiPo charger with 4S balancing and a safety bag
* [ ] Connect the ELRS receiver to the pads and configure it in Betaflight
* [ ] Connect Betaflight Web (app.betaflight.com) in Google Chrome

---

## 2. OddityRC F7 40A AIO flight controller

A flight controller and 4 in 1 ESC on the same board, with 3 pads per motor, a USB-C port, and pads for the camera and the VTX.

### Specifications confirmed by the console (`status`)

| Item | Value |
|---|---|
| Microcontroller | STM32F722RET6 at 216 MHz |
| Gyroscope and accelerometer | ICM42688P, about 8 kHz |
| OSD | MAX7456 compatible, 30×13 |
| Blackbox | 16 MB of flash (Winbond W25Q128, JEDEC `0x00ef4018`) |
| Temperature | 44 °C, no I2C errors |
| Battery | Not connected |
| Arming flags | RXLOSS, CLI, MSP, DSHOT_TELEM (normal, with no receiver and with USB) |

The DPS310 barometer is detected: `status` on 2026-10-13 and 2026-10-14 shows `I2C:1`, `BARO=DPS310`, `I2C Errors: 0` (the earlier `I2C:0` reading is superseded).

### Photos

![Flight controller, photo 1](imagens/controladora_oddityrc_1.jpg)

*Photo 1, with numbers 1 to 19.*

![Flight controller, photo 2](imagens/controladora_oddityrc_2.jpg)

*Photo 2, with numbers 1 to 25. Numbers 20 to 25 only appear in this photo.*

### Parts table

| No. | What it is | Certainty |
|---|---|---|
| 1 | STM32F722RET6 processor. The model can be read in photo 2 and was confirmed by `status`. | High |
| 2 | OSD chip marked `AT7456E`, compatible with the MAX7456 that Betaflight showed. | High |
| 3 | Chip marked `1428P P42AB1 2230`. It looks like the ICM42688P gyroscope, but the marking does not confirm it. | Likely |
| 4 | 5 pin regulator marked `LG33`. The name suggests 3.3 V. | Likely |
| 5 | Two diodes, probably to choose between the 5 V from the USB and the one from the battery. | Likely |
| 6 | Large ceramic capacitors, beige in color. | Likely |
| 7 | Small integrated circuit, with no legible marking. | Unknown |
| 8 | Component marked `E17E 419JG3`. It could be a regulator or a MOSFET. | Unknown |
| 9 | Black blocks in groups of 3, the ESC transistors (12 in total). One per group is marked. | Likely |
| 10 | Motor pads, 3 per motor, already soldered. | High |
| 11 | USB-C connector. | High |
| 12 | Battery pads. Red goes to `+` and black goes to `G`. | High |
| 13 | Electrolytic capacitor of 470 µF and 35 V, from the marking read on the part. The polarity stripe must face the black wire, still to be confirmed. | High |
| 14 | Four free pads: `G`, `B-`, `5V` and `LED`. By the labels, they are for a buzzer and an LED strip. | Likely |
| 15 | Status LED. | Likely |
| 16 | Component with a hole in it, looks like the barometer. | Likely |
| 17 | Beige crystal or oscillator with metal terminals. | Likely |
| 18 | Gold pad with a black dot, probably the BOOT button. | Uncertain |
| 19 | Camera pads (`CAM`, `5V`, `G`), already soldered. | High |
| 20 | 8 pin chip to the left of the processor, maybe the Blackbox flash. | Uncertain |
| 21 | Square black block, with no marking. | Unknown |
| 22 | Pads with 4 wires, which by position look like `5V`, `G`, `R2` and `T2`. The usual ELRS receiver pattern. | Medium |
| 23 | Pads with the white and yellow wires, next to `VTX`. They look like SmartAudio and video. | Medium |
| 24 | Free pads `R1` and `T1`. | High |
| 25 | Elongated silver part, next to the barometer. | Unknown |

### Differences from the shop photos

* The USB on this board is **USB-C**. The shop photo showed micro USB.
* I did not find the `4R7` inductor that appears in the shop photos. This version of the board is different.
* What looked like a button is the gold pad of number 18.
* Numbers 22 and 23 come from small, partly hidden labels. Check with a multimeter before connecting the battery, especially the red wire.

### Pads for the receiver and for the analog VTX

* UART2 (`R2` and `T2`) is the one used for the ELRS receiver.
* Signal pads visible on the silkscreen: `T1`, `R1`, `R3`, `T3`, `CAM`, `VTX`, `5V` and `G`.
* Motor pads: `M1` to `M4`, with 3 pads each.

---

## 3. ELRS 2.4 GHz receiver (BETAFPV Nano)

By the markings, this is a BETAFPV ELRS Nano 2.4 GHz. The front says "ELRS 2.4G V1.1" and the back has "BETAFPV" printed on it. The radio chip is the SX1281 and the microcontroller is the ESP8285.

### Photos

![Receiver, front](imagens/recetor_elrs_frente.jpg)

*Front, with numbers 1 to 30.*

![Receiver, back](imagens/recetor_elrs_costas.jpg)

*Back, with numbers 31 to 57.*

### Identified parts, front

| No. | What it is | Certainty |
|---|---|---|
| 1 | Radio chip. `1281` can be read, with the Semtech logo, so it is the SX1281. | High |
| 2 | Chip marked `AT2401C`, a 2.4 GHz amplifier. | High |
| 3 | Antenna connector, of the U.FL (IPEX) type. | High |
| 4 | LED. | Likely |
| 5 | Crystal marked `BN52.0`, at 52 MHz, which is the frequency the SX1281 uses. | Likely |
| 6 | Large inductor, probably for the SX1281 voltage converter. | Likely |
| 7 and 8 | Two blue parts, probably radio filters. | Uncertain |
| 9 | `R` pad | High |
| 10 | `T` pad | High |
| 11 | `V` pad (power) | High |
| 12 | `G` pad (ground) | High |

### Identified parts, back

| No. | What it is | Certainty |
|---|---|---|
| 31 | `ESP8285` microcontroller. It runs the ELRS firmware and has the Wi-Fi used to update it. | High |
| 32 | Voltage regulator marked `IL318`. It looks like a 3.3 V one, but I could not confirm it. | Likely |
| 33 | Crystal marked `YXC 26.000` (26 MHz), for the ESP8285. | High |
| 34 | Large status LED. | Likely |
| 35 | Small LED, with a green background. | Uncertain |
| 36 | Gold pad with a black dot, probably the BOOT button. | Likely |

### Resistors and capacitors

The small parts have no marking. They are grouped by appearance, with no values.

| Appearance | Likely function | Numbers |
|---|---|---|
| Beige body, metal terminals | Ceramic capacitor | 13, 16, 18, 23, 24, 25, 26, 28, 29, 30 (front) and 37, 38, 39, 40, 44, 46, 49, 51, 52, 57 (back) |
| Light gray | Capacitor, probably for radio | 14, 15, 17, 19, 20 (front) and 43, 50 (back) |
| Dark body | Resistor | 21, 22, 27 (front) and 41, 42, 45, 47, 53, 54, 55, 56 (back) |
| Dark, with a green mark | Resistor or diode | 48 |

### Connection to the drone

The pads are the same on both faces, only seen from opposite sides. On the back the order is reversed.

| Receiver pad | Connects to | Notes |
|---|---|---|
| `R` (9) | `T2` on the flight controller (UART2 TX) | The RX of one connects to the TX of the other |
| `T` (10) | `R2` on the flight controller (UART2 RX) | The TX of one connects to the RX of the other |
| `V` (11) | `5V` | Confirm the voltage in the BETAFPV manual, which I did not read on the board |
| `G` (12) | `G` | Ground |

In Betaflight, on the Ports tab, enable **Serial Receiver** on UART2 and choose **CRSF** as the protocol. The antenna must be connected to the U.FL (3) before connecting the battery.

---

## 4. Raspberry Pi 5 with 16 GB

A board without a heatsink or a fan, with a SanDisk Ultra 128 GB microSD card inserted. The board silkscreen shows the FCC ID `2ABCB-RPI5`, the IC `20953-RPI5` and the ANATEL `04930-24-10629`.

### Photos

![Raspberry Pi 5, top face](imagens/raspberry_pi5_cima.jpg)

*Top face, with numbers 1 to 50.*

![Raspberry Pi 5, close up](imagens/raspberry_pi5_grande_plano.jpg)

*Close up with the same numbers as the top face: 2, 3, 9, 30, 45 and 46.*

![Raspberry Pi 5, bottom face](imagens/raspberry_pi5_baixo.jpg)

*Bottom face, with numbers 51 to 67.*

### Chips, top face

| No. | What it is | Certainty |
|---|---|---|
| 1 | Broadcom BCM2712 SoC. `BROADCOM 2712ZPKFSB00D0T` can be read. | High |
| 2 | 16 GB memory. The chip has no legible marking, but the board has "16GB RAM" printed on it. | Likely |
| 3 | `RP1-C0`, the chip that handles the USB, Ethernet, GPIO and cameras. | High |
| 4 | Renesas `DA9091` power manager. | High |
| 5 | Module with a metal shield, probably the Wi-Fi and Bluetooth. | Likely |
| 6 | Broadcom chip `BCM5421…`, probably the Ethernet. | Likely |
| 7 | Oscillator marked `AEL25.00`, at 25 MHz. | Likely |
| 8 | Large diode, marked `100`. | Likely |
| 9, 10, 11, 12, 13, 14 | Small chips and a transistor, with no marking I can read. Number 9 seems to have `K40A`, but I did not confirm it. | Unknown |

### Connectors and buttons, top face

| No. | What it is |
|---|---|
| 15 | USB-C power input (J1, `PWR`) |
| 16 and 17 | Micro HDMI 0 (J2) and 1 (J13) |
| 18 | Debug UART (J16) |
| 19 | Clock battery (J5, `BAT`) |
| 20 | PCIe (J20) |
| 21 | Power button (`PSW`) |
| 22 | Status LED (`STAT`) |
| 23 and 24 | Camera or display 1 (J4) and 0 (J3) |
| 25 | PoE header (J14) |
| 26 | Trxcom `TRJG0926HENL4R` RJ45, with the transformers inside |
| 27 | Two USB 3 ports (J12, `USB3`) |
| 28 | The other two USB ports, probably USB 2 |
| 29 | Fan (J17, `FAN`) |
| 30 | 40 pin GPIO header (J8) |
| 31 and 32 | Plated holes next to `VID` and `BAT`. I do not know what they are for. |

These connectors have high certainty, because their function is printed on the board itself.

### Small parts, top face

* **33 to 36:** gray blocks next to the power manager, probably inductors.
* **37 to 44:** small parts in the middle of the board, between the SoC and the RP1. They are resistors or capacitors, with no marking.
* **45:** black part with a metal end, in the close up.
* **46:** eight pads with no component, in the close up. You can see that nothing is mounted there.
* **47, 48 and 50:** beige parts, probably capacitors.
* **49:** one of several small parts in a row, next to the RP1.

### Bottom face

| No. | What it is | Certainty |
|---|---|---|
| 51 | microSD slot, with a SanDisk Ultra 128 GB card | High |
| 52, 53, 54 | Small chips: one with 8 pins, one square, another with 6 pins next to the microSD slot | Unknown |
| 55 and 56 | Oscillators or crystals | Likely |
| 57 and 58 | Small black parts next to `TP36` and `TP35` | Unknown |
| 59 | Beige part, probably a capacitor | Likely |
| 60 and 61 | Contact strips next to J15 and J19 | Unknown |
| 62 and 63 | Gray parts, possibly inductors or fuses | Uncertain |
| 64, 65, 66, 67 | Groups of small capacitors and resistors | Likely |

For 64 to 66 I am not sure which chip on the top face sits above each group. The points `TP1` to `TP76` are test points and have their numbering printed on the board, so they were not numbered.

### What was left unnumbered

Only some of the small parts around the power manager (4) are marked, not all of them. On the bottom face, the swarm of capacitors and resistors is in groups. To number each one, close up photos in quadrants are needed.

---

## Limits of this document

* The identifications come from photos, not from measurements or from the schematic. The chip markings were read by eye and may have errors in the smallest letters.
* The numbers in the images are not those of any official schematic. They only link each photo to its table.
* Each board has its own numbering. Number 1 on the flight controller is not number 1 on the receiver.

## 5. Glossary

| Term | Meaning |
| --- | --- |
| **API** | Application Programming Interface: a defined way for software components to communicate. |
| **CRSF** | Crossfire Serial Protocol, a serial protocol used by radio-control equipment. |
| **ESC** | Electronic Speed Controller: hardware that drives a motor. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **UART** | Universal Asynchronous Receiver/Transmitter: an asynchronous serial interface. |
| **VTX** | Video transmitter: hardware that sends the aircraft video feed. |
