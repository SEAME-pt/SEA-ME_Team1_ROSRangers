# SEA-ME Team 1 (ROS Rangers): Code and docs repo

Software and documentation for the SEA:ME 2026/27 autonomous drone: ESP8266 (D1 mini) firmware, C++ protocol library, ROS 2 ground nodes, simulation, CI and HIL tests, plus the plan, decisions (ADRs), architecture, hardware notes and team process.

The documentation is an Obsidian vault in `docs/`. It was written in the team's docs repo (`ptorrao-git/SeameDocumentation`, folder `vaultSeame/`) and is copied here under issue #36. Check the relevant ADR before changing a design decision.

## Layout

| Path | What |
|---|---|
| `docs/` | Documentation vault (see "Where to look in the docs") |
| `drone/firmware/esp8266/` | D1 mini firmware (PlatformIO, env `d1_mini`, `d1_mini_ota`) |
| `drone/lib/protocol/` | C++ protocol library (CMake + GoogleTest, shared with firmware) |
| `drone/ground/src/sd_protocol_py/` | Python implementation of the same protocol |
| `drone/ground/src/flight_interface/` | ROS 2 lifecycle node (ground ↔ drone) |
| `drone/ground/src/gesture_node/` | Gesture recognition → flight interface |
| `drone/config/betaflight/` | Betaflight `diff all` / `status` baselines per version |
| `drone/simulation/` | PX4 SITL / Gazebo setup |
| `drone/tests/{sitl,hil,vectors}/` | SITL tests, HIL bench tests, golden test vectors shared by C++ and Python |

## Where to look in the docs

Paths are relative to `docs/`. Start at `PraticalPlan/00 Start/Home.md` (map of everything).

