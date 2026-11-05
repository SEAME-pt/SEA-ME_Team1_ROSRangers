#!/usr/bin/env python3
"""Build, run and benchmark the C++ protocol library tests (drone/lib/protocol).

Run without arguments for the menu, or give a command:

    python3 scripts/protocol_tests.py list
    python3 scripts/protocol_tests.py run                       # every test
    python3 scripts/protocol_tests.py run -f 'Receiver.*'       # one suite
    python3 scripts/protocol_tests.py run -f Frame.DecodesTheReferenceSetpoint
    python3 scripts/protocol_tests.py run --brief               # no explanations
    python3 scripts/protocol_tests.py bench -n 200 -f 'Sha256.*:HmacSha256.*'
    python3 scripts/protocol_tests.py bench -n 100 --csv /tmp/bench.csv

While running, each test shows what it does and why it matters. Those lines
come from drone/lib/protocol/tests/README.md: add a row there for a new test.

`-f` is a GoogleTest filter: `Suite.Name`, `Suite.*`, several joined with `:`,
exclusions after `-` (`*-FrameFuzz.*`).

Times are wall-clock times of this PC, measured by this script between the
GoogleTest start and end lines of each test, so they include about 10 µs of
pipe overhead: values under ~50 µs are mostly noise. They compare tests and
catch regressions; they do not predict ESP8266 timings.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO / "drone" / "lib" / "protocol"
DEFAULT_BUILD_DIR = Path(tempfile.gettempdir()) / "sd_protocol_build"
BINARY = "sd_protocol_tests"
CATALOGUE = SOURCE_DIR / "tests" / "README.md"
VECTOR_DIR = REPO / "drone" / "tests" / "vectors"
VECTOR_TEST = "Golden/Vector.Check/"  # test_vectors.cpp: one test per vector

SECTION_LINE = re.compile(r"^## (.+)")
ROW_LINE = re.compile(r"^\| `([^`]+)` \| (.+?) \| (.+?) \|$")
RUN_LINE = re.compile(r"^\[ RUN      \] (\S+)")
# Parameterized and typed tests add ", where GetParam() = ..." before the time.
END_LINE = re.compile(
    r"^\[ +(OK|FAILED|SKIPPED) +\] ([^\s,]+)(?:, where .*)? \(\d+ ms\)$"
)
REPEAT_LINE = re.compile(r"^Repeating all tests \(iteration (\d+)\)")

COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def paint(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if COLOR else text


GREEN, RED, YELLOW, DIM, BOLD = "32", "31", "33", "2", "1"


# ---------------------------------------------------------------- build


def build(build_dir: Path) -> Path:
    """Configures (first time only) and builds. Returns the test binary."""
    steps = []
    if not (build_dir / "CMakeCache.txt").exists():
        steps.append(["cmake", "-S", str(SOURCE_DIR), "-B", str(build_dir)])
    steps.append(["cmake", "--build", str(build_dir), "-j"])

    start = time.perf_counter()
    print(f"Building {SOURCE_DIR.relative_to(REPO)} in {build_dir} ...", flush=True)
    for step in steps:
        result = subprocess.run(step, capture_output=True, text=True)
        if result.returncode != 0:
            print(result.stdout + result.stderr)
            raise SystemExit(paint("Build failed.", RED))
    print(paint(f"Build OK ({time.perf_counter() - start:.1f} s)\n", DIM))
    return build_dir / BINARY


# ---------------------------------------------------------------- parsing


def parse_list(output: str) -> list[str]:
    """`--gtest_list_tests` output -> ['Suite.Name', ...]."""
    tests, suite = [], ""
    for raw in output.splitlines():
        line = raw.split("#", 1)[0].rstrip()  # drop "# GetParam() = ..." comments
        if not line or line.startswith("Running main()"):
            continue
        if not line.startswith(" "):
            suite = line.strip()
        else:
            tests.append(suite + line.strip())
    return tests


def parse_line(line: str) -> tuple[str, str] | None:
    """One GoogleTest output line -> ('run' | 'ok' | 'failed' | 'skipped' |
    'iteration', value), or None for any other line."""
    if m := RUN_LINE.match(line):
        return "run", m.group(1)
    if m := END_LINE.match(line):
        return m.group(1).lower(), m.group(2)
    if m := REPEAT_LINE.match(line):
        return "iteration", m.group(1)
    return None


@dataclass
class Catalogue:
    """Plain-language descriptions from tests/README.md."""

    section_of: dict[str, str] = field(default_factory=dict)  # test -> section title
    intro: dict[str, str] = field(default_factory=dict)  # section title -> intro
    does: dict[str, str] = field(default_factory=dict)
    why: dict[str, str] = field(default_factory=dict)


def parse_catalogue(text: str) -> Catalogue:
    cat, section = Catalogue(), ""
    for line in text.splitlines():
        if m := SECTION_LINE.match(line):
            section = m.group(1).strip()
        elif m := ROW_LINE.match(line.strip()):
            name, does, why = m.groups()
            cat.section_of[name] = section
            cat.does[name], cat.why[name] = does.strip(), why.strip()
        elif section and line.strip() and not line.startswith("|"):
            cat.intro.setdefault(section, line.strip())
    return cat


def add_vectors(cat: Catalogue, vector_dir: Path) -> None:
    """Golden vectors describe themselves: 'description' in each vector, and
    the row named after the file (e.g. `1_valid_frames`) in tests/README.md
    gives the section and the why."""
    for path in sorted(vector_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        group = path.stem
        for vector in data if isinstance(data, list) else [data]:
            name = VECTOR_TEST + vector.get("name", "?")
            cat.section_of[name] = cat.section_of.get(group, "Golden vectors")
            cat.does[name] = vector.get("description", vector.get("name", ""))
            cat.why[name] = cat.why.get(group, "Both implementations must agree")


def load_catalogue() -> Catalogue:
    try:
        cat = parse_catalogue(CATALOGUE.read_text())
    except OSError:
        return Catalogue()
    add_vectors(cat, VECTOR_DIR)
    return cat


def wrapped(label: str, text: str, indent: int) -> str:
    width = max(60, shutil.get_terminal_size().columns - 2)
    pad = " " * indent
    return textwrap.fill(
        text,
        width,
        initial_indent=pad + label,
        subsequent_indent=pad + " " * len(label),
    )


def summarize(samples_ms: list[float]) -> dict[str, float]:
    ordered = sorted(samples_ms)
    p95 = ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]
    return {
        "runs": len(ordered),
        "min": ordered[0],
        "median": statistics.median(ordered),
        "mean": statistics.fmean(ordered),
        "p95": p95,
        "max": ordered[-1],
    }


def fmt_s(seconds: float) -> str:
    return f"{seconds * 1000:.0f} ms" if seconds < 1 else f"{seconds:.2f} s"


def fmt_ms(ms: float) -> str:
    return f"{ms * 1000:7.0f} µs" if ms < 1 else f"{ms:7.2f} ms"


# ---------------------------------------------------------------- running


def list_tests(binary: Path, test_filter: str = "*") -> list[str]:
    result = subprocess.run(
        [str(binary), "--gtest_list_tests", f"--gtest_filter={test_filter}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return parse_list(result.stdout)


@dataclass
class RunResult:
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    times_ms: dict[str, list[float]] = field(default_factory=dict)
    wall_s: float = 0.0
    returncode: int = 0
    crashed_in: str = ""  # test that was running when the binary died
    timed_out: bool = False  # killed after TIMEOUT_S

    @property
    def ok(self) -> bool:
        return (
            bool(self.passed)
            and not self.failed
            and self.returncode == 0
            and not self.timed_out
        )

    def crash_message(self) -> str:
        where = f" during {self.crashed_in}" if self.crashed_in else ""
        if self.timed_out:
            return f"The test binary was stopped after {TIMEOUT_S} s{where}: it hung."
        how = (
            f"signal {-self.returncode}"
            if self.returncode < 0
            else f"exit code {self.returncode}"
        )
        return f"The test binary stopped with {how}{where}."


# A whole run of the binary longer than this is a hang: the binary is killed.
TIMEOUT_S = 600


def execute(binary: Path, test_filter: str, repeat: int, on_event) -> RunResult:
    """Runs the binary and calls on_event(kind, name, ms, detail_lines) for
    every start, end and repeat line. Failure messages are passed as
    detail_lines with the 'failed' event."""
    cmd = [str(binary), f"--gtest_filter={test_filter}", "--gtest_color=no"]
    if repeat > 1:
        cmd.append(f"--gtest_repeat={repeat}")

    result = RunResult()
    started_at, detail, current = 0.0, [], ""
    start = time.perf_counter()
    with subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
    ) as proc:
        fired = threading.Event()

        def kill() -> None:
            fired.set()
            proc.kill()

        watchdog = threading.Timer(TIMEOUT_S, kill)
        watchdog.daemon = True
        watchdog.start()
        for line in proc.stdout:
            now = time.perf_counter()
            event = parse_line(line.rstrip("\n"))
            if event is None:
                if started_at:
                    detail.append(line.rstrip("\n"))
                continue
            kind, value = event
            if kind == "run":
                started_at, detail, current = now, [], value
                on_event("run", value, 0.0, [])
            elif kind == "iteration":
                on_event("iteration", value, 0.0, [])
            else:
                ms = (now - started_at) * 1000
                result.times_ms.setdefault(value, []).append(ms)
                getattr(result, "passed" if kind == "ok" else kind).append(value)
                on_event(kind, value, ms, detail if kind == "failed" else [])
                started_at, current = 0.0, ""
        proc.wait()
        watchdog.cancel()
        result.timed_out = fired.is_set()
    result.wall_s = time.perf_counter() - start
    result.returncode = proc.returncode
    if current:  # the binary died inside a test: count that test as failed
        result.crashed_in = current
        result.failed.append(current)
        on_event("failed", current, (time.perf_counter() - started_at) * 1000, detail)
    return result


def run_tests(binary: Path, test_filter: str, explain: bool = True) -> int:
    tests = list_tests(binary, test_filter)
    if not tests:
        print(paint(f"No test matches '{test_filter}'.", YELLOW))
        return 1
    cat = load_catalogue() if explain else Catalogue()
    total, width = len(tests), max(len(t) for t in tests)
    digits = len(str(total))
    print(paint(f"Running {total} test(s), filter '{test_filter}'", BOLD))
    if explain:
        print(paint("Each test: what it does, and why it matters.", DIM))
    state = {"n": 0, "section": None}

    def show(kind: str, name: str, ms: float, detail: list[str]) -> None:
        if kind == "run":
            state["n"] += 1
            section = cat.section_of.get(name, name.split(".", 1)[0])
            if explain and section != state["section"]:
                state["section"] = section
                print("\n" + paint(f"── {section} ".ljust(70, "─"), BOLD))
                if section in cat.intro:
                    print(paint(wrapped("", cat.intro[section], 3), DIM))
            prefix = f"[{state['n']:>{digits}}/{total}]"
            print(f"{prefix} {name:<{width}} ", end="", flush=True)
            return
        if kind == "ok":
            print(paint("PASS", GREEN), paint(fmt_ms(ms), DIM))
        elif kind == "skipped":
            print(paint("SKIP", YELLOW))
        elif kind == "failed":
            print(paint("FAIL", RED), paint(fmt_ms(ms), DIM))
        if explain:
            indent = digits * 2 + 4
            if name in cat.does:
                print(wrapped("does: ", cat.does[name], indent))
                print(paint(wrapped("why:  ", cat.why[name], indent), DIM))
            else:
                print(
                    paint(" " * indent + "(no description in tests/README.md)", YELLOW)
                )
        if kind == "failed":
            print(paint("    What GoogleTest reported:", RED))
            for line in detail:
                print(paint("    │ ", RED) + line)

    result = execute(binary, test_filter, 1, show)

    if explain:
        print("\n" + paint("Summary by area:", BOLD))
        sections: dict[str, list[str]] = {}
        for name in tests:
            sections.setdefault(
                cat.section_of.get(name, name.split(".", 1)[0]), []
            ).append(name)
        passed = set(result.passed)
        for section, names in sections.items():
            good = sum(n in passed for n in names)
            mark = paint("✓", GREEN) if good == len(names) else paint("✗", RED)
            print(f"  {mark} {good:>2}/{len(names):<2} {section}")

    print("\n" + paint("Slowest:", BOLD))
    for name, samples in sorted(result.times_ms.items(), key=lambda kv: -kv[1][0])[:5]:
        print(f"  {fmt_ms(samples[0])}  {name}")
    ok = result.ok and len(result.passed) + len(result.skipped) == total
    status = paint("ALL PASSED", GREEN) if ok else paint("FAILED", RED)
    print(
        f"\n{status}: {len(result.passed)} passed, {len(result.failed)} failed, "
        f"{len(result.skipped)} skipped, {fmt_s(result.wall_s)}"
    )
    for name in result.failed:
        print(paint(f"  ✗ {name}", RED))
    if result.returncode:
        print(paint(result.crash_message(), RED))
    if explain and ok:
        print(paint("Every check above gave the expected result.", DIM))
    return 0 if ok else 1


def bench_tests(
    binary: Path, test_filter: str, repeat: int, csv_path: str | None
) -> int:
    tests = list_tests(binary, test_filter)
    if not tests:
        print(paint(f"No test matches '{test_filter}'.", YELLOW))
        return 1
    if csv_path:  # find a bad path now, not after the whole benchmark
        try:
            with open(csv_path, "a"):
                pass
        except OSError as error:
            print(paint(f"Cannot write {csv_path}: {error.strerror}", RED))
            return 1
    print(
        paint(
            f"Benchmark: {len(tests)} test(s) x {repeat} runs, filter '{test_filter}'",
            BOLD,
        )
    )
    print(paint("Wall-clock times on this PC, not on the ESP8266.\n", DIM))
    state = {
        "iteration": 1,
        "done": 0,
        "failed": 0,
        "start": time.perf_counter(),
        "shown": 0.0,
    }
    per_iteration = len(tests)

    def progress(kind: str, name: str, ms: float, detail: list[str]) -> None:
        if kind == "iteration":
            state["iteration"] = int(name)
        elif kind in ("ok", "failed", "skipped"):
            state["done"] += 1
            state["failed"] += kind == "failed"
            done = state["done"] / (per_iteration * repeat)
            now = time.perf_counter()
            if not sys.stdout.isatty():  # piped: one line per finished run
                if state["done"] % per_iteration == 0:
                    print(f"  run {state['iteration']}/{repeat} done", flush=True)
                return
            if done < 1 and now - state["shown"] < 0.1:
                return  # redraw at most 10 times a second
            state["shown"] = now
            bar = "#" * int(done * 30)
            line = (
                f"\r  run {state['iteration']:>{len(str(repeat))}}/{repeat} "
                f"[{bar:<30}] {done:4.0%}  {time.perf_counter() - state['start']:5.1f} s"
            )
            if state["failed"]:
                line += paint(f"  {state['failed']} failures", RED)
            print(line, end="", flush=True)

    result = execute(binary, test_filter, repeat, progress)
    print("\n")
    if result.returncode:
        print(paint(result.crash_message(), RED))
    if not result.times_ms:
        print(paint("No test finished, so there is nothing to measure.", RED))
        return 1

    rows = sorted(
        ((name, summarize(s)) for name, s in result.times_ms.items()),
        key=lambda row: -row[1]["mean"],
    )
    width = max(len(name) for name, _ in rows)
    header = f"{'test':<{width}}  {'min':>10}  {'median':>10}  {'mean':>10}  {'p95':>10}  {'max':>10}"
    print(paint(header, BOLD))
    for name, s in rows:
        cells = "  ".join(
            f"{fmt_ms(s[k]):>10}" for k in ("min", "median", "mean", "p95", "max")
        )
        print(f"{name:<{width}}  {cells}")
    print(
        f"\n{len(rows)} tests, {repeat} runs each, {fmt_s(result.wall_s)} total, "
        f"{result.wall_s / repeat * 1000:.1f} ms per full run"
    )

    if csv_path:
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                ["test", "runs", "min_ms", "median_ms", "mean_ms", "p95_ms", "max_ms"]
            )
            for name, s in rows:
                writer.writerow(
                    [name, s["runs"]]
                    + [f"{s[k]:.4f}" for k in ("min", "median", "mean", "p95", "max")]
                )
        print(f"Saved {csv_path}")

    if result.failed:
        print(
            paint(
                f"{len(result.failed)} failed runs: {sorted(set(result.failed))}", RED
            )
        )
    return 0 if result.ok else 1


# ---------------------------------------------------------------- menu


def choose(title: str, options: list[str]) -> int | None:
    """Numbered menu. Returns the index, or None for back/quit."""
    print(paint(f"\n{title}", BOLD))
    for number, label in enumerate(options, 1):
        print(f"  {number}) {label}")
    print("  q) Back / quit")
    while True:
        try:
            answer = input("> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if answer in ("q", "b", ""):
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return int(answer) - 1
        print("Enter one of the numbers.")


def ask(prompt: str, default: str) -> str | None:
    """Returns the answer, the default for an empty line, or None (go back)
    when the input ends or Ctrl+C is pressed."""
    try:
        return input(f"{prompt} [{default}]: ").strip() or default
    except (EOFError, KeyboardInterrupt):
        print()
        return None


def pick_filter(binary: Path) -> str | None:
    tests = list_tests(binary)
    suites = sorted({t.split(".", 1)[0] for t in tests})
    scope = choose("Which tests?", ["All", "One suite", "One test", "Type a filter"])
    if scope is None:
        return None
    if scope == 0:
        return "*"
    if scope == 1:
        counts = [f"{s} ({sum(t.startswith(s + '.') for t in tests)})" for s in suites]
        i = choose("Suite", counts)
        return None if i is None else f"{suites[i]}.*"
    if scope == 2:
        i = choose("Suite", suites)
        if i is None:
            return None
        names = [t for t in tests if t.startswith(suites[i] + ".")]
        j = choose("Test", [n.split(".", 1)[1] for n in names])
        return None if j is None else names[j]
    return ask("GoogleTest filter", "*")


def ask_runs() -> int | None:
    while True:
        answer = ask("Runs per test", "100")
        if answer is None:
            return None
        if answer.isdigit() and int(answer) > 0:
            return int(answer)
        print("Enter a whole number above 0.")


def menu(build_dir: Path, no_build: bool = False) -> int:
    binary = build_dir / BINARY if no_build else build(build_dir)
    actions = ["Run tests", "Benchmark tests", "List tests", "Rebuild"]
    while True:
        action = choose("Protocol library tests", actions)
        if action is None:
            return 0
        if action == 3:
            binary = build(build_dir)
            continue
        if action == 2:
            for name in list_tests(binary):
                print(f"  {name}")
            continue
        test_filter = pick_filter(binary)
        if test_filter is None:
            continue
        print()
        try:
            if action == 0:
                run_tests(binary, test_filter)
            else:
                repeat = ask_runs()
                if repeat is None:
                    continue
                csv_path = ask("Save CSV to (empty = no)", "")
                if csv_path is None:
                    continue
                bench_tests(binary, test_filter, repeat, csv_path or None)
        except KeyboardInterrupt:
            print(paint("\nInterrupted.", YELLOW))


# ---------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--build-dir", type=Path, default=DEFAULT_BUILD_DIR)
    parser.add_argument(
        "--no-build", action="store_true", help="use the existing binary"
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("list", help="list the tests")
    run_p = sub.add_parser("run", help="run tests and show each one")
    run_p.add_argument("-f", "--filter", default="*")
    run_p.add_argument(
        "--brief", action="store_true", help="one line per test, no explanations"
    )
    bench_p = sub.add_parser(
        "bench", help="run tests N times and show timing statistics"
    )
    bench_p.add_argument("-f", "--filter", default="*")
    bench_p.add_argument("-n", "--runs", type=int, default=100)
    bench_p.add_argument("--csv", help="save the statistics to this CSV file")
    args = parser.parse_args(argv)

    if shutil.which("cmake") is None and not args.no_build:
        raise SystemExit("cmake not found. Install it or use --no-build.")
    if args.no_build and not (args.build_dir / BINARY).exists():
        raise SystemExit(f"No {BINARY} in {args.build_dir}. Run without --no-build.")
    try:
        if args.command is None:
            return menu(args.build_dir, args.no_build)
        binary = args.build_dir / BINARY if args.no_build else build(args.build_dir)
        if args.command == "list":
            for name in list_tests(binary):
                print(name)
            return 0
        if args.command == "run":
            return run_tests(binary, args.filter, explain=not args.brief)
        return bench_tests(binary, args.filter, max(1, args.runs), args.csv)
    except subprocess.CalledProcessError as error:
        print(paint(f"{BINARY} failed: {error.stderr or error}", RED), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(paint("\nInterrupted.", YELLOW))
        return 130


if __name__ == "__main__":
    sys.exit(main())
