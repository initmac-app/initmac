#!/usr/bin/env python3
"""InitMac checkup: a read-only health report for your Mac.

Looks at free space, battery health, security settings, pending updates, background
startup items and memory, then offers fixes. Nothing changes unless you answer yes, and
it never deletes your files: for folders worth cleaning it opens them in Finder, and for
security settings it opens the right System Settings page so the change is your own click.

Usage:
    python3 checkup.py                # report, then offer fixes one by one
    python3 checkup.py --report-only  # just the report, no questions
    python3 checkup.py --json         # machine-readable report

Only uses the Python standard library and commands that don't need admin rights.
"""

import argparse
import datetime
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

HOME = Path.home()
LOG_DIR = HOME / "mac-setup-logs"
BREW_PREFIXES = ["/opt/homebrew", "/usr/local"]
REPO_URL = "https://github.com/initmac-app/initmac"
FIREWALL_TOOL = "/usr/libexec/ApplicationFirewall/socketfilterfw"
LAUNCH_DIRS = [HOME / "Library" / "LaunchAgents", Path("/Library/LaunchAgents"), Path("/Library/LaunchDaemons")]
LOW_SPACE_FRACTION = 0.15          # warn when less than 15% of the disk is free
BIG_FOLDER_BYTES = 1 * 1024 ** 3   # offer to open folders bigger than 1 GB
BIG_BREW_CACHE_BYTES = 500 * 1024 ** 2
SIZE_TIME_LIMIT = 20               # seconds spent measuring each folder at most
SETTINGS = {
    "firewall": "x-apple.systempreferences:com.apple.preference.security?Firewall",
    "filevault": "x-apple.systempreferences:com.apple.preference.security?FileVault",
    "backup": "x-apple.systempreferences:com.apple.prefs.backup",
    "updates": "x-apple.systempreferences:com.apple.Software-Update-Settings.extension",
}
OK, WARN = "✓", "⚠"


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return ("%.0f %s" if unit in ("B", "KB") else "%.1f %s") % (n, unit)
        n /= 1024.0


def folder_size(path, skip=(), time_limit=SIZE_TIME_LIMIT):
    """Total size of a folder, skipping unreadable parts. Returns (bytes, complete)."""
    deadline = time.monotonic() + time_limit
    total = 0
    for root, dirs, files in os.walk(path, onerror=lambda e: None):
        dirs[:] = [d for d in dirs if os.path.join(root, d) not in skip]
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
        if time.monotonic() > deadline:
            return total, False
    return total, True


