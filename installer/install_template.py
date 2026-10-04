#!/usr/bin/env python3
"""InitMac installer (generated).

Installs the apps you picked with Homebrew, and optionally configures Git + an SSH
key and a few macOS defaults. It shows you the full plan and asks before changing
anything. Safe to re-run: anything already done is skipped.

What it will never do: delete your files, run sudo itself (Homebrew may ask for your
password for some apps), or send data anywhere unless you opt in to syncing your app list.

Usage:
    python3 install.py                     # show the plan, ask, then run
    python3 install.py --dry-run           # print what would happen, change nothing
    python3 install.py --yes               # don't ask questions, use the values from the website
    python3 install.py --restore-defaults  # undo the macOS settings changes
    python3 install.py --sync              # save this Mac's app list to InitMac (opt-in)
    python3 install.py --forget-device     # delete that saved list from InitMac and this Mac

Only uses the Python standard library so it runs on a brand-new Mac.
"""

import argparse
import datetime
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

# ---- Your selection (generated) ---------------------------------------------
CONFIG = __CONFIG__
# -----------------------------------------------------------------------------

HOME = Path.home()
LOG_DIR = HOME / "mac-setup-logs"
BACKUP_FILE = HOME / "mac-setup-defaults-backup.json"
DEVICE_FILE = HOME / ".initmac" / "device.json"
BREW_PREFIXES = ["/opt/homebrew", "/usr/local"]
HOMEBREW_INSTALL = "https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh"
RESTARTABLE = ("Dock", "Finder", "SystemUIServer")
REPO_URL = "https://github.com/initmac-app/initmac"
APPLICATION_DIRS = [Path("/Applications"), HOME / "Applications"]


