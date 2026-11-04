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
            r"^TEST\((\w+),\s*(\w+)\)", path.read_text(), protocol_tests.re.M
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
