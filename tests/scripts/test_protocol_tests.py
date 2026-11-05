import pathlib
import sys

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import protocol_tests  # noqa: E402

LIST_OUTPUT = """Running main() from gtest_main.cc
Frame.
  EncodesReferenceSetpoint
  RoundTrip
Param/Window.
  Accepts/0  # GetParam() = 1
"""

# What a GoogleTest binary prints for one passing and one failing test.
FAKE_RUN = r"""#!/bin/sh
case "$1" in
--gtest_list_tests)
  printf 'Suite.\n  Good\n  Bad\n'
  ;;
*)
  echo '[==========] Running 2 tests from 1 test suite.'
  echo '[ RUN      ] Suite.Good'
  echo '[       OK ] Suite.Good (0 ms)'
  echo '[ RUN      ] Suite.Bad'
  echo 'test.cpp:12: Failure'
  echo 'Expected equality of these values:'
  echo '[  FAILED  ] Suite.Bad (1 ms)'
  echo '[==========] 2 tests from 1 test suite ran. (1 ms total)'
  echo '[  FAILED  ] Suite.Bad'
  ;;
esac
"""


@pytest.fixture
def fake_binary(tmp_path):
    binary = tmp_path / "sd_protocol_tests"
    binary.write_text(FAKE_RUN)
    binary.chmod(0o755)
    return binary


def test_parse_list_joins_suite_and_name_and_drops_comments():
    assert protocol_tests.parse_list(LIST_OUTPUT) == [
        "Frame.EncodesReferenceSetpoint",
        "Frame.RoundTrip",
        "Param/Window.Accepts/0",
    ]


@pytest.mark.parametrize(
    "line, expected",
    [
        ("[ RUN      ] Frame.RoundTrip", ("run", "Frame.RoundTrip")),
        ("[       OK ] Frame.RoundTrip (0 ms)", ("ok", "Frame.RoundTrip")),
        ("[  FAILED  ] Frame.RoundTrip (12 ms)", ("failed", "Frame.RoundTrip")),
        ("[  SKIPPED ] Frame.RoundTrip (0 ms)", ("skipped", "Frame.RoundTrip")),
        ("Repeating all tests (iteration 3) . . .", ("iteration", "3")),
        ("[  FAILED  ] Frame.RoundTrip", None),  # final summary line, no time
        ("[----------] 2 tests from Frame", None),
    ],
)
def test_parse_line(line, expected):
    assert protocol_tests.parse_line(line) == expected


def test_summarize():
    s = protocol_tests.summarize([4.0, 1.0, 3.0, 2.0])
    assert (s["runs"], s["min"], s["max"]) == (4, 1.0, 4.0)
    assert s["median"] == 2.5
    assert s["mean"] == 2.5
    assert s["p95"] == 4.0


def test_fmt_ms_switches_to_microseconds_below_one_ms():
    assert protocol_tests.fmt_ms(0.042).strip() == "42 µs"
    assert protocol_tests.fmt_ms(12.5).strip() == "12.50 ms"


def test_run_shows_each_test_and_the_failure_message(fake_binary, capsys):
    assert protocol_tests.run_tests(fake_binary, "*") == 1
    out = capsys.readouterr().out
    assert "[1/2] Suite.Good" in out and "PASS" in out
    assert "[2/2] Suite.Bad" in out and "FAIL" in out
    assert "test.cpp:12: Failure" in out
    assert "1 passed, 1 failed" in out


def test_run_with_no_matching_test_fails(fake_binary, monkeypatch, capsys):
    monkeypatch.setattr(protocol_tests, "list_tests", lambda binary, f="*": [])
    assert protocol_tests.run_tests(fake_binary, "Nothing.*") == 1
    assert "No test matches" in capsys.readouterr().out


def test_bench_writes_csv_and_reports_failures(fake_binary, tmp_path, capsys):
    csv_path = tmp_path / "bench.csv"
    assert protocol_tests.bench_tests(fake_binary, "*", 1, str(csv_path)) == 1
    lines = csv_path.read_text().splitlines()
    assert lines[0].startswith("test,runs,min_ms")
    assert {line.split(",")[0] for line in lines[1:]} == {"Suite.Good", "Suite.Bad"}
    assert "failed runs" in capsys.readouterr().out


def test_menu_choice(monkeypatch):
    answers = iter(["9", "2"])
    monkeypatch.setattr("builtins.input", lambda _="": next(answers))
    assert protocol_tests.choose("Pick", ["a", "b"]) == 1
    monkeypatch.setattr("builtins.input", lambda _="": "q")
    assert protocol_tests.choose("Pick", ["a", "b"]) is None


