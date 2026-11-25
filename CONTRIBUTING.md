# Contributing to Blip

Open an issue with the operating system, Python or release version, steps to reproduce, and expected behavior. Please remove email addresses, passwords, personal file paths, and desktop contents from attachments.

For a code change, create a branch, keep application logic in `main.py`, and follow [AGENTS.md](AGENTS.md). Install `requirements-dev.txt`, run lint and unit tests, and run desktop tests when changing UI, tray, capture, or shutdown behavior. Document the platforms you actually tested.

Build dependencies belong in `requirements-dev.txt`; the app's direct runtime dependencies are limited to Pillow and pystray. Add a focused behavioral test for a meaningful bug fix. Keep user-facing changes and release versions in `CHANGELOG.md`.

Pull requests should explain the concrete behavior that changes and the validation performed. Never include credentials, private screen captures, generated bundles, or virtual environments in a pull request.
