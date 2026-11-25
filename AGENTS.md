# Working on Blip

Blip is a Python desktop tray companion, not a web app or a ChatGPT Pet. Start with `readme.md` for user-visible behavior. `agent.md` is a discovery pointer to this file.

## Repository map

- `main.py`: every runtime feature, native window integration, animation, tray, Gmail watcher, screen preview, CLI, and `__version__`.
- `test_main.py`: stdlib unittest suite. GUI tests are opt-in with `BLIP_GUI_TESTS=1`.
- `pyproject.toml`: package metadata, the `blip` GUI entry point, and lint configuration.
- `requirements.txt`: the two direct runtime dependencies, Pillow and pystray.
- `requirements-dev.txt`: build and verification tools; these are not app runtime dependencies.
- `Blip.spec`, `scripts/build.py`: target-platform desktop bundle creation.
- `.github/workflows/`: CI and tag-triggered releases.
- `docs/images/`: controlled, app-only illustrations suitable for public documentation.

## Implementation rules

1. Preserve the single-file runtime design. Packaging scripts and tests may be separate.
2. Keep Tk on the main thread. Workers communicate with the UI through the queue; do not call Tk from tray, network, or capture threads.
3. Keep the visible pet and reply buttons clickable while transparent space lets input pass through. Do not enable an unconditional whole-window click-through flag.
4. Window close hides Blip; the menu's Quit command performs shutdown. Cancel Tk timers, stop workers, and remove the tray icon during shutdown.
5. Gmail stays disabled until enabled by the user. Use TLS, a read-only inbox, header-only BODY.PEEK, and a starting UID baseline. Never persist or log passwords.
6. Screen capture happens only after the user invokes it. Do not add recording, OCR services, uploads, telemetry, or automatic screenshot saves.
7. Keep native integrations behind platform guards. Native Wayland is unsupported; do not advertise macOS/Linux desktop behavior as verified based only on unit tests or successful packaging.
8. Do not commit `.venv`, `.tools`, `build`, `dist`, local logs, real credentials, or user desktop captures. The `.tools/tk-runtime` fallback serves this development workspace and is not a distributable dependency.

## Verification

Use the active virtual environment; activation is optional. On this Windows workspace the interpreter is `.venv\Scripts\python.exe`.

```text
python -m ruff check .
python -m unittest discover -v
python -m build
python -m twine check dist/*.whl dist/*.tar.gz
```

For a change to window, tray, animation, capture, or shutdown behavior, also run the opt-in desktop suite in an interactive session. It captures the desktop into memory as part of testing. Do not silently skip a failed capture test and report that all desktop checks passed.

For release changes, run `python scripts/build.py`, verify the archive checksum, and smoke-test the extracted application. Keep `main.__version__`, `CHANGELOG.md`, and the release tag aligned. Release automation uses action commit SHAs; preserve that convention.

Do not spawn sub-agents unless the user explicitly requests delegation. Continue routine local implementation and verification without adding approval steps beyond the user's authorized scope.
