"""Export ChatGPT session cookies from a Firefox profile into Playwright form."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

# Firefox moz_cookies.sameSite -> Playwright sameSite
_SAMESITE = {0: "None", 1: "Lax", 2: "Strict", 256: "Lax"}

_WANTED = ("chatgpt.com", "openai.com")


def find_firefox_profile() -> Path | None:
    """Locate the most recently used Firefox profile with ChatGPT cookies."""
    candidates: list[Path] = []
    home = Path.home()
    for base in (
        home / ".var/app/org.mozilla.firefox/config/mozilla/firefox",
        home / ".mozilla/firefox",
        home / ".config/aph/profile",
        home / ".librewolf",
    ):
        if base.is_file() and base.name == "profile":  # direct profile dir
            candidates.append(base)
            continue
        if not base.is_dir():
            continue
        for child in base.iterdir():
            if child.is_dir() and (child / "cookies.sqlite").exists():
                candidates.append(child)
    best: Path | None = None
    best_mtime = -1.0
    for prof in candidates:
        try:
            mtime = (prof / "cookies.sqlite-wal").stat().st_mtime
        except OSError:
            try:
                mtime = (prof / "cookies.sqlite").stat().st_mtime
            except OSError:
                continue
        if mtime > best_mtime:
            best, best_mtime = prof, mtime
    return best


def export_cookies(profile: str | os.PathLike | None = None) -> list[dict]:
    """Read Firefox cookies.sqlite (via a temp copy, lock-safe) and return
    a list of Playwright-format cookie dicts for ChatGPT/OpenAI domains."""
    if profile is None:
        found = find_firefox_profile()
        if found is None:
            raise RuntimeError("no Firefox profile with cookies.sqlite found")
        profile = found
    profile = Path(profile)
    db = profile / "cookies.sqlite" if (profile / "cookies.sqlite").exists() else profile
    tmpdir = Path(tempfile.mkdtemp(prefix="altlay-ck"))
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(db) + suffix)
        if src.exists():
            shutil.copy(src, tmpdir / ("cookies.sqlite" + suffix))
    con = sqlite3.connect(f"file:{tmpdir / 'cookies.sqlite'}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT host,name,value,path,expiry,isSecure,isHttpOnly,sameSite"
            " FROM moz_cookies"
        ).fetchall()
    finally:
        con.close()
    out = []
    for host, name, value, path, expiry, secure, httponly, samesite in rows:
        domain = host.lstrip(".")
        if not any(domain == w or domain.endswith("." + w) for w in _WANTED):
            continue
        out.append({
            "name": name, "value": value, "domain": host, "path": path,
            # newer Firefox stores ms; Playwright wants seconds
            "expires": (expiry / 1000.0) if expiry > 10_000_000_000 else float(expiry),
            "httpOnly": bool(httponly), "secure": bool(secure),
            "sameSite": _SAMESITE.get(samesite, "Lax"),
        })
    if not any(c["name"] == "__Secure-next-auth.session-token.0" for c in out):
        raise RuntimeError(f"no ChatGPT session cookie in {profile}")
    return out


def main() -> None:  # `uv run altlay-cookies` debug helper
    cookies = export_cookies(os.environ.get("ALTALAY_FIREFOX_PROFILE"))
    print(json.dumps({"count": len(cookies),
                      "names": sorted({c["name"] for c in cookies})}, indent=1))
