# Releasing Blip

The first supported desktop target is Windows x64. macOS and Linux bundles remain experimental until their desktop acceptance checks are completed. A matrix build passing is evidence that a package builds, not that every desktop integration works.

1. Update the literal `__version__` in `main.py` and add the matching `CHANGELOG.md` entry. Package metadata reads that version automatically.
2. Install `requirements-dev.txt`. Run `python -m ruff check .`, `python -m unittest discover -v`, `python -m build`, and `python -m twine check dist/*.whl dist/*.tar.gz`.
3. On a real desktop for each supported platform, run `BLIP_GUI_TESTS=1 python -m unittest -v test_main` (use `$env:BLIP_GUI_TESTS = "1"` in PowerShell). The desktop suite performs one in-memory screen capture and uses a simulated Gmail mailbox.
4. Run `python scripts/build.py` on each target OS. Extract the resulting archive somewhere outside the checkout and verify startup, transparency/input pass-through, a full walk/turn, left-click bounce/staff spin, all mood replies, bubble expiry, hide/restore, user-initiated screen capture/save, and Quit. Check the output's dependency notices and SHA-256 checksum.
5. For Gmail changes, separately verify an actual account with a disposable app password. Never put the password in source files, test fixtures, screenshots, logs, CI variables, or issue text. Delete the test credential from the account afterward.
6. Commit and push `main`, then create and push a matching tag, such as `v0.1.0`. The release workflow verifies the tag/version, runs tests, builds on each OS, and uploads the platform archives plus source/wheel packages and checksums to a GitHub release.

```bash
git tag -a v0.1.0 -m "Blip 0.1.0"
git push origin v0.1.0
```

The workflow can also be run manually for an existing version tag. The release automation only uses GitHub's repository token; PyPI publishing is not configured. Installers are not code signed/notarized, and no signing certificate is included in this repository.

For a local build, use `python scripts/build.py`. Keep all generated output out of Git. Build folders contain the full application: shipping just `Blip.exe` from a folder build will omit its dependencies.
