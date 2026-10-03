import sys
from pathlib import Path

import pytest

from installer.render import render_installer

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from verify_script import verify  # noqa: E402

SELECTION = {
    "apps": ["git", "slack", "terraform"],
    "git": {"enabled": True, "ssh_key": True},
    "defaults": ["dock_autohide"],
    "created_at": "2026-10-03T10:00:00+00:00",
}


@pytest.fixture
def script():
    return render_installer(SELECTION, "abc", server="https://initmac.in", commit="a" * 40)


def test_genuine_script_verifies(script):
    problems, commit = verify(script, local_root=ROOT)
    assert problems == [] and commit == "a" * 40


@pytest.mark.parametrize("tamper, expected", [
    (lambda s: s.replace("def confirm_plan(self):", "def confirm_plan(self):\n        return True"), "code differs"),
    (lambda s: s.replace("'brew': 'slack'", "'brew': 'evil-slack'"), "not an exact entry"),
    (lambda s: s.replace("'server': 'https://initmac.in'", "'server': 'http://evil.example'"), "not https"),
    (lambda s: s.replace("CONFIG = {", "CONFIG = __import__('os').system('x') or {", 1), "contains code"),
    (lambda s: s.replace("# Source: ", "# Src: ", 1), "line 2"),
])
def test_tampering_is_detected(script, tamper, expected):
    tampered = tamper(script)
    assert tampered != script, "tamper lambda didn't change anything"
    problems, _ = verify(tampered, local_root=ROOT)
    assert any(expected in p for p in problems), problems
