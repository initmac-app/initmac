<h1 align="center">InitMac</h1>

<p align="center"><b>Set up a new Mac in minutes, and see exactly what runs.</b><br>
Pick the apps you need (each with a plain-English explanation), then install them all with one Terminal command.</p>

<p align="center">
  <a href="https://initmac.in"><b>initmac.in</b></a> ·
  <a href="https://initmac.in/ai">AI on your Mac</a> ·
  <a href="#verify-the-script-you-downloaded">Verify a script</a> ·
  <a href="CONTRIBUTING.md">Suggest an app</a>
</p>

<p align="center">
  <a href="https://github.com/initmac-app/initmac/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/initmac-app/initmac/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/initmac-app/initmac/actions/workflows/catalog-check.yml"><img alt="Catalog check" src="https://github.com/initmac-app/initmac/actions/workflows/catalog-check.yml/badge.svg"></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-green.svg"></a>
</p>

<!-- Demo GIF: record a 15–20 s capture of the wizard + Terminal run, save as docs/demo.gif, then replace this comment with:
<p align="center"><img src="docs/demo.gif" alt="InitMac demo" width="760"></p> -->

## Why InitMac

- **Decide faster.** Tell it your role and what you do day to day; it recommends from a curated
  catalog of 84 apps (Raycast, Rectangle, OrbStack, VS Code, 1Password, Obsidian…) and explains
  each one, so you're not googling "best Mac terminal" at midnight.
- **One command.** `bash <(curl -fsSL https://initmac.in/i/<id>/run.sh)` installs everything with
  [Homebrew](https://brew.sh), plus optional Git/SSH setup and a few sensible macOS settings.
- **Safe to run, safe to re-run.** It shows the full plan and asks before changing anything,
  skips apps you already have (even ones you didn't install with Homebrew), and backs up settings.
- **Verifiable, not just "trust me".** This repository is the exact code that runs on your Mac,
  and you can prove the script you downloaded matches it (see below).

## How it works

1. Go to **[initmac.in](https://initmac.in)**, answer two quick questions.
2. Review the recommended apps and tick what you want.
3. Copy the command into Terminal, read the plan, type `y`.

Already set up a Mac you love? Share it as a link, or let InitMac save your app list privately
(the installer offers this at the end; skip it with `--no-sync`) and recreate it on your next Mac.

## AI on your Mac

**[initmac.in/ai](https://initmac.in/ai)**: pick a kit and get one command.

- **Agentic coding:** Claude Code, Codex, Gemini CLI and Cursor (more agents, such as OpenCode, Aider,
  Cline and Qwen Code, at [initmac.in/apps/ai](https://initmac.in/apps/ai)).
- **Local models:** Ollama, LM Studio and the Hugging Face CLI (plus llama.cpp, MLX-LM, Jan, Msty, LLM).

At the end, the installer reads your Mac's memory and shows which open-weight models fit:

```
Open-weight models on this Mac (48 GB memory):
       8 GB+  small 1-4B models (e.g. qwen3:4b, gemma3:4b)
      16 GB+  7-14B models (e.g. qwen3:8b, qwen3:14b)
  ->  32 GB+  up to ~32B models (e.g. qwen3:32b, gemma3:27b)
      64 GB+  70B-class models (e.g. llama3.3:70b) and everything smaller
A good first model for this Mac: qwen3:14b (about 9 GB to download).
```

It offers to download that one starter model with Ollama (default No), then prints the command to
start each agent. It never asks for API keys or accounts: each tool signs you in itself.

## Mac checkup

A free, read-only health report you can run any time:

```bash
bash <(curl -fsSL https://initmac.in/checkup.sh)
```

It reports free space (and which caches are big), battery health, FileVault / firewall /
Gatekeeper / SIP / Time Machine, pending macOS and Homebrew updates, background startup items
and memory pressure. Then it suggests fixes and asks about each one. It never deletes your files:
for big folders it opens them in Finder, and for security settings it opens the right System
Settings page. The only commands it can run are `brew cleanup`, `brew upgrade <your outdated
apps>` and `open`, and only after you type `y`. Source: [`installer/checkup.py`](installer/checkup.py),
held to the same sandbox tests as the installer.

## What the installer does

When you run your InitMac command, it:

1. **Shows you a plan and asks `Continue? [y/N]`** before changing anything.
2. Installs Xcode Command Line Tools and Homebrew if they're missing.
3. Installs the apps you picked with Homebrew, skipping ones you already have, including apps
   you installed yourself from the vendor's website (those are left exactly as they are).
4. Optionally sets your Git name/email and creates an SSH key (only if you don't have one).
5. Optionally applies the macOS settings you ticked, after backing up your originals.
6. Offers to save your app list (Homebrew package names only) to a private page on initmac.in.
   Pressing Enter saves it; type `n` or run with `--no-sync` to skip.

**It never** deletes or overwrites your files, runs `sudo` itself, or sends anything except
that app list. Undo options: `--restore-defaults`, `--forget-device`,
`brew uninstall <name>`.

## How that's enforced, not just promised

- `tests/test_installer_safety.py` runs the real generated installer with **every** app, setting
  and option turned on, inside a sandbox (fake home folder, fake Homebrew, fake network) and fails
  if it runs any command, writes any file or contacts any server outside a short allowlist. It also
  statically scans the template for dangerous calls, and proves the scanner works by injecting
  harmful lines and checking they're caught.
- The launcher (`installer/launcher.sh.tmpl`) refuses to run the installer unless its SHA-256
  matches the fingerprint shown on the website.
- CI runs all of this on macOS for every change.

## Verify the script you downloaded

Every generated script says which commit of this repo it was built from (line 2). Check it:

```bash
curl -fsSL https://initmac.in/i/<your-id> -o initmac-install.py
curl -fsSL https://raw.githubusercontent.com/initmac-app/initmac/main/tools/verify_script.py -o verify_script.py
python3 verify_script.py initmac-install.py
# ✓ Verified: matches github.com/initmac-app/initmac @ <commit>
```

It confirms the code is byte-for-byte the public template and that every app and setting in your
`CONFIG` block is an exact entry from the public catalog at that commit.

## Repository layout

| Path | What |
|---|---|
| `installer/install_template.py` | The installer (Python standard library only, 3.9+). `CONFIG` is filled in per user |
| `installer/launcher.sh.tmpl` | The `bash <(curl …)` launcher: Command Line Tools + fingerprint check |
| `installer/render.py` | How initmac.in renders both (the exact code the site uses) |
| `catalog/catalog.json` | Every app: Homebrew name, type, category, explanation, who it's for |
| `catalog/macos_defaults.json` | The optional macOS settings |
| `catalog/questionnaire.json` | Roles, daily activities and recommendation weights |
| `tools/check_brew_tokens.py` | Checks every catalog entry still exists in Homebrew |
| `tools/verify_script.py` | Verifies a downloaded installer against this repo |

## Run the tests

```bash
uv run pytest            # or: python3 -m pytest
```

## Contributing

App suggestions and fixes are very welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).
Security issues: please follow [SECURITY.md](SECURITY.md) instead of opening a public issue.

## License

MIT. The initmac.in website itself (wizard, share links, sync service) is a separate, private codebase.
