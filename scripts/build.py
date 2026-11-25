"""Build a portable Blip bundle on the current OS. No publishing or network I/O."""

import ast
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys

from packaging.requirements import Requirement


ROOT = Path(__file__).resolve().parents[1]


def version():
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets):
            return ast.literal_eval(node.value)
    raise RuntimeError("main.py has no literal __version__")


def sha256(path):
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def make_assets(release_version):
    assets = ROOT / ".build-assets"
    assets.mkdir(exist_ok=True)
    sys.path.insert(0, str(ROOT))
    from main import tray_image

    image = tray_image().resize((256, 256))
    image.save(assets / "blip.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (256, 256)])
    if sys.platform == "darwin":
        image.resize((1024, 1024)).save(assets / "blip.icns")
    numbers = tuple(int(part) for part in release_version.split(".")) + (0,)
    text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={numbers}, prodvers={numbers}, mask=0x3f,
                   flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', 'Tahashibur Rahman'),
    StringStruct('FileDescription', 'Blip desktop companion'),
    StringStruct('FileVersion', '{release_version}'),
    StringStruct('InternalName', 'Blip'),
    StringStruct('LegalCopyright', 'Copyright 2026 Tahashibur Rahman. MIT license.'),
    StringStruct('OriginalFilename', 'Blip.exe'),
    StringStruct('ProductName', 'Blip'),
    StringStruct('ProductVersion', '{release_version}')
  ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)"""
    (assets / "windows-version.txt").write_text(text, encoding="utf-8")


def runtime_distributions():
    pending, found = ["Pillow", "pystray"], {}
    while pending:
        name = pending.pop()
        key = re.sub(r"[-_.]+", "-", name).lower()
        if key in found:
            continue
        dist = metadata.distribution(name)
        found[key] = dist
        for value in dist.requires or []:
            requirement = Requirement(value)
            if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
                pending.append(requirement.name)
    return found


def write_notices(bundle):
    licenses = bundle / "licenses"
    licenses.mkdir(exist_ok=True)
    dists = runtime_distributions()
    # Include the bootloader's redistribution license and exception as well.
    notices = ["Blip third-party notices", "", "Bundled dependencies retain their own licenses.",
               "Blip source: https://github.com/TahashiburRahman/blip", ""]
    for name, dist in sorted({**dists, "pyinstaller": metadata.distribution("pyinstaller")}.items()):
        target = licenses / name
        target.mkdir(exist_ok=True)
        notices.append(f"{dist.metadata['Name']} {dist.version}")
        for url in dist.metadata.get_all("Project-URL") or []:
            notices.append(f"  {url}")
        if dist.metadata.get("Home-page"):
            notices.append(f"  Homepage: {dist.metadata['Home-page']}")
        for item in dist.files or []:
            if not item.name.lower().startswith(("license", "copying", "notice", "copyright")):
                continue
            source = Path(dist.locate_file(item))
            if source.is_file():
                shutil.copy2(source, target / source.name)
        notices.append("")
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.exists():
        python_license = Path(sys.base_prefix) / "LICENSE"
    if python_license.is_file():
        shutil.copy2(python_license, licenses / "PYTHON-LICENSE.txt")
    for library in ("TCL_LIBRARY", "TK_LIBRARY"):
        source = Path(os.getenv(library, "")) / "license.terms"
        if source.is_file():
            shutil.copy2(source, licenses / f"{library}-license.terms")
    notices += ["Python: https://www.python.org/psf/license/",
                "Tcl/Tk: https://www.tcl-lang.org/software/tcltk/license.html", ""]
    (bundle / "THIRD_PARTY_NOTICES.txt").write_text("\n".join(notices), encoding="utf-8")
    return {dist.metadata["Name"]: dist.version for dist in dists.values()}


def main():
    release_version = version()
    if not re.fullmatch(r"\d+\.\d+\.\d+", release_version):
        raise SystemExit("Use a three-part numeric release version in main.py.")
    # This checkout may use a lean Python install without its Tcl script files.
    local_runtime = ROOT / ".tools" / "tk-runtime"
    if sys.platform == "win32" and (local_runtime / "tcl_library" / "init.tcl").is_file():
        os.environ.setdefault("TCL_LIBRARY", str(local_runtime / "tcl_library"))
        os.environ.setdefault("TK_LIBRARY", str(local_runtime / "tk_library"))
    os.environ["BLIP_BUILD_VERSION"] = release_version
    os.environ.setdefault("PYINSTALLER_CONFIG_DIR", str(ROOT / ".tools" / "pyinstaller-cache"))
    make_assets(release_version)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "Blip.spec"],
                   cwd=ROOT, check=True)
    output = ROOT / "dist"
    bundle = output / "Blip"
    for name in ("LICENSE", "readme.md", "CHANGELOG.md"):
        shutil.copy2(ROOT / name, bundle / name)
    dependencies = write_notices(bundle)
    info = {
        "application": "Blip", "version": release_version,
        "python": platform.python_version(), "platform": sys.platform,
        "architecture": platform.machine(),
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "pyinstaller": metadata.version("pyinstaller"), "runtime_dependencies": dependencies,
    }
    (bundle / "BUILD-INFO.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    machine = platform.machine().lower()
    architecture = "x86_64" if machine in ("amd64", "x86_64") else "arm64" if machine in ("arm64", "aarch64") else machine
    system = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    stem = f"Blip-v{release_version}-{system}-{architecture}"
    if sys.platform == "darwin":
        # BUNDLE is already signed ad hoc by PyInstaller; keep its contents intact.
        stage = output / "macos-release"
        stage.mkdir(exist_ok=True)
        staged_app = stage / "Blip.app"
        if staged_app.exists():
            if staged_app.resolve().parent != stage.resolve():
                raise RuntimeError("Unexpected staging target")
            shutil.rmtree(staged_app)
        shutil.copytree(output / "Blip.app", staged_app, symlinks=True)
        for name in ("LICENSE", "readme.md", "CHANGELOG.md", "THIRD_PARTY_NOTICES.txt", "BUILD-INFO.json"):
            shutil.copy2(bundle / name, stage / name)
        shutil.copytree(bundle / "licenses", stage / "licenses", dirs_exist_ok=True)
        archive = output / f"{stem}.zip"
        subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", str(stage), str(archive)], check=True)
    else:
        archive = Path(shutil.make_archive(str(output / stem), "zip" if sys.platform == "win32" else "gztar",
                                           root_dir=output, base_dir="Blip"))
    checksums = output / "SHA256SUMS"
    checksums.write_text(f"{sha256(archive)}  {archive.name}\n", encoding="utf-8")
    print(f"Built {archive.name} ({archive.stat().st_size / 1024 / 1024:.1f} MiB)")
    print(f"SHA256: {sha256(archive)}")


if __name__ == "__main__":
    main()
