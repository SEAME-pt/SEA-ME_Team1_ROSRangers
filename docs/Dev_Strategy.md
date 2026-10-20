---
title: SEA:ME — Software Development & Git Workflow
type: process
status: active
date: 2026-10-15
project: SEA autonomous drone
tags: [process, git, ci]
---

# SEA:ME — Software Development & Git Workflow

> [!info] Project
> **SEA:ME — Autonomous Drone**
>
> **Duration:** 6 months
> **Development model:** GitHub + GitHub Actions
> **Documentation:** Obsidian
> **Repository model:** Separate code and documentation repositories

---

## 1. Purpose

This document defines how software will be developed, reviewed, tested and integrated during the SEA:ME autonomous drone project.

The goal is to use a **professional but lightweight development process** appropriate for a 6-month project with real hardware.

The process should provide:

- controlled integration of code
- protection of stable software
- automated verification
- code review
- traceability from requirements to implementation and tests
- clear separation between development and stable software
- integration between software, simulation, hardware, and flight testing

The process should **not become an administrative burden**. Practices should be introduced when they provide real value to the project.

---

# 2. Repository Structure

The project uses two separate repositories.

### Software Repository

Contains the actual implementation:

- source code
- tests
- build configuration
- CI configuration
- scripts
- simulation code
- hardware-interface code
- configuration required to build and test the software

Example:

```text
sea-me-drone/
├── src/
├── include/
├── tests/
├── simulation/
├── scripts/
├── config/
├── .github/
│   ├── workflows/
│   ├── pull_request_template.md
│   └── ...
├── CMakeLists.txt
└── README.md
```

### Documentation Repository

The Obsidian repository contains:

- requirements
- architecture
- design decisions
- system documentation
- testing strategy
- test plans
- hardware documentation
- meeting notes
- ADRs
- project planning
- links to GitHub implementation work

The documentation repository is **not intended to contain the source code**.

---

# 3. Responsibilities of Each Repository

| Information | Location |
|---|---|
| Source code | GitHub software repository |
| Unit tests | GitHub software repository |
| Integration tests | GitHub software repository |
| CI configuration | GitHub software repository |
| Build configuration | GitHub software repository |
| Requirements | Obsidian |
| System architecture | Obsidian |
| Design decisions | Obsidian |
| Test plans | Obsidian |
| Test results requiring documentation | Obsidian |
| Flight-test reports | Obsidian |
| Issues / implementation tasks | GitHub Issues |
| Code review | GitHub Pull Requests |
| Stable source code | `main` |
| Integrated development code | `dev` |

---

# 4. Development Branch Model

The project uses two permanent branches:

```text
main
 │
 │ stable
 │
 ↑
dev
 │
 │ integrated development
 │
 ├── feature/*
 ├── bugfix/*
 └── other temporary branches
```

## `main`

`main` represents the **always stable** version of the software.

Code should only reach `main` when it has passed the validation appropriate for the milestone.

`main` should therefore be:

- protected
- buildable
- tested
- reviewable
- suitable for implementation to the drone

There should be **no direct pushes to `main`**.

---

## `dev`

`dev` represents the **integrated development state**.

It is where completed features and fixes are combined.

`dev` may contain unfinished project work elsewhere, but code merged into it should still:

- build successfully
- pass the normal automated CI checks
- not knowingly break existing functionality

There should be **no direct pushes to `dev`**.

---

# 5. Feature and Bug-Fix Branches

Development work is performed on temporary branches.

Examples:

```text
feature/sensor-interface
feature/altitude-control
feature/telemetry
feature/waypoint-navigation
feature/simulation-environment

bugfix/gps-timeout
bugfix/motor-controller
bugfix/telemetry-packet
```

Branches should have a clear relationship to the work being performed.

Where appropriate, the branch should correspond to a GitHub Issue.

Example:

```text
Issue #42 — Implement GPS interface

        ↓

feature/42-gps-interface

        ↓

Pull Request

        ↓

dev
```

>*Note: Check [[Feature_and_Bugfix_Branches|Branch Basics]] documentation in case of any doubt*

---

# 6. Pull Request Workflow

The normal development workflow is:

```text
Issue
  ↓
Feature / Bugfix branch
  ↓
Implementation
  ↓
Local tests
  ↓
Rebase onto latest dev if necessary
  ↓
Pull Request → dev
  ↓
Code review
  ↓
GitHub Actions
  ↓
Squash merge
  ↓
dev
```

Promotion to a stable version:

