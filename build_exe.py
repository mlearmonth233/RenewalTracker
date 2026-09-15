"""Build a standalone RenewalTracker executable with PyInstaller.

    pip install -r requirements.txt -r requirements-build.txt
    python build_exe.py

Output: dist/RenewalTracker (macOS/Linux) or dist/RenewalTracker.exe (Windows).
Build on the operating system you want to ship to; PyInstaller does not
cross-compile.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> int:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller is not installed. Run: pip install -r requirements-build.txt", file=sys.stderr)
        return 1

    for folder in ("build", "dist"):
        shutil.rmtree(ROOT / folder, ignore_errors=True)

    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(ROOT / "renewaltracker.spec")]
    print("+", " ".join(cmd), flush=True)
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        return result.returncode

    exe = ROOT / "dist" / ("RenewalTracker.exe" if sys.platform == "win32" else "RenewalTracker")
    if not exe.exists():
        print("Build finished but the executable was not found.", file=sys.stderr)
        return 1

    size_mb = exe.stat().st_size / 1_000_000
    print(f"\nBuilt {exe} ({size_mb:.1f} MB) for {platform.system()} {platform.machine()}.")
    print("Run it directly; it opens the app in your browser and stores data in your user profile.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
