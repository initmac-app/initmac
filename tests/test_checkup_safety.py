"""Proves the checkup is read-only unless the user says yes, and only does allowed things.

Same approach as test_installer_safety.py: a static scan of the source, plus running the real
script in a sandbox (fake HOME, fake Homebrew, fake system commands) and checking everything it does.
"""

import json
import os
import sys
import types
from pathlib import Path

import pytest

from safety import static_violations

CHECKUP = Path(__file__).resolve().parent.parent / "installer" / "checkup.py"

READ_ONLY = {
    ("system_profiler", "SPHardwareDataType"), ("system_profiler", "SPPowerDataType"),
    ("sysctl", "-n"), ("sw_vers", "-productVersion"), ("fdesetup", "status"), ("spctl", "--status"),
    ("csrutil", "status"), ("tmutil", "destinationinfo"), ("softwareupdate", "--list"),
    ("socketfilterfw", "--getglobalstate"), ("brew", "--cache"), ("brew", "outdated"), ("brew", "leaves"),
}
FIX_COMMANDS = {("brew", "cleanup"), ("brew", "upgrade"), ("open",)}

SAMPLES = {
    "SPHardwareDataType": "      Model Name: MacBook Pro\n      Chip: Apple M5 Pro\n",
    "SPPowerDataType": "          Cycle Count: 412\n          Condition: Service Recommended\n          Maximum Capacity: 76%\n",
    "kern.boottime": "{ sec = 1700000000, usec = 0 } Tue Nov 14 22:13:20 2023",
    "kern.memorystatus_vm_pressure_level": "2",
    "-productVersion": "26.5.1",
    "fdesetup": "FileVault is Off.",
    "spctl": "assessments enabled",
    "csrutil": "System Integrity Protection status: enabled.",
    "tmutil": "tmutil: No destinations configured.",
    "softwareupdate": "* Label: macOS 26.7.1\n\tTitle: macOS Tahoe 26.7.1, Version: 26.7.1, Size: 1KiB, Recommended: YES,\n",
    "socketfilterfw": "Firewall is disabled. (State = 0)",
    "outdated": json.dumps({"formulae": [{"name": "awscli"}, {"name": "aws-c-http"}], "casks": [{"name": "raycast"}]}),
    "leaves": "awscli\nuv\n",
}


def test_checkup_source_has_no_dangerous_calls():
    assert static_violations(CHECKUP.read_text(), allow_programs={"csrutil"}) == []


def test_checkup_runs_on_python39():
    import ast
    ast.parse(CHECKUP.read_text(), feature_version=(3, 9))


class Sandbox:
    def __init__(self, tmp_path, monkeypatch, answers):
        self.home = tmp_path / "home"
        (self.home / "Library" / "Caches" / "Homebrew").mkdir(parents=True)
        (self.home / "Library" / "Caches" / "Homebrew" / "big.tar.gz").write_bytes(b"x" * 3000)
        (self.home / "Library" / "Developer" / "Xcode" / "DerivedData" / "App").mkdir(parents=True)
        (self.home / "Library" / "Developer" / "Xcode" / "DerivedData" / "App" / "build.o").write_bytes(b"x" * 5000)
        (self.home / "Library" / "LaunchAgents").mkdir(parents=True)
        (self.home / "Library" / "LaunchAgents" / "com.example.agent.plist").write_text("<plist/>")
        brew_prefix = tmp_path / "homebrew"
        (brew_prefix / "bin").mkdir(parents=True)
        (brew_prefix / "bin" / "brew").write_text("#!/bin/sh\n")
        monkeypatch.setenv("HOME", str(self.home))
        self.commands = []
        self.answers = list(answers)

        self.mod = types.ModuleType("initmac_checkup")
        exec(compile(CHECKUP.read_text(), "checkup.py", "exec"), self.mod.__dict__)
        m = self.mod
        m.BREW_PREFIXES = [str(brew_prefix)]
        m.LAUNCH_DIRS = [self.home / "Library" / "LaunchAgents"]
        m.BIG_FOLDER_BYTES = 1000
        m.BIG_BREW_CACHE_BYTES = 1000
        m.subprocess = self._fake_subprocess()
        m.shutil = types.SimpleNamespace(disk_usage=lambda p: types.SimpleNamespace(total=500 * 10**9, free=40 * 10**9))
        m.input = self._fake_input
        self.mod.sys = types.SimpleNamespace(stdin=types.SimpleNamespace(isatty=lambda: True), exit=sys.exit,
                                             argv=sys.argv, stdout=sys.stdout)

    def _fake_input(self, prompt=""):
        return self.answers.pop(0) if self.answers else ""

    def _fake_subprocess(self):
        commands, home = self.commands, self.home

        def out_for(cmd):
            if cmd[-1:] == ["--cache"]:
                return str(home / "Library" / "Caches" / "Homebrew")
            for key, value in SAMPLES.items():
                if key in cmd or os.path.basename(cmd[0]) == key or key in cmd[1:2]:
                    return value
            return ""

        def run(cmd, **kw):
            commands.append(list(cmd))
            return types.SimpleNamespace(stdout=out_for(cmd), returncode=0)

        def call(cmd, **kw):
            commands.append(list(cmd))
            return 0

        return types.SimpleNamespace(run=run, call=call, SubprocessError=Exception)

    def main(self, *args):
        argv = sys.argv
        sys.argv = ["checkup.py", *args]
        try:
            self.mod.main()
        except SystemExit:
            pass
        finally:
            sys.argv = argv

    def files(self):
        return sorted(str(p.relative_to(self.home)) for p in self.home.rglob("*") if p.is_file())


