"""Verify the frozen application's CLI starts without opening the desktop UI."""

from pathlib import Path
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    if sys.platform == "win32":
        binary = root / "dist" / "Blip" / "Blip.exe"
    elif sys.platform == "darwin":
        binary = root / "dist" / "Blip.app" / "Contents" / "MacOS" / "Blip"
    else:
        binary = root / "dist" / "Blip" / "Blip"
    if not binary.is_file():
        raise SystemExit(f"Build the desktop app first: {binary}")
    subprocess.run([str(binary), "--version"], cwd=binary.parent, check=True, timeout=30)
    print("Bundled Blip starts and exits successfully with --version.")


if __name__ == "__main__":
    main()