| Need | File |
|---|---|
| What we do this week / sprint | `PraticalPlan/01 Plan/Study Plan v2.md` (Sprint 0 + Module 1, by week and track) |
| Long-term plan (Modules 2–3) | `PraticalPlan/01 Plan/Roadmap to April 2027.md` |
| Who owns what (tracks A–E) | `PraticalPlan/01 Plan/Team Tracks.md` |
| Rituals (stand-up, demos, weekly checklist) | `PraticalPlan/01 Plan/Weekly Rituals.md`, `Dailys.md` |
| Decisions | `PraticalPlan/02 Decisions/Decision Register.md`. `ADR-0NN *.md` are the accepted decisions. |
| Architecture, protocol, wiring | `PraticalPlan/03 Architecture/` |
| CI/CD, repo layout, HIL | `PraticalPlan/04 CI-CD/` |
| Safety rules (read before any hardware task) | `PraticalPlan/05 Hardware/Safety Rules.md` |
| Bench tests (ESP-S1-xx, Stage 2) | `PraticalPlan/05 Hardware/ESP Bench Test Plan.md` |
| Open questions for instructors / physical checks | `PraticalPlan/07 Reference/Open Questions.md` |
| Dev process (Git, PRs, REQ IDs, issues) | `Dev_Strategy.md`, `Git Learning/` |
| Phases 1–6 | `Development_Plan.md` |
| Old material (don't use as a source) | `Outdated/` |

`To Do.md` is legacy. **Tasks live in GitHub Issues and the project board**, not in the docs.

## Docs conventions

- English only. Notes have YAML frontmatter (`title`, `type`, `status`, `date`, `tags`).
- Link with Obsidian wikilinks: `[[Note Name]]`, `[[ADR-011 FC UART Allocation|ADR-011]]`.
- Dates are absolute (`2026-10-14`), never "next week".
- Every page ends with a `Glossary` section.
- Don't commit `.obsidian/` workspace files.
- To change a decision, add a dated entry to its ADR or write a new ADR that supersedes it. Never edit silently.

## Git workflow

- `main` (stable) and `dev` (integration) are protected. Never push to them directly.
- Branch from `dev`: `feature/<issue>-<topic>` or `bugfix/<issue>-<topic>`. Every branch has an issue, and every feature goes through a PR into `dev`.
- PR into `dev` using `.github/pull_request_template.md`. Put `Closes #N` in the description, or `Refs #N` if the issue must stay open (for example a spike that still needs bench results).
- Feature → `dev`: squash merge. `dev` → `main`: merge commit + tag (`v0.1.0` at Module 1 end).
- Two reviewers from outside the author's track (`PraticalPlan/01 Plan/Team Tracks.md` §2).
- Requirement IDs: `REQ-<SYS|NAV|SEN|COM|CTL|SAF|PWR|SW|HW>-NNN` (`Dev_Strategy.md` §15).
- Commit messages: short imperative subject (`Add issue templates and label set`).

## Issues and board

- Issue forms in `.github/ISSUE_TEMPLATE/`: Task, Bug, Incident/crash, Test session, Spike. Blank issues are disabled.
- Labels are defined in `.github/labels.yml`. Change them there, then run `scripts/sync-labels.sh` (needs `gh` + `yq` v4).
- Each issue gets one `type:` label (task/bug/incident/test/spike/docs/chore), one `track:` label (A flight-safety, B embedded-link, C ground-ros2, D ci-test, E sim-gestures, shared) and one priority label (`P0 🔴`/`P1 🟠`/`P2 🟡`). Flags: `safety-critical`, `deliverable`, `stretch`, `blocked`, `needs-instructor`, `props-off`, `in-cage-flight`.
- Board: GitHub Project #111 "Team1 ROSRangers Board", https://github.com/orgs/SEAME-pt/projects/111 (project node id `PVT_kwDOCx6Z1M4Bm4OL`).
  - Status: Backlog → Todo (this sprint) → In Progress → In Review → Done, plus Blocked.
  - Fields: Sprint (2-week iterations: Sprint 0 = 7–18 Oct 2026, Sprint 1 from 19 Oct, … Sprint 4 ends 13 Dec), Phase (Development Plan 1–6), Requirement (text).
  - Automations: auto-add from the repo, item added → Backlog, PR linked → In Review, closed → Done, auto-archive.
  - New sprint: add the next iterations to the Sprint field (Module 2 starts 14 Dec), then turn the next Study Plan weeks into issues with the issue forms.
- `close-linked-issues.yaml` closes linked issues when a PR merges into `dev`. GitHub only does this by itself for `main`.

## CI

- `ci.yaml`: `lint` (pre-commit) always runs. `check-components` turns each job on only when its component exists:
  `drone/lib/protocol/CMakeLists.txt` → `protocol-cpp`, `drone/ground/src/sd_protocol_py/pyproject.toml` → `ground-python`, `drone/firmware/esp8266/platformio.ini` → `esp-firmware`, both + `drone/tests/sitl/*.py` → `betaflight-sitl`. Skips print a `::notice`.
- `hil.yaml` runs on the self-hosted `hil-drone` runner when firmware and `drone/tests/hil/*.py` exist. No LiPo on the HIL bench, ever.
- Adding a component turns its job on immediately. Ship at least one real test with it: empty pytest and coverage gates fail.
- Run `pre-commit run --all-files` before pushing (end-of-file, whitespace, check-yaml, ruff, clang-format).

## Gotchas

- `Closes #N` does not close issues on PRs into `dev` by itself (see `close-linked-issues.yaml` above).
- The sidebar "Development" links on an issue act like `Closes`. They can only be removed in the browser.
- Use `gh` from cli.github.com, not Ubuntu's apt version (2.4 has no `gh project`/`gh label`, and `gh pr edit` breaks). Board and field changes go through `gh api graphql`. The board's view grouping and workflow targets can only be changed in the UI.
- Granting people access (project collaborators, repo permissions) is blocked in auto mode. Ask the user to do it in the UI.

## Safety

Hardware work follows `docs/PraticalPlan/05 Hardware/Safety Rules.md`: props off on the bench, the radio and the safety pilot always win over code, gestures never arm. Every crash, incident or fault is filed with the Incident form (Safety Rules #12). Never cut `safety-critical` work.
