"""Static safety scanner shared by the installer and checkup safety tests."""

import ast
import re

ALLOWED_URL_PREFIXES = (
    "https://raw.githubusercontent.com/Homebrew/",
    "https://github.com/settings/",
    "https://github.com/initmac-app/initmac",
    "http://127.0.0.1",
    "http://localhost",
)

DANGEROUS_PROGRAMS = {"sudo", "rm", "rmdir", "chmod", "chown", "dd", "diskutil", "mkfs", "launchctl", "csrutil", "curl", "sh"}

FORBIDDEN_CALLS = {
    "eval", "exec", "compile", "__import__",
    "os.remove", "os.unlink", "os.rmdir", "os.removedirs", "os.rename", "os.replace", "os.system",
    "os.popen", "os.chown", "shutil.rmtree", "shutil.move", "shutil.copy", "shutil.copyfile",
    "subprocess.getoutput", "subprocess.check_output",
}


def _dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None


def static_violations(source: str, allow_programs: frozenset = frozenset()) -> list[str]:
    """allow_programs: programs a specific script may name (e.g. the checkup's read-only `csrutil status`);
    the behavioural sandbox tests then pin down exactly which arguments they're called with."""
    dangerous = DANGEROUS_PROGRAMS - set(allow_programs)
    problems = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _dotted(node.func) or ""
            if name in FORBIDDEN_CALLS:
                problems.append(f"forbidden call {name}()")
            if any(k.arg == "shell" for k in node.keywords):
                problems.append(f"shell= keyword in {name}()")
            if isinstance(node.func, ast.Attribute) and node.func.attr in ("unlink", "rmdir", "rename", "replace"):
                receiver = _dotted(node.func.value)
                if receiver not in ("BACKUP_FILE", "DEVICE_FILE"):
                    problems.append(f"deletes {receiver} (only BACKUP_FILE/DEVICE_FILE allowed)")
        if isinstance(node, ast.List):
            # Command argument lists, e.g. ["sudo", ...] or ["rm", "-rf", ...]
            words = {e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}
            bad = words & dangerous
            if bad:
                problems.append(f"dangerous command in argument list: {sorted(bad)}")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            # Shell snippets (e.g. the bash -c / askpass strings) starting a dangerous command.
            for word in re.findall(r"(?:^|[;&|`(]\s*)(sudo|rm|chmod|chown|diskutil|dd)\s", node.value):
                problems.append(f"dangerous shell command in string: {word!r}")
            for url in re.findall(r"https?://[^\s\"')]+", node.value):
                if not url.startswith(ALLOWED_URL_PREFIXES):
                    problems.append(f"unexpected URL {url}")
    return problems
