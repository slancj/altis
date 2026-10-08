"""Find DeepSeek's localStorage token across Firefox-family profiles."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path


def _profile_roots() -> list[Path]:
    home = Path.home()
    roots = [
        home / ".var/app/org.mozilla.firefox/config/mozilla/firefox",
        home / ".config/aph",
        home / ".mozilla/firefox",
        home / ".librewolf",
        home / ".var/app/app.zen_browser.zen/.zen",
    ]
    out = []
    for base in roots:
        if not base.is_dir():
            continue
        for prof in base.iterdir():
            if prof.is_dir():
                out.append(prof)
    # aph uses a bare profile dir
    aph = home / ".config/aph/profile"
    if aph.is_dir():
        out.append(aph)
    return out


def _ls_candidates() -> list[Path]:
    cands = []
    for prof in _profile_roots():
        store = prof / "storage" / "default"
        if not store.is_dir():
            continue
        for child in store.iterdir():
            if child.name.startswith("https+++chat.deepseek.com"):
                db = child / "ls" / "data.sqlite"
                if db.exists():
                    cands.append(db)
    return cands


def _read_value(db: Path, key: str):
    tmp = Path(tempfile.mkdtemp(prefix="altlay-ds")) / "data.sqlite"
    shutil.copy(db, tmp)
    con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
    try:
        row = con.execute("SELECT value FROM data WHERE key=?", (key,)).fetchone()
    finally:
        con.close()
    if not row:
        return None
    v = row[0].decode() if isinstance(row[0], bytes) else row[0]
    try:
        return json.loads(v)
    except (json.JSONDecodeError, TypeError):
        return v


def find_session(explicit_profile: str | os.PathLike | None = None) -> dict:
    """Return {userToken, device_id, profile} — most-recent valid token wins."""
    if explicit_profile is not None:
        dbs = [Path(explicit_profile) / "storage" / "default"]
        cands: list[Path] = []
        for base in dbs:
            if base.is_dir():
                for child in base.iterdir():
                    if child.name.startswith("https+++chat.deepseek.com"):
                        db = child / "ls" / "data.sqlite"
                        if db.exists():
                            cands.append(db)
        # also accept a direct data.sqlite path
        if Path(explicit_profile).name == "data.sqlite":
            cands = [Path(explicit_profile)]
    else:
        cands = _ls_candidates()
    best: dict | None = None
    best_mtime = -1.0
    for db in cands:
        tok = _read_value(db, "userToken")
        token = (tok or {}).get("value") if isinstance(tok, dict) else None
        if not token:
            continue
        try:
            mtime = db.stat().st_mtime
        except OSError:
            mtime = 0.0
        if mtime > best_mtime:
            dev = _read_value(db, "deepseek-device-id:chat")
            best = {"userToken": token,
                    "device_id": dev if isinstance(dev, str) else None,
                    "profile": str(db)}
            best_mtime = mtime
    if best is None:
        raise RuntimeError("no logged-in DeepSeek session found in browser profiles")
    return best
