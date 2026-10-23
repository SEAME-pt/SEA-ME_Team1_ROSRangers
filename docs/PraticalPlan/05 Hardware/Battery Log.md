---
title: Battery Log
type: hardware
status: active
date: 2026-10-16
project: SEA autonomous drone
tags:
  - hardware
  - power
  - safety
---

# Battery Log

> [!info] One row per battery check
> Fill in a new row every time a battery comes back from storage, before a bench or cage session, and after any crash. Rules for LiPo handling are in [[Safety Rules]] #7–#8.

---

## 1. Requirement

**REQ-PWR-001**: Before any bench or cage session, the flight battery shall be inspected (no swelling, dents, torn wrap or damaged leads), the connector polarity through the XT30 ↔ XT60 adapter shall be checked, and the voltage of each cell shall be recorded in this log. A battery with any damage, or with a cell outside 3.5–4.20 V, shall not be used.

| Limit | Value | Source |
|---|---|---|
| Full charge | 4.20 V/cell (16.8 V for 4S) | LiPo standard |
| Storage | ~3.8 V/cell | LiPo standard |
| Land | 3.5 V/cell | [[Safety Rules]] #8 |

## 2. Batteries

| ID | Battery | Connector |
|---|---|---|
| B1 | CNHL 4S 850 mAh | XT60, used with the XT30 ↔ XT60 adapter |

## 3. Checks

| Date | Battery | Swelling / damage | Adapter polarity | Cells (V) | Pack (V) | Notes | Issue |
|---|---|---|---|---|---|---|---|
| 2026-10-16 | B1 | None | OK | 3.90 / 3.90 / 3.90 / 3.90 | 15.6 | First check on arrival. Cells are below full charge (4.20 V); charge at the lab station before a flight. | #19 |
| 2026-10-22 | B1 | None; wires and connector in good condition | — | 3.84 / 3.83 / 3.83 / 3.86 | 15.36 | Before the #22 bench session (props off). Measured with a multimeter. About half charge: enough for bench tests; charge to 4.20 V/cell before a flight. | #22 |

## 4. Glossary

| Term | Meaning |
| --- | --- |
| **4S** | Four LiPo cells in series. |
| **LiPo** | Lithium-polymer battery. |
| **Storage voltage** | Per-cell voltage at which a LiPo is kept when not in use, to slow ageing. |
| **XT30 / XT60** | Common power connectors for small and medium drone batteries. |
| **REQ** | Requirement ID of the form `REQ-<SYS, NAV, SEN, COM, CTL, SAF, PWR, SW or HW>-NNN`, used for traceability. |
| **XT60** | The battery connector on the pack. An XT30 to XT60 adapter is used on the drone. |
