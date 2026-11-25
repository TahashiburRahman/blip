# AI entry point

This is **Blip**, a Python desktop tray pet. The repository's canonical AI contributor instructions are in [AGENTS.md](AGENTS.md); read that file before changing code.

- Product, installation, privacy, and platform support: [readme.md](readme.md).
- Runtime implementation and version: [main.py](main.py).
- Behavioral and opt-in desktop tests: [test_main.py](test_main.py).
- Packaging: [pyproject.toml](pyproject.toml), [Blip.spec](Blip.spec), and [scripts/build.py](scripts/build.py).
- Release process: [docs/releasing.md](docs/releasing.md).

Keep all runtime behavior in `main.py`. Gmail is opt-in and read-only; screen capture is user-initiated and local. Never add real credentials or captured desktop contents to the repository.