CATALOGUE = """# Title

## SeqWindow: the message number rule
Accept only higher numbers.

| Test | What it does | Why it matters |
|---|---|---|
| `Suite.Good` | 1, 2, 3 are accepted | The normal case |
"""


def test_parse_catalogue():
    cat = protocol_tests.parse_catalogue(CATALOGUE)
    assert cat.section_of["Suite.Good"] == "SeqWindow: the message number rule"
    assert (
        cat.intro["SeqWindow: the message number rule"] == "Accept only higher numbers."
    )
    assert cat.does["Suite.Good"] == "1, 2, 3 are accepted"
    assert cat.why["Suite.Good"] == "The normal case"
    assert "Test" not in cat.does  # the table header is not a test


def test_real_catalogue_describes_every_test_in_the_sources():
    sources = (protocol_tests.SOURCE_DIR / "tests").glob("test_*.cpp")
    names = {
        f"{suite}.{name}"
        for path in sources
        for suite, name in protocol_tests.re.findall(
            r"^TEST(?:_F)?\((\w+),\s*(\w+)\)", path.read_text(), protocol_tests.re.M
        )
    }
    missing = names - set(protocol_tests.load_catalogue().does)
    assert names and not missing, f"add these to tests/README.md: {sorted(missing)}"


def test_run_explains_each_test(fake_binary, monkeypatch, capsys):
    monkeypatch.setattr(
        protocol_tests,
        "load_catalogue",
        lambda: protocol_tests.parse_catalogue(CATALOGUE),
    )
    protocol_tests.run_tests(fake_binary, "*")
    out = capsys.readouterr().out
    assert "── SeqWindow: the message number rule" in out
    assert "does: 1, 2, 3 are accepted" in out
    assert "why:  The normal case" in out
    assert "(no description in tests/README.md)" in out  # Suite.Bad has none
    assert "What GoogleTest reported:" in out


def test_brief_run_has_no_explanations(fake_binary, capsys):
    protocol_tests.run_tests(fake_binary, "*", explain=False)
    out = capsys.readouterr().out
    assert "does:" not in out and "Summary by area" not in out


def test_parameterized_end_line_drops_the_param_value():
    line = "[  FAILED  ] Golden/Vector.Check/wrong_magic, where GetParam() = wrong_magic (0 ms)"
    assert protocol_tests.parse_line(line) == (
        "failed",
        "Golden/Vector.Check/wrong_magic",
    )


def test_vectors_describe_themselves(tmp_path):
    (tmp_path / "5_sequence.json").write_text(
        '[{"name": "seq_gap", "description": "1, 2, 10 accepted"}]'
    )
    cat = protocol_tests.parse_catalogue(
        "## Golden vectors\n| `5_sequence` | Frames in a row | No replay |\n"
    )
    protocol_tests.add_vectors(cat, tmp_path)
    name = "Golden/Vector.Check/seq_gap"
    assert cat.section_of[name] == "Golden vectors"
    assert cat.does[name] == "1, 2, 10 accepted"
    assert cat.why[name] == "No replay"