```text
dev
  ↓
Validation
  ↓
Pull Request → main
  ↓
Code review
  ↓
CI
  ↓
Hardware / simulation validation as required
  ↓
Merge commit
  ↓
main
```

>*Note: Check [[Pull_Request_Workflow|Pull Requests Basics]] documentation in case of any doubt*

---

# 7. Rebase Policy

Rebase is used to keep personal development branches synchronized with the current base branch.

For example:

```text
feature
 │
 ├── A ── B ── C
 │
 └── local
      └── D ── E
```

If `dev` has progressed:

```text
feature
 │
 ├── A ── B ── C ── F ── G
 │
 └── local
      └── D ── E
```

The developer can rebase his local changes into the feature `branch`:

```text
feature
 │
 └── A ── B ── C ── F ── G
                         \
                          D' ── E'
```

This allows conflicts to be resolved before the Pull Request is merged.

## Rebase Rules

### Allowed

Rebase:

- your own feature branch
- your own bug-fix branch
- branches that nobody else is actively developing on

### Avoid

Do not rebase:

- `main`
- `dev`
- shared branches
- a branch another developer is actively working on

The reason is that rebasing rewrites commit history.

>*Note: Check [[Rebase_Policy|Rebase Basics]] documentation in case of any doubt*

---

# 8. Merge Strategy

Different types of merges serve different purposes.

## Feature → `dev`

Use **Squash Merge**.

Example:

```text
feature/telemetry
    │
    ├── commit
    ├── commit
    ├── fix typo
    ├── fix tests
    └── another fix
             │
             ↓
        Squash Merge
             │
             ↓
            dev
```

The result is one meaningful commit representing the completed change.

Example:

```text
dev

* Add telemetry communication system
* Implement GPS interface
* Fix sensor initialization
```

This keeps the `dev` history easier to understand.

---

## `dev` → `main`

Use a **normal merge commit**.

The merge represents a deliberate promotion of the integrated development state into the stable branch.

Example:

```text
dev
 │
 ├── Feature A
 ├── Feature B
 ├── Bugfix C
 │
 └────── Merge → main
```

This creates a visible point in history where a stable version was promoted.

It also makes project milestones easier to identify.

>*Note: Check [[Merge_Strategy|Merge Strategy]] documentation in case of any doubt*

---

# 9. Branch Protection

Both permanent branches should be protected.

## `dev`

Recommended requirements:

- Pull Request required
- Direct pushes disabled
- CI checks required
- At least one reviewer where practical
- Branch must pass automated tests before merging
- No force pushes

## `main`

Stricter requirements:

- Pull Request required
- Direct pushes disabled
- CI checks required
- **Two approvals**
- No force pushes
- Conversations must be resolved
- Required checks must pass
- Hardware/simulation validation required when appropriate for the stable version

The exact requirements can be adjusted depending on team size and project workload.

> [!note]
> Two reviewers on every single `dev` PR may create unnecessary delays for a small team. The stronger review requirement belongs on `main`.

>*Note: Check [[Branch_Protection|Branch Protection]] documentation in case of any doubt*

---

# 10. Continuous Integration

GitHub Actions will automatically verify Pull Requests and important branch updates.

The initial CI pipeline should focus on things that provide direct value to the project.

## Required CI Checks

### Build

Verify that the project can be built successfully.

```text
Source
  ↓
Build
  ↓
Success / Failure
```

### Unit Tests

Run automated tests for individual components.

Examples:

- sensor calculations
- coordinate conversions
- navigation algorithms
- communication parsing
- state machines
- utility functions

### Integration Tests

Verify that multiple components work together.

Examples:

- sensor → navigation
- communication → command handling
- navigation → actuator control
- telemetry → communication layer

### Static Analysis

Use appropriate tools for the project's languages.

Examples:

- compiler warnings
- `clang-tidy`
- `cppcheck`
- other relevant analyzers

### Formatting / Style

Automatically verify code formatting where practical.

---

# 11. CI Pipeline

A simplified pipeline:

```text
Pull Request
     │
     ├── Build
     │
     ├── Unit Tests
     │
     ├── Integration Tests
     │
     ├── Static Analysis
     │
     └── Formatting
          │
          ↓
       PASS / FAIL
          │
          ↓
      Code Review
          │
          ↓
       Merge
```

CI should run automatically for:

- Pull Requests into `dev`
- Pull Requests into `main`
- pushes to protected branches after merges

---

# 12. Hardware and Simulation Testing

Automated CI cannot replace testing on the actual drone.

The project therefore distinguishes between different levels of validation.

## Level 1 — Unit Testing

Tests individual software components.

Example:

```text
GPS parser
Altitude calculation
PID controller
Command parser
```

## Level 2 — Integration Testing

Tests several software components together.

Example:

```text
GPS
 ↓
Navigation
 ↓
Flight controller
```

## Level 3 — Simulation

Test the system without physical hardware where possible.

Examples:

- simulated sensor data
- simulated movement
- navigation scenarios
- failure scenarios
- communication loss
- invalid sensor data

## Level 4 — Hardware / Bench Testing

Test software against the real electronics without flight.

Examples:

- sensors
- motors
- communication interfaces
- flight controller
- power-related behavior
- sensor failures

## Level 5 — Flight Testing

Controlled testing with the complete drone.

Examples:

- takeoff
- position holding
- waypoint navigation
- autonomous behavior
- recovery behavior
- communication loss
- emergency behavior

---

# 13. Test Pyramid

Testing should generally follow this structure:

```text
             Flight Tests
            ───────────────
          Hardware / Bench
        ─────────────────────
             Simulation
      ─────────────────────────
        Integration Tests
   ───────────────────────────────
          Unit Tests
────────────────────────────────────
```

The lower levels should provide fast feedback.

The higher levels are more expensive and potentially riskier.

Therefore, expensive hardware and flight testing should not be used to discover basic software errors that could have been caught automatically.

---

# 14. Requirements Traceability

The project should maintain lightweight traceability between requirements, implementation, and validation.

The goal is not to build an elaborate requirements-management system.

The basic relationship is:

```text
Requirement
     ↓
GitHub Issue
     ↓
Implementation
     ↓
Pull Request
     ↓
Automated Tests
     ↓
Hardware / Simulation / Flight Test
```

Each important requirement should have a unique identifier.

Example:

```text
REQ-NAV-001
REQ-NAV-002
REQ-COM-001
REQ-SAF-001
```

Example requirement:

```text
REQ-NAV-001

The drone shall be capable of navigating to a predefined waypoint.
```

Implementation:

```text
GitHub Issue #42
feature/42-waypoint-navigation
```

Pull Request:

```text
PR #57
Implement waypoint navigation

Implements REQ-NAV-001
```

Tests:

```text
TEST-NAV-001
Waypoint navigation reaches target within defined tolerance.
```

This creates a simple chain:

```text
REQ-NAV-001
    ↓
Issue #42
    ↓
PR #57
    ↓
Implementation
    ↓
TEST-NAV-001
    ↓
Simulation / Flight Test
```

---

# 15. Requirement IDs

Requirement IDs should be stable.

Suggested categories:

| Prefix | Category |
|---|---|
| `REQ-SYS` | System |
| `REQ-NAV` | Navigation |
| `REQ-SEN` | Sensors |
| `REQ-COM` | Communication |
| `REQ-CTL` | Control |
| `REQ-SAF` | Safety |
| `REQ-PWR` | Power |
| `REQ-SW` | Software |
| `REQ-HW` | Hardware |

Example:

```text
REQ-SEN-001
REQ-NAV-001
REQ-NAV-002
REQ-SAF-001
```

Requirement IDs should be referenced in relevant:

- GitHub Issues
- Pull Requests
- tests
- documentation
- test reports

---

# 16. GitHub Issues

GitHub Issues are used for implementation work.

An issue should answer:

- What needs to be done?
- Why is it needed?
- Which requirement does it address?
- What are the acceptance criteria?

Example:

```text
#42 Implement waypoint navigation

Requirement:
REQ-NAV-001

Goal:
Implement the first version of waypoint navigation.

Acceptance criteria:
- Accept a predefined waypoint.
- Calculate navigation direction.
- Handle arrival tolerance.
- Provide test coverage.
- Work in simulation.
```

---

# 17. Pull Request Requirements

A Pull Request should provide enough information for another developer to understand the change.

Suggested PR template:

```text
## Description

What was changed?

## Related Issue

Closes #XX

## Requirements

REQ-XXX-XXX

## Testing

- [ ] Build passes
- [ ] Unit tests pass
- [ ] Integration tests pass
- [ ] Simulation tested
- [ ] Hardware tested
- [ ] Flight tested

## Notes

Anything reviewers should know?
```

Not every change requires every type of test.

The developer should explicitly identify which validation was relevant.

---

# 18. Code Review

Code review is intended to catch:

- bugs
- unclear design
- unnecessary complexity
- incorrect assumptions
- missing tests
- safety problems
- maintainability problems

Reviewers should not focus exclusively on style.

Important questions include:

