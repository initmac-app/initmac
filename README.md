# InitMac installer & app catalog

[![CI](https://github.com/initmac-app/initmac/actions/workflows/ci.yml/badge.svg)](https://github.com/initmac-app/initmac/actions/workflows/ci.yml)
[![Catalog check](https://github.com/initmac-app/initmac/actions/workflows/catalog-check.yml/badge.svg)](https://github.com/initmac-app/initmac/actions/workflows/catalog-check.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

This is the open-source part of **[initmac.in](https://initmac.in)**: the script that runs on
your Mac, the launcher that downloads and verifies it, and the catalog of apps it can install.
If you're going to paste a command into Terminal, you should be able to read exactly what it does.
That's what this repository is for.

## What the installer does

When you run your InitMac command, it:

1. **Shows you a plan and asks `Continue? [y/N]`** before changing anything.
2. Installs Xcode Command Line Tools and Homebrew if they're missing.
3. Installs the apps you picked with Homebrew, skipping ones you already have, including apps
   you installed yourself from the vendor's website (those are left exactly as they are).
4. Optionally sets your Git name/email and creates an SSH key (only if you don't have one).
5. Optionally applies the macOS settings you ticked, after backing up your originals.
6. Optionally (only if you say yes) saves your app list to a private page on initmac.in.

**It never** deletes or overwrites your files, runs `sudo` itself, or sends data anywhere
unless you opt in to sync. Undo options: `--restore-defaults`, `--forget-device`,
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
