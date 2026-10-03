#!/usr/bin/env python3
"""Verify an InitMac installer you downloaded against this public repository.

    curl -fsSL https://initmac.in/i/<id> -o initmac-install.py
    python3 verify_script.py initmac-install.py

It checks that:
  1. the script names the public commit it was built from (line 2: "# Source: ... @ <commit>"),
  2. everything except the CONFIG block is byte-for-byte the public template at that commit,
  3. CONFIG is plain data (no code), and every app / macOS setting in it is an exact entry
     from the public catalog at that commit, and the sync server (if any) is https.

Only uses the Python standard library. Exit code 0 = verified, 1 = mismatch.
"""

import argparse
import ast
import difflib
import json
import re
import sys
import urllib.request
from pathlib import Path

RAW = "https://raw.githubusercontent.com/initmac-app/initmac/{commit}/{path}"
SOURCE_LINE = re.compile(r"^# Source: https://github\.com/initmac-app/initmac @ ([0-9a-f]{7,40})$")
PLACEHOLDER = "CONFIG = __CONFIG__"


def fetch(commit, path, local_root=None):
    if local_root:
        return (Path(local_root) / path).read_text(encoding="utf-8")
    with urllib.request.urlopen(RAW.format(commit=commit, path=path), timeout=20) as resp:
        return resp.read().decode("utf-8")


def verify(script_text, local_root=None):
    """Returns a list of problems (empty = verified) and the commit."""
    lines = script_text.split("\n")
    if len(lines) < 3:
        return ["file is too short to be an InitMac installer"], None
    m = SOURCE_LINE.match(lines[1])
    if not m:
        return ["line 2 is not an InitMac '# Source: ... @ <commit>' line"], None
    commit = m.group(1)

    try:
        tree = ast.parse(script_text)
    except SyntaxError as e:
        return [f"not valid Python: {e}"], commit
    assigns = [n for n in tree.body if isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == "CONFIG" for t in n.targets)]
    if len(assigns) != 1:
        return [f"expected exactly one CONFIG assignment, found {len(assigns)}"], commit
    node = assigns[0]
    try:
        config = ast.literal_eval(node.value)  # fails on anything that isn't plain data
    except ValueError:
        return ["CONFIG contains code, not just data"], commit

    # Rebuild the template: drop the Source line, put the placeholder back in place of CONFIG.
    rebuilt = lines[: node.lineno - 1] + [PLACEHOLDER] + lines[node.end_lineno:]
    del rebuilt[1]
    template = fetch(commit, "installer/install_template.py", local_root)
    problems = []
    if "\n".join(rebuilt) != template:
        diff = difflib.unified_diff(template.splitlines(), "\n".join(rebuilt).splitlines(),
                                    "public template", "your script", lineterm="", n=1)
        problems.append("code differs from the public template:\n" + "\n".join(list(diff)[:60]))

    catalog = {a["id"]: a for a in json.loads(fetch(commit, "catalog/catalog.json", local_root))}
    tweaks = {t["id"]: t for t in json.loads(fetch(commit, "catalog/macos_defaults.json", local_root))}
    for app in config.get("apps", []):
        ref = catalog.get(app.get("id"))
        expected = {k: ref[k] for k in ("id", "name", "brew", "type")} if ref else None
        if app != expected:
            problems.append(f"app {app!r} is not an exact entry of the public catalog")
    for tweak in config.get("defaults", []):
        if tweak != tweaks.get(tweak.get("id")):
            problems.append(f"macOS setting {tweak.get('id')!r} doesn't match the public catalog")
    server = config.get("server", "")
    if server and not server.startswith(("https://", "http://127.0.0.1", "http://localhost")):
        problems.append(f"sync server {server!r} is not https")
    return problems, commit


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("script", help="the installer you downloaded")
    parser.add_argument("--source-dir", help="compare against a local checkout instead of GitHub")
    args = parser.parse_args()
    problems, commit = verify(Path(args.script).read_text(encoding="utf-8"), args.source_dir)
    if problems:
        print("✗ NOT verified" + (f" (commit {commit})" if commit else ""))
        for p in problems:
            print("  - " + p)
        sys.exit(1)
    print(f"✓ Verified: matches github.com/initmac-app/initmac @ {commit}")
    print("  The code is the public template; CONFIG only contains public catalog entries.")


if __name__ == "__main__":
    main()