class Checkup:
    def __init__(self, report_only=False, assume_yes=False, quiet=False):
        self.report_only = report_only
        self.interactive = sys.stdin.isatty() and not assume_yes and not report_only
        self.quiet = quiet
        LOG_DIR.mkdir(exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.log_path = LOG_DIR / ("checkup-%s.log" % stamp)
        self.log_file = open(self.log_path, "a", encoding="utf-8")
        self.results = {}
        self.fixes = []  # (question, explanation, action)

    # ---- helpers ---------------------------------------------------------
    def log(self, msg=""):
        if not self.quiet:
            print(msg, flush=True)
        self.log_file.write(msg + "\n")
        self.log_file.flush()

    def output(self, cmd, timeout=30):
        """Run a read-only command and return its stdout ('' on any failure)."""
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False).stdout
        except (OSError, subprocess.SubprocessError):
            return ""

    def run(self, cmd):
        """Run a command the user agreed to, streaming its output. Returns exit code."""
        self.log("$ " + " ".join(cmd))
        try:
            return subprocess.call(cmd)
        except OSError as e:
            self.log("  couldn't run it: %s" % e)
            return 1

    def _input(self, prompt):
        try:
            return input(prompt).strip()
        except EOFError:
            print()
            return ""

    def confirm(self, question):
        if not self.interactive:
            return False
        return self._input("%s [y/N]: " % question).lower().startswith("y")

    def brew(self):
        for prefix in BREW_PREFIXES:
            path = Path(prefix) / "bin" / "brew"
            if path.exists():
                return str(path)
        return None

    # ---- checks (all read-only) -------------------------------------------
    def check_machine(self):
        hw = self.output(["system_profiler", "SPHardwareDataType"], timeout=20)
        model = re.search(r"Model Name:\s*(.+)", hw)
        chip = re.search(r"(?:Chip|Processor Name):\s*(.+)", hw)
        boot = re.search(r"sec = (\d+)", self.output(["sysctl", "-n", "kern.boottime"]))
        uptime_days = (time.time() - int(boot.group(1))) / 86400 if boot else None
        self.results["machine"] = {
            "model": model.group(1).strip() if model else "Mac",
            "chip": chip.group(1).strip() if chip else platform.machine(),
            "macos": self.output(["sw_vers", "-productVersion"]).strip() or platform.mac_ver()[0],
            "uptime_days": round(uptime_days, 1) if uptime_days is not None else None,
        }

    def check_space(self):
        usage = shutil.disk_usage("/")
        brew = self.brew()
        brew_cache = Path(self.output([brew, "--cache"]).strip()) if brew else None
        folders = []
        candidates = [
            ("Homebrew downloads", brew_cache),
            ("Xcode build files (DerivedData)", HOME / "Library" / "Developer" / "Xcode" / "DerivedData"),
            ("iOS simulators", HOME / "Library" / "Developer" / "CoreSimulator"),
            ("App caches", HOME / "Library" / "Caches"),
        ]
        for label, path in candidates:
            if not path or not path.is_dir():
                continue
            skip = {str(brew_cache)} if label == "App caches" and brew_cache else set()
            size, complete = folder_size(str(path), skip=skip)
            folders.append({"label": label, "path": str(path), "bytes": size, "complete": complete})
        self.results["space"] = {"total": usage.total, "free": usage.free, "folders": folders,
                                 "brew_cache": str(brew_cache) if brew_cache else None}

    def check_battery(self):
        power = self.output(["system_profiler", "SPPowerDataType"], timeout=20)
        if "Cycle Count" not in power:
            self.results["battery"] = None  # desktop Mac
            return
        cycles = re.search(r"Cycle Count:\s*(\d+)", power)
        condition = re.search(r"Condition:\s*(.+)", power)
        capacity = re.search(r"Maximum Capacity:\s*(\d+)%", power)
        self.results["battery"] = {
            "cycles": int(cycles.group(1)) if cycles else None,
            "condition": condition.group(1).strip() if condition else "Unknown",
            "max_capacity": int(capacity.group(1)) if capacity else None,
        }

    def check_security(self):
        firewall = self.output([FIREWALL_TOOL, "--getglobalstate"])
        tm = self.output(["tmutil", "destinationinfo"])
        self.results["security"] = {
            "filevault": "FileVault is On" in self.output(["fdesetup", "status"]),
            "firewall": bool(re.search(r"enabled|State = [12]", firewall)) and "disabled" not in firewall,
            "gatekeeper": "assessments enabled" in self.output(["spctl", "--status"]),
            "sip": "status: enabled" in self.output(["csrutil", "status"]),
            "backup": bool(tm.strip()) and "No destinations configured" not in tm,
        }

    def start_update_check(self):
        """softwareupdate takes ~30 s, so it runs in the background while other checks happen."""
        box = {}

        def work():
            box["out"] = self.output(["softwareupdate", "--list"], timeout=120)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        return thread, box

    def finish_update_check(self, thread, box):
        thread.join(130)
        out = box.get("out", "")
        titles = re.findall(r"Title:\s*([^,]+)", out)
        outdated = {"formulae": [], "casks": []}
        brew = self.brew()
        if brew:
            try:
                data = json.loads(self.output([brew, "outdated", "--json=v2"]) or "{}")
                outdated = {k: sorted(x["name"] for x in data.get(k, [])) for k in ("formulae", "casks")}
            except ValueError:
                pass
            # Only mention formulae you installed yourself; their dependencies upgrade along with them.
            requested = set(n.split("/")[-1] for n in self.output([brew, "leaves", "--installed-on-request"]).split())
            if requested:
                outdated["formulae"] = [n for n in outdated["formulae"] if n in requested]
        self.results["updates"] = {"macos": titles, "brew": outdated, "checked": bool(out)}

    def check_startup(self):
        items = []
        for folder in LAUNCH_DIRS:
            try:
                items += sorted(p.name[:-6] for p in folder.iterdir() if p.name.endswith(".plist"))
            except OSError:
                pass
        self.results["startup"] = items

    def check_memory(self):
        level = self.output(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"]).strip()
        self.results["memory"] = {"pressure": {"1": "normal", "2": "warning", "4": "critical"}.get(level, "unknown")}

    def run_checks(self):
        self.log("Checking your Mac (read-only)…")
        thread, box = self.start_update_check()
        self.check_machine()
        self.check_space()
        self.check_battery()
        self.check_security()
        self.check_startup()
        self.check_memory()
        self.log("Waiting for the macOS update check (can take ~30 s)…")
        self.finish_update_check(thread, box)

    # ---- report & fixes -----------------------------------------------------
    def report(self):
        r = self.results
        m = r["machine"]
        up = " · up %d days" % m["uptime_days"] if m["uptime_days"] is not None else ""
        self.log("")
        self.log("InitMac checkup · %s (%s) · macOS %s%s" % (m["model"], m["chip"], m["macos"], up))
        self.log("")

        s = r["space"]
        frac = s["free"] / float(s["total"]) if s["total"] else 1
        self.log(" SPACE     %s %s free of %s (%d%%)" % (WARN if frac < LOW_SPACE_FRACTION else OK,
                                                         human(s["free"]), human(s["total"]), frac * 100))
        if s["folders"]:
            self.log("           " + " · ".join("%s %s%s" % (f["label"], human(f["bytes"]), "" if f["complete"] else "+")
                                                for f in s["folders"]))
        for f in s["folders"]:
            if f["path"] == s["brew_cache"] and f["bytes"] >= BIG_BREW_CACHE_BYTES and self.brew():
                self.fixes.append(("Free %s of Homebrew downloads?" % human(f["bytes"]),
                                   "Runs `brew cleanup --prune=all`: deletes only Homebrew's cached installers and old "
                                   "app versions. Can't be undone, but Homebrew re-downloads anything it needs.",
                                   [self.brew(), "cleanup", "--prune=all"]))
            elif f["path"] != s["brew_cache"] and f["bytes"] >= BIG_FOLDER_BYTES:
                self.fixes.append(("Open %s (%s) in Finder?" % (f["label"], human(f["bytes"])),
                                   "Just opens the folder. InitMac never deletes your files; you decide what to remove.",
                                   ["open", f["path"]]))

        b = r["battery"]
        if b:
            healthy = (b["max_capacity"] or 100) >= 80 and b["condition"] == "Normal"
            self.log(" BATTERY   %s %s%% of original capacity · %s cycles · condition %s"
                     % (OK if healthy else WARN, b["max_capacity"], b["cycles"], b["condition"]))

        sec = r["security"]
        labels = [("filevault", "FileVault"), ("firewall", "Firewall"), ("gatekeeper", "Gatekeeper"),
                  ("sip", "System Integrity Protection"), ("backup", "Time Machine backup")]
        self.log(" SECURITY  " + "   ".join("%s %s %s" % (OK if sec[k] else WARN, name, "on" if sec[k] else "off")
                                           for k, name in labels))
        if not sec["firewall"]:
            self.fixes.append(("Open Firewall settings?", "The firewall blocks unwanted incoming connections. "
                               "Turning it on needs your password, so InitMac just opens the page.",
                               ["open", SETTINGS["firewall"]]))
        if not sec["filevault"]:
            self.fixes.append(("Open FileVault settings?", "FileVault encrypts your disk so a lost Mac doesn't expose "
                               "your files. InitMac just opens the page.", ["open", SETTINGS["filevault"]]))
        if not sec["backup"]:
            self.fixes.append(("Open Time Machine settings?", "Time Machine keeps hourly backups on an external "
                               "drive. InitMac just opens the page.", ["open", SETTINGS["backup"]]))

        u = r["updates"]
        outdated = u["brew"]["formulae"] + u["brew"]["casks"]
        mac_line = ("%s macOS updates available: %s" % (WARN, ", ".join(u["macos"])) if u["macos"]
                    else ("%s macOS is up to date" % OK if u["checked"] else "? couldn't check macOS updates"))
        brew_line = ("%s %d of your Homebrew apps have updates: %s%s" % (WARN, len(outdated), ", ".join(outdated[:8]),
                                                                           "…" if len(outdated) > 8 else "")
                     if outdated else "%s Homebrew apps up to date" % OK)
        self.log(" UPDATES   %s" % mac_line)
        self.log("           %s (as of your last Homebrew update)" % brew_line)
        if u["macos"]:
            self.fixes.append(("Open Software Update?", "Installs macOS and Safari updates. InitMac just opens the "
                               "page.", ["open", SETTINGS["updates"]]))
        if outdated and self.brew():
            self.fixes.append(("Update %d Homebrew apps?" % len(outdated),
                               "Runs `brew upgrade` for exactly these: %s." % ", ".join(outdated),
                               [self.brew(), "upgrade"] + outdated))

        startup = r["startup"]
        self.log(" STARTUP   %d background items%s" % (len(startup), (": " + ", ".join(startup)) if startup else ""))

        mem = r["memory"]["pressure"]
        days = m["uptime_days"]
        restart = days is not None and days > 14
        self.log(" MEMORY    %s memory pressure %s%s" % (OK if mem == "normal" else WARN, mem,
                                                         "   %s not restarted for %d days" % (WARN, days) if restart else ""))
        self.log("")
        self.log("Report saved to %s" % self.log_path)

    def offer_fixes(self):
        if not self.fixes:
            self.log("Nothing to fix. Your Mac looks good.")
            return
        if not self.interactive:
            self.log("%d suggested fixes (run again without --report-only to be asked about each):" % len(self.fixes))
            for question, _, _ in self.fixes:
                self.log("  - " + question)
            return
        self.log("")
        self.log("Suggested fixes. Nothing happens unless you answer y.")
        for question, explanation, action in self.fixes:
            self.log("")
            self.log("  " + explanation)
            if self.confirm(question):
                self.run(action)
            else:
                self.log("  Skipped.")


def main():
    parser = argparse.ArgumentParser(description="InitMac checkup: a read-only health report for your Mac")
    parser.add_argument("--report-only", action="store_true", help="only print the report; never ask or change anything")
    parser.add_argument("--json", action="store_true", help="print the report as JSON (implies --report-only)")
    parser.add_argument("--yes", "-y", action="store_true", help="non-interactive; same as --report-only")
    args = parser.parse_args()

    if platform.system() != "Darwin":
        print("The InitMac checkup only runs on macOS.")
        sys.exit(1)
    checkup = Checkup(report_only=args.report_only or args.json, assume_yes=args.yes, quiet=args.json)
    checkup.run_checks()
    if args.json:
        print(json.dumps(checkup.results, indent=2))
        return
    checkup.report()
    checkup.offer_fixes()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(130)
