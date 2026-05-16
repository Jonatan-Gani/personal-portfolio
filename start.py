#!/usr/bin/env python3
"""One-command setup + launch for Portfolio Manager.

    python start.py              set up anything missing, then run the web app
    python start.py --ibkr       also install the Interactive Brokers extra
    python start.py --reinstall  force a dependency reinstall
    python start.py --no-venv    install into the current Python instead of a
                                 .venv (use this if your environment blocks
                                 running executables from .venv/Scripts)

Safe to re-run: every step is skipped when it is already satisfied.
Works the same on Linux, macOS and Windows.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"


def _venv_python() -> Path:
    sub = ("Scripts", "python.exe") if os.name == "nt" else ("bin", "python")
    return VENV.joinpath(*sub)


def _run(cmd: list[str]) -> None:
    print("·", " ".join(cmd))
    subprocess.check_call(cmd, cwd=ROOT)


def _ensure_venv() -> None:
    if _venv_python().exists():
        return
    print("Creating virtual environment in .venv ...")
    venv.EnvBuilder(with_pip=True).create(VENV)


def _deps_ok(python: str) -> bool:
    return subprocess.call(
        [python, "-c", "import portfolio_manager"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=ROOT,
    ) == 0


def _ensure_deps(python: str, *, reinstall: bool, ibkr: bool, user: bool) -> None:
    if _deps_ok(python) and not reinstall:
        print("Dependencies already installed.")
        return
    spec = ".[ibkr]" if ibkr else "."
    cmd = [python, "-m", "pip", "install", "-e", spec]
    if user:
        cmd.insert(4, "--user")
    print(f"Installing dependencies ({spec}) ...")
    _run(cmd)


def _ensure_file(target: str, example: str) -> None:
    tgt, ex = ROOT / target, ROOT / example
    if tgt.exists():
        return
    if not ex.exists():
        print(f"warning: {example} missing — skipping {target}")
        return
    print(f"Creating {target} from {example}")
    shutil.copyfile(ex, tgt)


def main() -> int:
    args = set(sys.argv[1:])
    unknown = args - {"--ibkr", "--reinstall", "--no-venv"}
    if unknown:
        print(f"unknown option(s): {', '.join(sorted(unknown))}")
        print(__doc__)
        return 2

    use_venv = "--no-venv" not in args
    if use_venv:
        _ensure_venv()
        python = str(_venv_python())
    else:
        python = sys.executable

    _ensure_deps(
        python,
        reinstall="--reinstall" in args,
        ibkr="--ibkr" in args,
        user=not use_venv,
    )
    _ensure_file("config/config.yaml", "config/config.example.yaml")
    _ensure_file(".env", ".env.example")
    _run([python, "-m", "portfolio_manager.cli", "init-db"])

    print("\nStarting web app at http://localhost:8000  —  Ctrl+C to stop\n")
    with contextlib.suppress(KeyboardInterrupt):
        subprocess.call([python, "-m", "portfolio_manager.cli", "web"], cwd=ROOT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
