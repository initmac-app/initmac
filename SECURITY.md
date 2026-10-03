# Security policy

InitMac's installer runs on people's Macs, so we take reports seriously.

## Reporting a vulnerability

Please **don't open a public issue**. Use GitHub's private reporting instead:
**Security tab → Report a vulnerability** on this repository. You'll get a response within a few days.

## In scope

- The installer, launcher and rendering code in this repository
- Anything that would let initmac.in serve a script that differs from this repository's template
  without `tools/verify_script.py` noticing
- Catalog entries that could install something other than what they describe

The initmac.in web service is also in scope; report it the same way.
