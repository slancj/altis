"""Account pool with persistent cooldown failover. Alt + Relay."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

COOLDOWN_FALLBACK = 3600.0  # 1h when a limit error carries no reset time

LEGACY_HOME = Path.home() / ".config" / "altlay"
OLD_LOCAL_HOME = Path.cwd() / ".altlay"


def env(name: str) -> str | None:
    """ALTIS_* with ALTALAY_* fallback (pre-rebrand)."""
    return os.environ.get(f"ALTIS_{name}") or os.environ.get(f"ALTALAY_{name}")


def vault_home() -> Path:
    """Vault root: $ALTIS_HOME, else ./.altis in the working directory
    (in-repo so accounts travel with the project; git-ignored)."""
    custom = env("HOME")
    if custom:
        return Path(custom)
    home = Path.cwd() / ".altis"
    _migrate_legacy(home)
    return home


def _migrate_legacy(home: Path) -> None:
    """One-time move from the old locations (~/.config/altlay, ./.altlay)."""
    if home.exists():
        return
    for old in (OLD_LOCAL_HOME, LEGACY_HOME):
        if old.exists():
            try:
                import shutil
                shutil.move(str(old), str(home))
                print(f"[altis] moved vault {old} -> {home}", flush=True)
            except OSError as e:
                print(f"[altis] vault migration failed: {e}", flush=True)
            return


def _json_path(name: str) -> Path:
    return vault_home() / name


def _load_json(name: str, default):
    try:
        return json.loads(_json_path(name).read_text())
    except (OSError, json.JSONDecodeError):
        return default


def _save_json(name: str, data) -> None:
    home = vault_home()
    home.mkdir(parents=True, mode=0o700, exist_ok=True)
    p = home / name
    p.write_text(json.dumps(data, indent=1))
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def load_state() -> dict:
    return _load_json("state.json", {})


def save_state(state: dict) -> None:
    _save_json("state.json", state)


class RateLimited(Exception):
    """An account hit a usage limit. reset_at is epoch seconds or None."""

    def __init__(self, reset_at: float | None = None, detail: str = "") -> None:
        super().__init__(detail or "rate limited")
        self.reset_at = reset_at


class AccountDead(Exception):
    """An account's session is invalid (logged out / revoked)."""


class AllLimited(Exception):
    def __init__(self, status: dict) -> None:
        self.status = status
        lines = ["all accounts limited:"]
        for alias, info in status.items():
            lines.append(f"  {alias}: {info}")
        super().__init__("\n".join(lines))


def parse_reset_time(text: str, now: float | None = None) -> float | None:
    """Parse 'until 4:00 PM' / 'until 16:05' style notices into epoch seconds."""
    now = time.time() if now is None else now
    m = re.search(r"until\s+(\d{1,2}):(\d{2})\s*([AaPp][Mm])?", text)
    if not m:
        return None
    hour, minute, meridiem = int(m.group(1)), int(m.group(2)), (m.group(3) or "").upper()
    if meridiem == "PM" and hour < 12:
        hour += 12
    if meridiem == "AM" and hour == 12:
        hour = 0
    day = time.localtime(now)
    candidate = time.mktime((day.tm_year, day.tm_mon, day.tm_mday,
                             hour, minute, 0, 0, 0, day.tm_isdst))
    if candidate <= now:
        candidate += 86400  # tomorrow
    return candidate


class AccountPool:
    """Fixed-order pool: first healthy account answers, failover on limits."""

    def __init__(self, backend: str) -> None:
        self.backend = backend
        self._accounts = _load_json("accounts.json", {}).get(backend, [])
        self._state = load_state()

    def _key(self, name: str) -> str:
        return f"{self.backend}/{name}"

    def names(self) -> list[str]:
        return [a["name"] for a in self._accounts]

    def get(self, name: str) -> dict:
        for a in self._accounts:
            if a["name"] == name:
                return a
        raise KeyError(f"no {self.backend} account {name!r}")

    def healthy(self, now: float | None = None) -> list[dict]:
        now = time.time() if now is None else now
        return [a for a in self._accounts
                if self._state.get(self._key(a["name"]), {}).get("cooldown_until", 0) <= now]

    def mark_used(self, name: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        st = load_state()
        entry = st.setdefault(self._key(name), {})
        entry["last_used"] = now
        entry["failures"] = 0
        save_state(st)
        self._state = st

    def mark_limited(self, name: str, reset_at: float | None = None,
                     now: float | None = None) -> float:
        now = time.time() if now is None else now
        until = reset_at if reset_at and reset_at > now else now + COOLDOWN_FALLBACK
        st = load_state()
        entry = st.setdefault(self._key(name), {})
        entry["cooldown_until"] = until
        entry["failures"] = entry.get("failures", 0) + 1
        save_state(st)
        self._state = st
        return until

    def mark_dead(self, name: str, now: float | None = None) -> None:
        self.mark_limited(name, (time.time() if now is None else now) + 86400, now)

    def run(self, attempt, order: list[str] | None = None,
            now: float | None = None):
        """Call attempt(name, entry); fail over on limits. Fixed pool order."""
        now = time.time() if now is None else now
        wanted = order or self.names()
        tried: dict = {}
        for name in wanted:
            try:
                entry = self.get(name)
            except KeyError:
                continue
            if self._state.get(self._key(name), {}).get("cooldown_until", 0) > now:
                tried[name] = "on cooldown"
                continue
            try:
                result = attempt(name, entry)
            except RateLimited as e:
                until = self.mark_limited(name, e.reset_at, now)
                tried[name] = f"limited until {time.strftime('%H:%M', time.localtime(until))}"
                continue
            except AccountDead:
                self.mark_dead(name, now)
                tried[name] = "session dead"
                continue
            self.mark_used(name, now)
            return result
        raise AllLimited(tried or {"pool": "no accounts configured"})
