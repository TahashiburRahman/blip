"""Write GitHub release notes from this version's changelog section."""

import argparse
from pathlib import Path
import re


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("version")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    content = (Path(__file__).resolve().parents[1] / "CHANGELOG.md").read_text(encoding="utf-8")
    section = re.search(rf"^## {re.escape(args.version)}\b[^\n]*\n(.*?)(?=^## |\Z)",
                        content, flags=re.MULTILINE | re.DOTALL)
    if not section:
        raise SystemExit(f"CHANGELOG.md has no entry for {args.version}")
    notes = f"Blip {args.version}\n\n" + section[1].strip() + "\n\n"
    notes += ("Download the archive for your OS and extract it completely before launching Blip. "
              "Python is included in desktop bundles. On Windows, run `Blip/Blip.exe`. "
              "Windows desktop behavior has been tested; macOS and Linux bundles are experimental.\n\n"
              "Downloads are not signed with Windows/Apple developer certificates. "
              "See the README for permissions, platform requirements, and source installation. "
              "`SHA256SUMS` lists the release-file checksums.\n")
    args.output.write_text(notes, encoding="utf-8")


if __name__ == "__main__":
    main()