def test_golden_vector_files_match_the_generator():
    """drone/tests/vectors/*.json must be what generate.py writes."""
    import subprocess

    generator = protocol_tests.VECTOR_DIR / "generate.py"
    result = subprocess.run(
        [sys.executable, str(generator), "--check"], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


# Passes one test, then dies with SIGSEGV inside the second one.
CRASHING_RUN = r"""#!/bin/sh
case "$1" in
--gtest_list_tests)
  printf 'Suite.\n  Good\n  Boom\n  Never\n'
  ;;
*)
  echo '[ RUN      ] Suite.Good'
  echo '[       OK ] Suite.Good (0 ms)'
  echo '[ RUN      ] Suite.Boom'
  kill -SEGV $$
  ;;
esac
"""


@pytest.fixture
def crashing_binary(tmp_path):
    binary = tmp_path / "sd_protocol_tests"
    binary.write_text(CRASHING_RUN)
    binary.chmod(0o755)
    return binary


def test_a_crashing_binary_is_a_failure(crashing_binary, capsys):
    assert protocol_tests.run_tests(crashing_binary, "*") == 1
    out = capsys.readouterr().out
    assert "FAILED: 1 passed, 1 failed" in out
    assert "signal 11 during Suite.Boom" in out
    assert "✗  1/3" in out  # Never did not run, so the area is not all green


def test_bench_with_a_crashing_binary_fails_cleanly(crashing_binary, capsys):
    assert protocol_tests.bench_tests(crashing_binary, "*", 2, None) == 1
    assert "signal 11" in capsys.readouterr().out


def test_menu_asks_again_for_a_bad_number_of_runs(monkeypatch):
    answers = iter(["abc", "0", "25"])
    monkeypatch.setattr("builtins.input", lambda _="": next(answers))
    assert protocol_tests.ask_runs() == 25


def test_no_build_without_a_binary_says_what_to_do(tmp_path):
    with pytest.raises(SystemExit, match="Run without --no-build"):
        protocol_tests.main(["--no-build", "--build-dir", str(tmp_path), "list"])


# Passes one test, then hangs inside the second one.
HANGING_RUN = r"""#!/bin/sh
case "$1" in
--gtest_list_tests)
  printf 'Suite.\n  Good\n  Hang\n'
  ;;
*)
  echo '[ RUN      ] Suite.Good'
  echo '[       OK ] Suite.Good (0 ms)'
  echo '[ RUN      ] Suite.Hang'
  exec sleep 60
  ;;
esac
"""


def test_a_hanging_binary_is_stopped_and_reported(tmp_path, monkeypatch, capsys):
    binary = tmp_path / "sd_protocol_tests"
    binary.write_text(HANGING_RUN)
    binary.chmod(0o755)
    monkeypatch.setattr(protocol_tests, "TIMEOUT_S", 1)
    assert protocol_tests.run_tests(binary, "*", explain=False) == 1
    out = capsys.readouterr().out
    assert "stopped after 1 s during Suite.Hang: it hung" in out


def test_bench_refuses_a_csv_path_it_cannot_write(fake_binary, tmp_path, capsys):
    bad = tmp_path / "no_such_dir" / "bench.csv"
    assert protocol_tests.bench_tests(fake_binary, "*", 1, str(bad)) == 1
    out = capsys.readouterr().out
    assert "Cannot write" in out and "Benchmark:" not in out


def test_end_of_input_goes_back_instead_of_taking_the_default(monkeypatch):
    def eof(_=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert protocol_tests.ask("Runs per test", "100") is None
    assert protocol_tests.ask_runs() is None


def test_menu_quits_on_end_of_input(fake_binary, monkeypatch):
    answers = iter(["2", "1"])  # Benchmark, All tests, then the input ends

    def feed(_=""):
        try:
            return next(answers)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr("builtins.input", feed)
    ran = []
    monkeypatch.setattr(protocol_tests, "bench_tests", lambda *a: ran.append(a))
    assert protocol_tests.menu(fake_binary.parent, no_build=True) == 0
    assert ran == []  # no benchmark was started with the default 100 runs


# Passes one test, then a child process keeps the output pipe open.
GRANDCHILD_RUN = r"""#!/bin/sh
case "$1" in
--gtest_list_tests)
  printf 'Suite.\n  Good\n  Hang\n'
  ;;
*)
  echo '[ RUN      ] Suite.Good'
  echo '[       OK ] Suite.Good (0 ms)'
  echo '[ RUN      ] Suite.Hang'
  sleep 60 &
  wait
  ;;
esac
"""


def test_the_watchdog_also_stops_children_holding_the_pipe(tmp_path, monkeypatch):
    binary = tmp_path / "sd_protocol_tests"
    binary.write_text(GRANDCHILD_RUN)
    binary.chmod(0o755)
    monkeypatch.setattr(protocol_tests, "TIMEOUT_S", 1)
    start = protocol_tests.time.perf_counter()
    result = protocol_tests.execute(binary, "*", 1, lambda *a: None)
    assert protocol_tests.time.perf_counter() - start < 10
    assert result.timed_out and result.crashed_in == "Suite.Hang"


def test_a_run_that_ends_normally_is_never_called_a_hang(fake_binary, monkeypatch):
    monkeypatch.setattr(protocol_tests, "TIMEOUT_S", 30)
    result = protocol_tests.execute(fake_binary, "*", 1, lambda *a: None)
    assert not result.timed_out


def test_the_csv_check_leaves_no_file_behind(fake_binary, tmp_path, monkeypatch):
    target = tmp_path / "bench.csv"
    monkeypatch.setattr(
        protocol_tests, "execute", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt)
    )
    with pytest.raises(KeyboardInterrupt):
        protocol_tests.bench_tests(fake_binary, "*", 1, str(target))
    assert not target.exists()
