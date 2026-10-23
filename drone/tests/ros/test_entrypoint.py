"""The Docker images' entrypoint: drone/ground/docker/entrypoint.sh.

Every ground container starts through it. Without LOG_DIR it only sources ROS
and runs the command (CI and smoke test containers). With LOG_DIR (set by
docker-compose.yml) it also writes the command's output to
$LOG_DIR/$LOG_NAME/<start time>.log, so two runs can be compared. Checks:
  * without LOG_DIR: same output and exit code as the command, no files;
  * with LOG_DIR: stdout and stderr in the file, every line timestamped, a
    last line without a newline kept, the output still on stdout (docker
    logs), the command's exit code, latest.log pointing at the newest run,
    one file per run, files the host user can delete;
  * SIGTERM (docker stop) and SIGINT reach the command, and what it prints
    while stopping is still in the file.

Runs the script directly, no Docker: needs bash and ROS 2 Jazzy at
/opt/ros/jazzy (the script sources it). Without them the tests are skipped,
unless REQUIRE_ROS_TESTS=1 is set (CI sets it), then they fail.
docker/smoke_test.sh checks the same files from real containers.

Run with:  python3 -m pytest drone/tests/ros -v        (from the repo root)
"""

import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ENTRYPOINT = Path(__file__).resolve().parents[2] / "ground" / "docker" / "entrypoint.sh"
ROS_SETUP = Path("/opt/ros/jazzy/setup.bash")
STAMP = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{6} ")
# A pretend node: says it is ready, then on SIGTERM says it stops, exits 0
NODE = 'trap "echo stopping; exit 0" TERM; echo ready; while :; do sleep 0.05; done'


def runnable():
    found = ROS_SETUP.is_file() and shutil.which("bash") is not None
    if not found and os.environ.get("REQUIRE_ROS_TESTS"):
        raise RuntimeError(f"entrypoint.sh needs bash and {ROS_SETUP}")
    return found


@unittest.skipUnless(runnable(), f"bash or {ROS_SETUP} not available")
class EntrypointTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="entrypoint-test-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.logs = self.dir / "logs"

    def env(self, log=True):
        env = {k: v for k, v in os.environ.items() if k not in ("LOG_DIR", "LOG_NAME")}
        if log:
            env.update(LOG_DIR=str(self.logs), LOG_NAME="node")
        return env

    def run_script(self, script, log=True):
        return subprocess.run(
            ["bash", str(ENTRYPOINT), "bash", "-c", script],
            capture_output=True,
            text=True,
            env=self.env(log),
            timeout=30,
        )

    def latest(self):
        return self.logs / "node" / "latest.log"

    def test_without_log_dir_it_only_runs_the_command(self):
        result = self.run_script("echo out; echo err >&2; exit 3", log=False)
        self.assertEqual(result.returncode, 3)
        self.assertEqual((result.stdout, result.stderr), ("out\n", "err\n"))
        self.assertFalse(self.logs.exists())

    def test_ros_is_sourced(self):
        result = self.run_script('echo "$ROS_DISTRO"', log=False)
        self.assertEqual(result.stdout, "jazzy\n")

    def test_output_goes_to_a_timestamped_file_and_stdout(self):
        result = self.run_script("echo out; echo err >&2; printf 'no newline'; exit 3")
        self.assertEqual(result.returncode, 3)
        lines = self.latest().read_text().splitlines()
        self.assertEqual(len(lines), 3, lines)
        for line in lines:
            self.assertRegex(line, STAMP)
        self.assertEqual(
            sorted(STAMP.sub("", line) for line in lines),
            ["err", "no newline", "out"],
        )
        # Still on stdout, so `docker logs` / `docker compose logs` keep working
        self.assertEqual(result.stdout.splitlines(), lines)

    def test_time_is_the_wall_clock(self):
        self.run_script("echo hi")
        stamp = self.latest().read_text()[:19]
        logged = time.mktime(time.strptime(stamp, "%Y-%m-%d %H:%M:%S"))
        self.assertLess(abs(time.time() - logged), 10.0)

    def test_one_file_per_run_and_latest_is_the_newest(self):
        self.run_script("echo first")
        time.sleep(1.1)  # file names have a resolution of 1 s
        self.run_script("echo second")
        runs = sorted(p.name for p in (self.logs / "node").glob("20*.log"))
        self.assertEqual(len(runs), 2, runs)
        self.assertRegex(runs[0], r"^\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d\.log$")
        # Relative link: it also works on the host, outside the container
        self.assertEqual(os.readlink(self.latest()), runs[1])
        self.assertIn("second", self.latest().read_text())
        self.assertIn("first", (self.logs / "node" / runs[0]).read_text())

    def test_files_can_be_deleted_by_anyone(self):
        """In Docker the files belong to root; the host user must be able to
        remove them without sudo."""
        self.run_script("echo hi")
        folder = self.logs / "node"
        log = folder / os.readlink(self.latest())
        self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o777)
        self.assertEqual(stat.S_IMODE(log.stat().st_mode), 0o666)

    def test_log_dir_made_by_docker_is_opened_up(self):
        """A missing bind-mount folder is created by Docker as root, mode 755:
        the host user could not remove the per-node folders inside it."""
        self.logs.mkdir(mode=0o755)
        self.run_script("echo hi")
        self.assertEqual(stat.S_IMODE(self.logs.stat().st_mode), 0o777)

    def test_log_name_defaults_to_the_host_name(self):
        env = self.env()
        del env["LOG_NAME"]
        subprocess.run(
            ["bash", str(ENTRYPOINT), "true"], env=env, timeout=30, check=True
        )
        self.assertTrue((self.logs / os.uname().nodename / "latest.log").is_symlink())

    def stop_with(self, sig):
        proc = subprocess.Popen(
            ["bash", str(ENTRYPOINT), "bash", "-c", NODE],
            stdout=subprocess.DEVNULL,
            env=self.env(),
        )
        end = time.monotonic() + 20.0
        while time.monotonic() < end:
            if self.latest().exists() and "ready" in self.latest().read_text():
                break
            time.sleep(0.05)
        start = time.monotonic()
        proc.send_signal(sig)
        try:
            code = proc.wait(10.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            self.fail(f"still running 10 s after {sig.name}")
        self.assertLess(time.monotonic() - start, 5.0)
        return code

    def test_sigterm_reaches_the_command_and_its_last_lines_are_kept(self):
        self.assertEqual(self.stop_with(signal.SIGTERM), 0)
        self.assertTrue(self.latest().read_text().endswith(" stopping\n"))

    def test_sigint_stops_the_command_too(self):
        self.assertEqual(self.stop_with(signal.SIGINT), 0)
        self.assertTrue(self.latest().read_text().endswith(" stopping\n"))


if __name__ == "__main__":
    unittest.main()
