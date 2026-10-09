"""Preload system C++ runtime before playwright/greenlet import (Nix hosts)."""

import ctypes
import glob
import shutil
import subprocess


def ensure_native_libs() -> None:
    try:
        import greenlet  # noqa: F401
        return
    except (ImportError, OSError):
        pass
    for candidate in _find_libstdcpp():
        try:
            ctypes.CDLL(candidate, mode=ctypes.RTLD_GLOBAL)
            return
        except OSError:
            continue


def _find_libstdcpp() -> list[str]:
    found: list[str] = []
    import os
    nix_ld = os.environ.get("NIX_LD_LIBRARY_PATH")
    if nix_ld:
        for d in nix_ld.split(":"):
            found.extend(glob.glob(f"{d}/libstdc++.so.6"))
    for pattern in (
        "/nix/store/*-gcc-*-lib/lib/libstdc++.so.6",
        "/run/current-system/sw/share/nix-ld/lib/libstdc++.so.6",
        "/usr/lib/x86_64-linux-gnu/libstdc++.so.6",
        "/usr/lib/libstdc++.so.6",
    ):
        found.extend(glob.glob(pattern))
    cc = shutil.which("cc") or shutil.which("gcc")
    if cc:
        try:
            p = subprocess.run([cc, "-print-file-name=libstdc++.so.6"],
                               capture_output=True, text=True, timeout=10)
            if p.stdout.strip().endswith(".so"):
                found.append(p.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            pass
    return found