@pytest.fixture
def make(tmp_path, monkeypatch):
    return lambda *answers: Sandbox(tmp_path, monkeypatch, answers)


def key(cmd):
    exe = os.path.basename(cmd[0])
    return (exe,) if exe == "open" else (exe, cmd[1] if len(cmd) > 1 else "")


def test_report_only_never_changes_anything(make, capsys):
    sb = make()
    before = sb.files()
    sb.main("--report-only")
    out = capsys.readouterr().out
    assert sb.commands and all(key(c) in READ_ONLY for c in sb.commands), sb.commands
    assert [f for f in sb.files() if f not in before] == [f for f in sb.files() if f.startswith("mac-setup-logs/")]
    assert "Free" in out and "suggested fixes" in out  # it mentions fixes but doesn't ask


def test_answering_no_to_everything_changes_nothing(make):
    sb = make("n", "n", "n", "n", "n", "n", "n", "n")
    sb.main()
    assert all(key(c) in READ_ONLY for c in sb.commands), [c for c in sb.commands if key(c) not in READ_ONLY]


def test_fixes_run_only_allowed_commands_on_reported_targets(make):
    sb = make(*["y"] * 10)
    sb.main()
    fixes = [c for c in sb.commands if key(c) not in READ_ONLY]
    assert fixes, "expected fixes to run when answering yes"
    for c in fixes:
        assert key(c) in FIX_COMMANDS, c
        if key(c) == ("open",):
            target = c[1]
            assert target.startswith("x-apple.systempreferences:") or target == str(
                sb.home / "Library" / "Developer" / "Xcode" / "DerivedData"), c
        if key(c) == ("brew", "upgrade"):
            assert c[2:] == ["awscli", "raycast"]  # only apps you installed; dependency aws-c-http left to brew
        if key(c) == ("brew", "cleanup"):
            assert c[2:] == ["--prune=all"]
    assert any(key(c) == ("csrutil", "status") for c in sb.commands)
    assert not any(os.path.basename(c[0]) == "csrutil" and c[1] != "status" for c in sb.commands)
    assert (sb.home / "Library" / "Developer" / "Xcode" / "DerivedData" / "App" / "build.o").exists()


def test_report_reads_real_world_output_correctly(make, capsys):
    sb = make()
    sb.main("--report-only")
    out = capsys.readouterr().out
    assert "⚠ 76% of original capacity · 412 cycles · condition Service Recommended" in out
    assert "⚠ FileVault off" in out and "⚠ Firewall off" in out and "✓ Gatekeeper on" in out
    assert "macOS updates available: macOS Tahoe 26.7.1" in out
    assert "2 of your Homebrew apps have updates: awscli, raycast" in out
    assert "memory pressure warning" in out and "not restarted for" in out
    assert "1 background items: com.example.agent" in out


def test_json_output_is_machine_readable(make, capsys):
    sb = make()
    sb.main("--json")
    data = json.loads(capsys.readouterr().out)
    assert data["security"]["firewall"] is False and data["battery"]["cycles"] == 412
