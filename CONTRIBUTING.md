# Contributing to Phoney

Thanks for helping. This is a small hobby project, so short, focused changes are easiest to review.

## Reporting a bug

Open an issue with:

- What you did, what you expected, what happened.
- Windows version, phone model and iOS version.
- The agent version (shown on the status page at http://127.0.0.1:7421).
- The relevant lines from `%APPDATA%\Phoney\logs\agent.log`. Check them first: remove your PC name, IP addresses, project paths or anything else you do not want public.

Security problems go through private reporting instead; see [docs/SECURITY.md](docs/SECURITY.md#7-reporting-a-vulnerability).

## Sending a change

1. Set up as in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).
2. Make the change. Keep to the conventions there: type hints and Pydantic at boundaries in Python, no dependencies in the panel's JavaScript, OS effects only through `PlatformOps` so tests stay fake.
3. Add or update a test. Run, from `desktop\agent`:
   ```powershell
   & $py -m pytest -q
   & $py -m ruff check .
   & $py -m ruff format .
   ```
4. If you changed the protocol, update `docs/API.md` in the same pull request.
5. Open a pull request saying what changed and how you checked it. Screenshots help for panel changes; use demo data, not your own.

New features that let the phone do more on the PC (new action kinds, new remote controls) need a note on the security impact. The rule that the phone can only trigger actions listed in a file on the PC is not up for change.

By contributing you agree that your contribution is licensed under the [MIT License](LICENSE).