- Does this solve the intended problem?
- Does it satisfy the requirement?
- Could this fail on real hardware?
- What happens with invalid input?
- What happens when communication is lost?
- What happens when a sensor provides bad data?
- Are failure modes handled?
- Are tests sufficient?

---

# 19. Architecture Decisions

Important architectural decisions should be recorded in Obsidian using ADRs.

Example:

```text
ADR-001 — Communication Protocol

Status: Accepted

Context:
The drone requires communication between...

Decision:
Use XYZ protocol...

Consequences:
...
```

Suggested ADR topics:

- communication protocol
- sensor architecture
- navigation architecture
- flight-control architecture
- simulation architecture
- hardware/software boundaries
- error handling
- safety mechanisms

ADRs should document **why** a decision was made, not just what was implemented.

---

# 20. Versioning strategy

Stable versions should be tagged in Git.

Example:

```text
v0.1.0
v0.2.0
v0.3.0
```

During development, versions can remain below `1.0.0`.

Example:

```text
v0.1.0 — Initial hardware integration
v0.2.0 — Sensor integration
v0.3.0 — Navigation prototype
v0.4.0 — Autonomous navigation
```

The exact versioning strategy can evolve with the project.

A stable version should correspond to a known state of `main`.

---

# 21. Stable Validation

Before promoting a significant version to `main`, verify the relevant validation levels.

Example:

```text
Automated CI
     ↓
Simulation
     ↓
Hardware Bench Test
     ↓
Controlled Flight Test
     ↓
Release
```

Not every stable version needs a flight test.

The required validation depends on what changed.

For example:

### Documentation change

```text
CI → Review → Merge
```

### Navigation algorithm change

```text
CI
 ↓
Unit Tests
 ↓
Integration Tests
 ↓
Simulation
 ↓
Hardware
 ↓
Flight Test if appropriate
```

---

# 22. Safety Considerations

Because this project controls physical hardware, software validation must consider consequences beyond a normal software project.

Particular attention should be given to:

- unexpected actuator commands
- sensor failures
- invalid sensor data
- communication loss
- unexpected state transitions
- initialization failures
- loss of localization
- navigation errors
- emergency behavior

Flight testing should be performed incrementally.

A software change should not go directly from:

```text
Code → Full autonomous flight
```

without appropriate intermediate validation.

---

# 23. What We Are Deliberately NOT Doing Initially

The project has only six months, so the development process should avoid unnecessary enterprise complexity.

The following are **not initial priorities**:

- complicated multi-stage deployment pipelines
- `staging` / `production` environments
- merge queues
- elaborate stable version automation
- SBOM infrastructure unless required
- complex artifact promotion systems
- extensive automated requirements databases
- excessive branching strategies
- complicated security infrastructure
- full Continuous Deployment

These can be introduced later if the project demonstrates a real need for them.

# 24. Overall Workflow

The complete development process can be summarized as:

```text
                    REQUIREMENT
                         │
                         ↓
                    GitHub Issue
                         │
                         ↓
                 Feature / Bugfix
                      Branch
                         │
                         ↓
                    Development
                         │
                         ↓
                  Local Testing
                         │
                         ↓
              Rebase onto latest dev
                  when necessary
                         │
                         ↓
                  Pull Request
                       → dev
                         │
              ┌──────────┴──────────┐
              ↓                     ↓
          Code Review              CI
              │                     │
              └──────────┬──────────┘
                         ↓
                   Squash Merge
                         │
                         ↓
                        dev
                         │
                         ↓
                 Integration / Test
                         │
                         ↓
                  Validation / Integration Testing
                         │
                         ↓
                  Pull Request
                       → main
                         │
              ┌──────────┴──────────┐
              ↓                     ↓
        Code Review          CI / Validation
              │                     │
              └──────────┬──────────┘
                         ↓
                    Merge Commit
                         │
                         ↓
                       main
```

---

# 25. Guiding Principle

> **Automate what is cheap, review what is important, and test on hardware what cannot be trusted to simulation.**

The development process exists to help the team build a reliable autonomous drone within six months.

The process itself should never become more complicated than the problem it is solving.
## 26. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **Branch** | An independent line of development in version control. |
| **CI** | Continuous Integration: automated validation of proposed changes. |
| **Commit** | A recorded snapshot of changes in a version-control repository. |
| **PR** | Pull Request: a proposed change submitted for review before merging. |
| **REQ** | Requirement ID of the form REQ-<SYS, NAV, SEN, COM, CTL, SAF, PWR, SW or HW>-NNN, used for traceability. |
| **Telemetry** | Periodic data sent from a system to report state, measurements, or health. |