class Installer:
    def __init__(self, dry_run, assume_yes):
        self.dry_run = dry_run
        self.interactive = sys.stdin.isatty() and not assume_yes
        LOG_DIR.mkdir(exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.log_path = LOG_DIR / ("install-%s.log" % stamp)
        self.log_file = open(self.log_path, "a", encoding="utf-8")
        self.result = {"installed": [], "skipped": [], "failed": [], "steps": {}}
        self._prepend_brew_to_path()

    # ---- helpers ---------------------------------------------------------
    def log(self, msg=""):
        print(msg, flush=True)
        self.log_file.write(msg + "\n")
        self.log_file.flush()

    def section(self, title):
        self.log("")
        self.log("==> " + title)

    def run(self, cmd, mutating=True, interactive=False, env=None):
        """Run a command (argument list, never a shell string), streaming output. Returns exit code."""
        shown = " ".join(cmd)
        if mutating and self.dry_run:
            self.log("[dry-run] $ " + shown)
            return 0
        self.log("$ " + shown)
        full_env = dict(os.environ, **(env or {}))
        if interactive:
            # Inherit the terminal so the command can prompt (passwords, logins).
            return subprocess.call(cmd, env=full_env)
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=full_env
        )
        for line in proc.stdout:
            self.log("    " + line.rstrip())
        return proc.wait()

    def output(self, cmd):
        """Run a read-only command and return its stdout ('' on failure)."""
        try:
            return subprocess.run(cmd, capture_output=True, text=True, check=False).stdout
        except OSError:
            return ""

    def _input(self, prompt):
        try:
            return input(prompt).strip()
        except EOFError:  # Ctrl-D / closed input: treat as no answer
            print()
            return ""

    def ask(self, question, default=""):
        if not self.interactive:
            return default
        suffix = " [%s]" % default if default else ""
        return self._input("%s%s: " % (question, suffix)) or default

    def confirm(self, question, default=False):
        if not self.interactive:
            return default
        hint = "Y/n" if default else "y/N"
        answer = self._input("%s [%s]: " % (question, hint)).lower()
        return default if not answer else answer.startswith("y")

    def brew(self):
        for prefix in BREW_PREFIXES:
            path = Path(prefix) / "bin" / "brew"
            if path.exists():
                return str(path)
        return None

    def _prepend_brew_to_path(self):
        for prefix in BREW_PREFIXES:
            for sub in ("bin", "sbin"):
                p = "%s/%s" % (prefix, sub)
                if Path(p).is_dir() and p not in os.environ.get("PATH", "").split(":"):
                    os.environ["PATH"] = p + ":" + os.environ.get("PATH", "")

    def _setup_askpass(self):
        """Without a terminal, sudo can't prompt; show a macOS password dialog instead."""
        if self.interactive or os.environ.get("SUDO_ASKPASS"):
            return
        script = Path(tempfile.mkdtemp()) / "askpass.sh"
        script.write_text(
            "#!/bin/sh\n"
            "osascript -e 'text returned of (display dialog \"InitMac needs your "
            "Mac password to install some apps.\" default answer \"\" with hidden answer "
            "with title \"InitMac\" with icon caution)'\n"
        )
        script.chmod(0o700)
        os.environ["SUDO_ASKPASS"] = str(script)

    # ---- plan & consent ----------------------------------------------------
    def plan_lines(self):
        apps = CONFIG["apps"]
        git = CONFIG.get("git") or {}
        tweaks = CONFIG.get("defaults") or []
        will = ["Install Xcode Command Line Tools and Homebrew if they're missing",
                "(and add one line to ~/.zprofile so Homebrew is on your PATH)"]
        if apps:
            todo, present = self.partition_apps()
            if todo:
                will.append("Install %s with Homebrew: %s" % (_count(len(todo), "app"), ", ".join(a["name"] for a in todo)))
            if present:
                will.append("Skip %s you already have: %s" % (_count(len(present), "app"), ", ".join(a["name"] for a, _ in present)))
        if git.get("enabled"):
            will.append("Set your Git name/email, default branch 'main' and pull behaviour")
            if git.get("ssh_key", True):
                will.append("Create ~/.ssh/id_ed25519 only if you don't have one, and add a github.com entry to ~/.ssh/config")
        if tweaks:
            restart = sorted({r for t in tweaks for r in t.get("restart", [])})
            will.append("Change %s (your originals are backed up first): %s"
                        % (_count(len(tweaks), "macOS setting"), ", ".join(t["label"] for t in tweaks)))
            if restart:
                will.append("Restart %s so the settings take effect" % " and ".join(restart))
        wont = [
            "Delete or overwrite any of your files",
            "Run sudo itself (Homebrew may ask for your password for some apps)",
            "Send any data anywhere, unless you choose to sync your app list at the end",
        ]
        return will, wont

    def confirm_plan(self):
        will, wont = self.plan_lines()
        self.section("Here's what InitMac will do")
        for line in will:
            self.log("  + " + line)
        self.log("")
        self.log("  It will NOT:")
        for line in wont:
            self.log("  - " + line)
        self.log("")
        if self.dry_run or not self.interactive:
            return True
        if not self.confirm("Continue?"):
            self.log("OK, stopped. Nothing on your Mac was changed.")
            return False
        return True

    # ---- steps -----------------------------------------------------------
    def check_platform(self):
        if platform.system() != "Darwin":
            self.log("This installer only runs on macOS.")
            sys.exit(1)
        self.log("macOS %s on %s" % (platform.mac_ver()[0], platform.machine()))

    def command_line_tools(self):
        self.section("Xcode Command Line Tools")
        if subprocess.call(["xcode-select", "-p"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
            self.log("Already installed.")
            return
        if self.dry_run:
            self.log("[dry-run] would run: xcode-select --install")
            return
        self.run(["xcode-select", "--install"])
        self.log("A dialog opened. Click Install and wait; this can take several minutes...")
        deadline = time.time() + 45 * 60
        while time.time() < deadline:
            if subprocess.call(["xcode-select", "-p"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
                self.log("Command Line Tools installed.")
                return
            time.sleep(10)
        self.log("Timed out waiting for the Command Line Tools. Install them and re-run.")
        sys.exit(1)

    def homebrew(self):
        self.section("Homebrew")
        if self.brew():
            self.log("Already installed at " + self.brew())
        elif self.dry_run:
            self.log("[dry-run] would install Homebrew from " + HOMEBREW_INSTALL)
            return
        else:
            self._setup_askpass()
            env = {} if self.interactive else {"NONINTERACTIVE": "1"}
            cmd = ["/bin/bash", "-c", '/bin/bash -c "$(curl -fsSL %s)"' % HOMEBREW_INSTALL]
            code = self.run(cmd, interactive=self.interactive, env=env)
            self._prepend_brew_to_path()
            if code != 0 or not self.brew():
                self.log("Homebrew install failed; see the output above.")
                sys.exit(1)

        brew = self.brew()
        zprofile = HOME / ".zprofile"
        line = 'eval "$(%s shellenv)"' % brew
        existing = zprofile.read_text() if zprofile.exists() else ""
        if line not in existing:
            if self.dry_run:
                self.log("[dry-run] would add Homebrew to ~/.zprofile")
            else:
                with open(zprofile, "a") as f:
                    f.write("\n# Homebrew\n%s\n" % line)
                self.log("Added Homebrew to ~/.zprofile (open a new terminal to pick it up).")

    def installed_tokens(self):
        brew = self.brew()
        if not brew:
            return set(), set()
        formulae = set(self.output([brew, "list", "--formula", "-1"]).split())
        casks = set(self.output([brew, "list", "--cask", "-1"]).split())
        return formulae, casks

    def cask_app_bundles(self, tokens):
        """Map cask token -> the .app bundle names it installs (read-only `brew info`)."""
        brew = self.brew()
        if not brew or not tokens:
            return {}
        raw = self.output([brew, "info", "--json=v2", "--cask"] + list(tokens))
        try:
            casks = json.loads(raw).get("casks", []) if raw else []
        except ValueError:
            return {}
        bundles = {}
        for cask in casks:
            names = []
            for artifact in cask.get("artifacts", []):
                if not isinstance(artifact, dict) or "app" not in artifact:
                    continue
                for entry in artifact["app"]:
                    if isinstance(entry, str):
                        names.append(entry)
                    elif isinstance(entry, dict) and entry.get("target"):
                        names.append(os.path.basename(entry["target"]))
                if artifact.get("target"):
                    names.append(os.path.basename(artifact["target"]))
            bundles[cask.get("token", "")] = sorted(set(n for n in names if n.endswith(".app")))
        return bundles

    def partition_apps(self):
        """Split the selection into (to install, [(app, reason) already present]).

        An app counts as present if Homebrew installed it, or (for casks) if its .app bundle
        already exists in /Applications or ~/Applications, e.g. downloaded from the vendor's site.
        Those are left exactly as they are.
        """
        formulae, casks = self.installed_tokens()
        todo, present = [], []
        for app in CONFIG["apps"]:
            name = app["brew"].split("/")[-1]
            if name in (formulae if app["type"] == "formula" else casks):
                present.append((app, "via Homebrew"))
            else:
                todo.append(app)
        cask_todo = [a["brew"] for a in todo if a["type"] == "cask"]
        bundles = self.cask_app_bundles(cask_todo)
        still = []
        for app in todo:
            found = [b for b in bundles.get(app["brew"], [])
                     if any((d / b).exists() for d in APPLICATION_DIRS)]
            if found:
                present.append((app, "installed outside Homebrew (%s)" % found[0]))
            else:
                still.append(app)
        return still, present

    def install_apps(self):
        apps = CONFIG["apps"]
        self.section("Apps (%d selected)" % len(apps))
        if not apps:
            self.log("Nothing selected.")
            return
        brew = self.brew()
        if not brew and not self.dry_run:
            self.log("Homebrew is missing; cannot install apps.")
            self.result["failed"] = [a["id"] for a in apps]
            return
        brew = brew or "brew"

        def is_installed(app, formulae, casks):
            name = app["brew"].split("/")[-1]
            return name in (formulae if app["type"] == "formula" else casks)

        todo, present = self.partition_apps()
        for app, reason in present:
            self.log("  already installed: %s (%s)" % (app["name"], reason))
            self.result["skipped"].append(app["id"])
        if not todo:
            return

        self._setup_askpass()
        env = {"HOMEBREW_NO_AUTO_UPDATE": "1", "HOMEBREW_NO_ENV_HINTS": "1"}
        self.run([brew, "update"])

        # Third-party taps (e.g. hashicorp/tap/terraform) must be tapped explicitly first.
        tapped = set(self.output([brew, "tap"]).split())
        for tap in sorted({a["brew"].rsplit("/", 1)[0] for a in todo if a["brew"].count("/") == 2}):
            if tap not in tapped:
                self.run([brew, "tap", tap], env=env)

        for kind in ("formula", "cask"):
            batch = [a for a in todo if a["type"] == kind]
            if batch:
                self.log("")
                self.log("Installing %d %s(s): %s" % (len(batch), kind, ", ".join(a["name"] for a in batch)))
                self.run([brew, "install", "--" + kind] + [a["brew"] for a in batch], env=env)

        if self.dry_run:
            self.result["installed"] = [a["id"] for a in todo]
            return

        # Anything the batch didn't install (one bad token aborts a whole batch) gets retried alone.
        formulae, casks = self.installed_tokens()
        for app in todo:
            if not is_installed(app, formulae, casks):
                self.log("")
                self.log("Retrying %s on its own..." % app["name"])
                self.run([brew, "install", "--" + app["type"], app["brew"]], env=env)
        formulae, casks = self.installed_tokens()
        for app in todo:
            key = "installed" if is_installed(app, formulae, casks) else "failed"
            self.result[key].append(app["id"])

    def git_and_ssh(self):
        git = CONFIG.get("git")
        if not git or not git.get("enabled"):
            return
        self.section("Git")
        git_bin = shutil.which("git") or "git"
        current_name = self.output([git_bin, "config", "--global", "user.name"]).strip()
        current_email = self.output([git_bin, "config", "--global", "user.email"]).strip()
        name = self.ask("Your name for Git commits", git.get("name") or current_name)
        email = self.ask("Your email for Git commits", git.get("email") or current_email)
        settings = [("init.defaultBranch", "main"), ("pull.rebase", "false")]
        if name:
            settings.insert(0, ("user.name", name))
        if email:
            settings.insert(1, ("user.email", email))
        else:
            self.log("No email given; skipping user.email.")
        for key, value in settings:
            self.run([git_bin, "config", "--global", key, value])
        self.result["steps"]["git"] = "ok"

        if not git.get("ssh_key", True):
            return
        self.section("SSH key for GitHub")
        ssh_dir = HOME / ".ssh"
        key = ssh_dir / "id_ed25519"
        if key.exists():
            self.log("Using existing key " + str(key))
        else:
            if not self.dry_run:
                ssh_dir.mkdir(mode=0o700, exist_ok=True)
            cmd = ["ssh-keygen", "-t", "ed25519", "-C", email or name or "mac", "-f", str(key)]
            if not self.interactive:
                cmd += ["-N", ""]
                self.log("No terminal attached, so the key is created without a passphrase.")
            if self.run(cmd, interactive=self.interactive) != 0:
                self.log("ssh-keygen failed.")
                self.result["steps"]["ssh"] = "failed"
                return

        config = ssh_dir / "config"
        block = "Host github.com\n  AddKeysToAgent yes\n  UseKeychain yes\n  IdentityFile ~/.ssh/id_ed25519\n"
        existing = config.read_text() if config.exists() else ""
        if "Host github.com" not in existing:
            if self.dry_run:
                self.log("[dry-run] would add a github.com entry to ~/.ssh/config")
            else:
                with open(config, "a") as f:
                    f.write(("\n" if existing else "") + block)
                config.chmod(0o600)
                self.log("Added github.com entry to ~/.ssh/config")
        self.run(["ssh-add", "--apple-use-keychain", str(key)], interactive=self.interactive)

        pub = key.with_suffix(".pub")
        if pub.exists():
            pubkey = pub.read_text().strip()
            if not self.dry_run:
                subprocess.run(["pbcopy"], input=pubkey, text=True, check=False)
            self.log("Public key (copied to clipboard):")
            self.log("  " + pubkey)
            self.log("Add it at https://github.com/settings/ssh/new")
            self.result["steps"]["ssh_pubkey"] = pubkey
        self.result["steps"]["ssh"] = "ok"

        if shutil.which("gh") and self.confirm("Log in to GitHub with the gh CLI now (it can upload the key)?"):
            self.run(["gh", "auth", "login", "--hostname", "github.com", "--git-protocol", "ssh"], interactive=True)

    def macos_defaults(self):
        tweaks = CONFIG.get("defaults") or []
        if not tweaks:
            return
        self.section("macOS defaults (%d tweaks)" % len(tweaks))
        backup = json.loads(BACKUP_FILE.read_text()) if BACKUP_FILE.exists() else {}
        restart = []
        for tweak in tweaks:
            self.log("- " + tweak["label"])
            if tweak.get("mkdir") and not self.dry_run:
                Path(os.path.expanduser(tweak["mkdir"])).mkdir(parents=True, exist_ok=True)
            for domain, key, typ, value in tweak["writes"]:
                slot = "%s %s" % (domain, key)
                if slot not in backup:  # keep the very first (original) value
                    proc = subprocess.run(["defaults", "read", domain, key], capture_output=True, text=True)
                    backup[slot] = {
                        "domain": domain, "key": key, "type": typ,
                        "existed": proc.returncode == 0, "value": proc.stdout.strip(),
                    }
                self.run(["defaults", "write", domain, key, "-" + typ, _defaults_value(typ, value)])
            restart += [r for r in tweak.get("restart", []) if r in RESTARTABLE and r not in restart]
        if not self.dry_run:
            BACKUP_FILE.write_text(json.dumps(backup, indent=2))
            self.log("Original values saved to %s (undo with --restore-defaults)" % BACKUP_FILE)
        for proc_name in restart:
            self.run(["killall", proc_name])
        self.result["steps"]["defaults"] = "ok"

    def restore_defaults(self):
        self.section("Restoring macOS defaults")
        if not BACKUP_FILE.exists():
            self.log("No backup found at %s" % BACKUP_FILE)
            return
        backup = json.loads(BACKUP_FILE.read_text())
        for item in backup.values():
            if item["existed"]:
                self.run(["defaults", "write", item["domain"], item["key"], "-" + item["type"],
                          _restore_value(item["type"], item["value"])])
            else:
                self.run(["defaults", "delete", item["domain"], item["key"]])
        for proc_name in RESTARTABLE:
            self.run(["killall", proc_name])
        if not self.dry_run:
            BACKUP_FILE.unlink()  # our own backup file; its values have just been restored

    # ---- opt-in device sync ------------------------------------------------
    def _api(self, method, path, body=None, secret=None):
        server = CONFIG.get("server") or ""
        if not server.startswith(("https://", "http://127.0.0.1", "http://localhost")):
            raise RuntimeError("No InitMac server configured for syncing")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(server + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", "initmac-installer")
        if secret:
            req.add_header("X-Device-Secret", secret)
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode() or "{}")

    def _load_device(self):
        try:
            return json.loads(DEVICE_FILE.read_text())
        except (OSError, ValueError):
            return None

    def sync_device(self):
        self.section("Sync this Mac's app list")
        brew = self.brew()
        if not brew:
            self.log("Homebrew isn't installed, so there's nothing to sync.")
            return
        # Only what you installed yourself, not the dependencies Homebrew pulled in.
        formulae = sorted(self.output([brew, "leaves", "--installed-on-request"]).split()
                          or self.output([brew, "list", "--formula", "-1"]).split())
        casks = sorted(self.output([brew, "list", "--cask", "-1"]).split())
        device = self._load_device()
        name = self.ask("Name to show for this Mac", (device or {}).get("name") or "My Mac")[:60]
        self.log("Sending only: the name %r and %d Homebrew package names (%d formulae, %d casks)."
                 % (name, len(formulae) + len(casks), len(formulae), len(casks)))
        self.log("No hardware IDs, usernames, file paths or anything else.")
        if self.dry_run:
            self.log("[dry-run] would send that to %s" % CONFIG.get("server"))
            return
        try:
            if not device:
                created = self._api("POST", "/api/devices")
                device = {"id": created["id"], "secret": created["secret"], "url": created["url"]}
            device["name"] = name
            DEVICE_FILE.parent.mkdir(mode=0o700, exist_ok=True)
            DEVICE_FILE.write_text(json.dumps(device))
            DEVICE_FILE.chmod(0o600)
            self._api("PUT", "/api/devices/" + device["id"],
                      {"name": name, "formulae": formulae, "casks": casks}, secret=device["secret"])
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read().decode()).get("detail")
            except (ValueError, OSError):
                detail = None
            self.log("Sync failed: %s%s" % (e, " (%s)" % detail if detail else ""))
            return
        except (urllib.error.URLError, OSError, ValueError, KeyError, RuntimeError) as e:
            self.log("Sync failed: %s" % e)
            return
        self.log("Saved. Your private page: %s" % device["url"])
        self.log("Delete it any time: re-run this installer with --forget-device")
        self.result["steps"]["sync"] = device["url"]

    def forget_device(self):
        self.section("Forget this Mac")
        device = self._load_device()
        if not device:
            self.log("This Mac isn't synced with InitMac; nothing to delete.")
            return
        if self.dry_run:
            self.log("[dry-run] would delete %s and %s" % (device.get("url"), DEVICE_FILE))
            return
        try:
            self._api("DELETE", "/api/devices/" + device["id"], secret=device["secret"])
        except urllib.error.HTTPError as e:
            if e.code != 404:
                self.log("Couldn't delete it on the server: %s" % e)
                return
        except (urllib.error.URLError, OSError) as e:
            self.log("Couldn't reach InitMac: %s" % e)
            return
        DEVICE_FILE.unlink()  # only InitMac's own sync file
        self.log("Deleted this Mac's saved app list from InitMac and from %s." % DEVICE_FILE)

    def summary(self):
        names = {a["id"]: a["name"] for a in CONFIG["apps"]}
        r = self.result
        self.section("Summary")
        self.log("%s: %s" % ("Would install" if self.dry_run else "Installed",
                             ", ".join(names[i] for i in r["installed"]) or "-"))
        self.log("Already there: %s" % (", ".join(names[i] for i in r["skipped"]) or "-"))
        self.log("Failed: %s" % (", ".join(names[i] for i in r["failed"]) or "-"))
        if r["failed"]:
            self.log("Search the log for the failed names to see why: %s" % self.log_path)
        self.log("Log: %s" % self.log_path)
        if not self.dry_run and not r["failed"] and r["installed"]:
            self.log("")
            self.log("Enjoying InitMac? A star on GitHub helps others find it: %s" % REPO_URL)
        # Machine-readable line for the local web app.
        print("@@RESULT " + json.dumps(r), flush=True)


def _count(n, word):
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def _defaults_value(typ, value):
    if typ == "bool":
        return "true" if value else "false"
    if typ == "string":
        return os.path.expanduser(str(value))
    return str(value)


def _restore_value(typ, raw):
    if typ == "bool":
        return "true" if raw in ("1", "true", "YES") else "false"
    return raw


def main():
    parser = argparse.ArgumentParser(description="InitMac installer")
    parser.add_argument("--dry-run", action="store_true", help="show what would happen without changing anything")
    parser.add_argument("--yes", "-y", action="store_true", help="never prompt; use the values chosen on the website")
    parser.add_argument("--restore-defaults", action="store_true", help="undo macOS defaults changes made by this script")
    parser.add_argument("--sync", action="store_true", help="only save this Mac's app list to InitMac (opt-in)")
    parser.add_argument("--forget-device", action="store_true", help="delete this Mac's saved app list from InitMac")
    args = parser.parse_args()

    inst = Installer(dry_run=args.dry_run, assume_yes=args.yes)
    inst.check_platform()
    if args.restore_defaults:
        inst.restore_defaults()
        return
    if args.forget_device:
        inst.forget_device()
        return
    if args.sync:
        inst.sync_device()
        return
    if args.dry_run:
        inst.log("DRY RUN: nothing will be changed.")
    if not inst.confirm_plan():
        return
    inst.command_line_tools()
    inst.homebrew()
    inst.install_apps()
    inst.git_and_ssh()
    inst.macos_defaults()
    inst.summary()
    if inst.interactive and CONFIG.get("server") and inst.confirm(
        "Save this Mac's app list to InitMac so you can see it or copy it to another Mac?"
    ):
        inst.sync_device()
    sys.exit(1 if inst.result["failed"] else 0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(130)
