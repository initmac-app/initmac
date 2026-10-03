"""Checks every catalog entry against Homebrew (catches renamed/removed casks).

Run after Homebrew is installed:  python3 tools/check_brew_tokens.py
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from catalog.validate import load_all  # noqa: E402

tapped = set(subprocess.run(["brew", "tap"], capture_output=True, text=True).stdout.split())
bad = []
for app in load_all()[0]:
    if app["brew"].count("/") == 2 and app["brew"].rsplit("/", 1)[0] not in tapped:
        # The installer taps it first; checking would require tapping here.
        print(f"{'needs tap':8} {app['type']:8} {app['brew']}")
        continue
    proc = subprocess.run(
        ["brew", "info", "--json=v2", f"--{app['type']}", app["brew"]], capture_output=True, text=True
    )
    status = "ok" if proc.returncode == 0 else "MISSING"
    print(f"{status:8} {app['type']:8} {app['brew']}")
    if proc.returncode != 0:
        bad.append(app["id"])
print(f"\n{len(bad)} problem(s): {', '.join(bad)}" if bad else "\nAll tokens found.")
sys.exit(1 if bad else 0)
