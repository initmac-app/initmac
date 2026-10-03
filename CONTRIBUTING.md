# Contributing to InitMac

The most useful contributions are to the **app catalog**: new apps, better explanations, and
fixes when Homebrew renames something.

## Adding or fixing an app

Edit `catalog/catalog.json`. Each entry looks like:

```json
{"id": "rectangle", "name": "Rectangle", "brew": "rectangle", "type": "cask", "category": "productivity", "core": true,
 "description": "Snap windows to halves, thirds and corners with keyboard shortcuts.",
 "why": "macOS window management is basic; this makes split-screen work effortless.",
 "audiences": ["role_dev", "role_general", "act_code"], "alternatives": ["Magnet"], "homepage": "https://rectangleapp.com"}
```

- `brew` / `type`: exactly what you'd pass to `brew install` (`--cask` for `"type": "cask"`).
  Only Homebrew's own repositories and the taps listed in `tests/test_catalog.py` are allowed.
- `description`: what it is, in one sentence. `why`: why someone would want it.
- `audiences`: roles/activities from `catalog/questionnaire.json` it's useful for.
- `core`: `true` only if it's the obvious default pick for those audiences (it gets pre-selected).
- `homepage`: must be `https://`.

Before opening a pull request:

```bash
brew info --cask <name>          # or --formula: make sure the name is right
uv run pytest                    # catalog + safety tests
python3 tools/check_brew_tokens.py
```

## Changing the installer

Changes to `installer/` get extra scrutiny because that code runs on people's Macs. Keep it to the
Python standard library, never use a shell string, and expect to update the allowlists in
`tests/test_installer_safety.py` with a clear reason if you add a command, file or URL.

Every pull request runs the full test suite and a Homebrew check on macOS. Nothing merges without
a maintainer's review.
