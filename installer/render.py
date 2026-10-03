"""Renders a personalized installer (template + CONFIG) and its bash launcher.

This is the exact code initmac.in uses to generate the scripts it serves, so anyone can
reproduce and audit them. See tools/verify_script.py.
"""

import os
import pprint
import re
import shlex
import subprocess
from pathlib import Path

from catalog.validate import apps_by_id, tweaks_by_id

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "install_template.py"
LAUNCHER = HERE / "launcher.sh.tmpl"
PLACEHOLDER = "CONFIG = __CONFIG__"
REPO_URL = "https://github.com/initmac-app/initmac"
SOURCE_PREFIX = "# Source: "


def source_commit() -> str:
    """The public-repo commit this installer comes from (baked into the cloud image as BUILD_COMMIT)."""
    if os.environ.get("INITMAC_COMMIT"):
        return os.environ["INITMAC_COMMIT"]
    build_file = HERE.parent / "BUILD_COMMIT"
    if build_file.exists():
        return build_file.read_text().strip()
    try:
        out = subprocess.run(["git", "-C", str(HERE.parent), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def build_config(selection: dict, selection_id: str = "", server: str = "") -> dict:
    # Deterministic: the same selection always yields the same config, so the script's
    # SHA-256 can be pinned by the launcher and shown to users.
    apps = apps_by_id()
    tweaks = tweaks_by_id()
    git = selection.get("git") or {}
    return {
        "selection_id": selection_id,
        "created_at": selection.get("created_at", ""),
        "server": server,
        "apps": [
            {k: apps[i][k] for k in ("id", "name", "brew", "type")}
            for i in selection.get("apps", [])
            if i in apps
        ],
        "git": {
            "enabled": bool(git.get("enabled")),
            "name": git.get("name", ""),
            "email": git.get("email", ""),
            "ssh_key": bool(git.get("ssh_key", True)),
        },
        "defaults": [tweaks[i] for i in selection.get("defaults", []) if i in tweaks],
    }


def render_installer(selection: dict, selection_id: str = "", server: str = "", commit: str | None = None) -> str:
    # pformat emits a Python literal; every user-provided string goes through repr(),
    # so names/emails can't break out of the data structure.
    config = pprint.pformat(build_config(selection, selection_id, server), sort_dicts=False, width=100)
    template = TEMPLATE.read_text(encoding="utf-8")
    assert PLACEHOLDER in template
    script = template.replace(PLACEHOLDER, "CONFIG = " + config, 1)
    # Line 2: where to find the exact source of everything but the CONFIG block.
    shebang, rest = script.split("\n", 1)
    return f"{shebang}\n{SOURCE_PREFIX}{REPO_URL} @ {commit or source_commit()}\n{rest}"


def render_launcher(script_url: str, sha256: str) -> str:
    """Bash wrapper: installs the Command Line Tools if needed (python3 needs them), then
    downloads the installer, checks it against the pinned SHA-256 and only then runs it."""
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError("sha256 must be 64 lowercase hex characters")
    return LAUNCHER.read_text().replace("{{SHA256}}", sha256).replace("{{URL}}", shlex.quote(script_url))
