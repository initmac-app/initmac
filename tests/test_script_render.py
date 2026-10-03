import ast
import platform
import subprocess
import sys

import pytest

from installer.render import build_config, render_installer, render_launcher

SELECTION = {
    "apps": ["git", "google-chrome", "terraform", "not-a-real-app"],
    "git": {"enabled": True, "name": "Ada Lovelace", "email": "ada@example.com", "ssh_key": True},
    "defaults": ["dock_autohide", "unknown_tweak"],
}


def test_config_drops_unknown_ids():
    config = build_config(SELECTION, "abc")
    assert [a["id"] for a in config["apps"]] == ["git", "google-chrome", "terraform"]
    assert [t["id"] for t in config["defaults"]] == ["dock_autohide"]


def test_rendered_script_compiles_and_contains_tokens():
    script = render_installer(SELECTION, "abc")
    compile(script, "install.py", "exec")
    for token in ("'git'", "'google-chrome'", "'hashicorp/tap/terraform'", "'Ada Lovelace'"):
        assert token in script
    assert "__CONFIG__" not in script


def test_rendered_script_is_python39_compatible():
    script = render_installer(SELECTION, "abc")
    ast.parse(script, feature_version=(3, 9))


def test_user_strings_cannot_inject_code():
    evil = {"apps": [], "git": {"enabled": True, "name": "x'''\nimport os; os.system('boom')\n'''", "email": ""}}
    script = render_installer(evil)
    tree = ast.parse(script)
    config_assign = next(
        n for n in tree.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "CONFIG"
    )
    assert ast.literal_eval(config_assign.value)["git"]["name"] == evil["git"]["name"]


def test_launcher_pins_sha256():
    digest = "a" * 64
    sh = render_launcher("https://example.com/i/abc", digest)
    assert "curl -fsSL https://example.com/i/abc" in sh
    assert f'expected="{digest}"' in sh
    # The hash check comes before the script is run.
    assert sh.index("shasum -a 256") < sh.index("/usr/bin/python3")
    with pytest.raises(ValueError):
        render_launcher("https://example.com/i/abc", "not-a-hash")


def test_rendering_is_deterministic():
    sel = {**SELECTION, "created_at": "2026-10-02T10:00:00+00:00"}
    a = render_installer(sel, "abc", server="https://initmac.in")
    assert a == render_installer(sel, "abc", server="https://initmac.in")
    assert "'server': 'https://initmac.in'" in a


@pytest.mark.skipif(platform.system() != "Darwin", reason="installer only runs on macOS")
def test_dry_run_changes_nothing(tmp_path):
    path = tmp_path / "install.py"
    path.write_text(render_installer({**SELECTION, "git": {"enabled": False}}))
    out = subprocess.run(
        [sys.executable, str(path), "--dry-run", "--yes"], capture_output=True, text=True, timeout=120
    )
    assert "DRY RUN" in out.stdout
    assert "@@RESULT" in out.stdout
    assert "[dry-run] $ defaults write com.apple.dock autohide -bool true" in out.stdout
    if "already installed: Terraform" not in out.stdout:
        assert "tap hashicorp/tap" in out.stdout
