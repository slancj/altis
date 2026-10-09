"""Account vault: you hand over accounts, altlay files them. Alt + Relay."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
import time
from pathlib import Path

from altlay.pool import _load_json, _save_json, vault_home

BACKENDS = ("chatgpt", "claude", "deepseek")

# session markers per backend (cookie name or localStorage key)
MARKERS = {
    "chatgpt": "__Secure-next-auth.session-token.0",
    "claude": "sessionKey",
    "deepseek": "userToken",
}
DOMAINS = {
    "chatgpt": ("chatgpt.com", "openai.com"),
    "claude": ("claude.ai", "anthropic.com"),
}
_SAMESITE = {0: "None", 1: "Lax", 2: "Strict", 256: "Lax"}


# ---------------------------------------------------------------- vault CRUD
def load_vault() -> dict:
    return _load_json("accounts.json", {})


def save_vault(vault: dict) -> None:
    _save_json("accounts.json", vault)


def entries(backend: str) -> list[dict]:
    return load_vault().get(backend, [])


def get_entry(backend: str, name: str) -> dict:
    for e in entries(backend):
        if e["name"] == name:
            return e
    raise KeyError(f"no {backend} account {name!r}")


def add_entry(backend: str, entry: dict) -> None:
    vault = load_vault()
    pool = vault.setdefault(backend, [])
    if any(e["name"] == entry["name"] for e in pool):
        raise ValueError(f"{backend} account {entry['name']!r} already exists")
    pool.append(entry)
    save_vault(vault)


def remove_entry(backend: str, name: str) -> None:
    vault = load_vault()
    pool = vault.get(backend, [])
    vault[backend] = [e for e in pool if e["name"] != name]
    if len(vault[backend]) == len(pool):
        raise KeyError(f"no {backend} account {name!r}")
    save_vault(vault)


def rename_entry(backend: str, old: str, new: str) -> None:
    vault = load_vault()
    for e in vault.get(backend, []):
        if e["name"] == old:
            e["name"] = new
            save_vault(vault)
            return
    raise KeyError(f"no {backend} account {old!r}")


def update_session(backend: str, name: str, session: dict) -> None:
    """Replace stored session material (self-heal after manual re-login)."""
    import datetime
    vault = load_vault()
    for e in vault.get(backend, []):
        if e["name"] == name:
            e.update(session)
            e["added_at"] = datetime.datetime.now().isoformat(timespec="seconds")
            e["source"] = (e.get("source") or "") + " +relogin"
            save_vault(vault)
            return
    raise KeyError(f"no {backend} account {name!r}")


def suggest_alias(email: str | None, taken: list[str], fallback: str) -> str:
    if email and "@" in email:
        base = re.sub(r"[^a-zA-Z0-9_+-]+", "", email.split("@")[0]).lower() or fallback
    else:
        base = fallback
    name, i = base, 2
    while name in taken:
        name, i = f"{base}-{i}", i + 1
    return name


# ---------------------------------------------------------------- config.toml
def _config_path() -> Path:
    return vault_home() / "config.toml"


def load_config() -> dict:
    try:
        import tomllib
        with open(_config_path(), "rb") as f:
            return tomllib.load(f)
    except (OSError, ImportError, Exception):
        return {}


def get_default(backend: str) -> str | None:
    return load_config().get("default_account", {}).get(backend)


def _write_config(cfg: dict) -> None:
    home = vault_home()
    home.mkdir(parents=True, mode=0o700, exist_ok=True)
    lines = []
    if cfg.get("default_account"):
        lines.append("[default_account]")
        for k, v in cfg["default_account"].items():
            lines.append(f'{k} = "{v}"')
    if cfg.get("ignore", {}).get("emails"):
        lines.append("[ignore]")
        emails = ", ".join(f'"{e}"' for e in cfg["ignore"]["emails"])
        lines.append(f"emails = [{emails}]")
    p = _config_path()
    p.write_text("\n".join(lines) + "\n" if lines else "")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def set_default(backend: str, name: str) -> None:
    cfg = load_config()
    cfg.setdefault("default_account", {})[backend] = name
    _write_config(cfg)


def get_ignored() -> list[str]:
    return load_config().get("ignore", {}).get("emails", [])


def add_ignore(email: str) -> None:
    cfg = load_config()
    ignored = cfg.setdefault("ignore", {}).setdefault("emails", [])
    if email and email not in ignored:
        ignored.append(email)
    _write_config(cfg)


def resolve_name(backend: str, pinned: str | None = None) -> str | None:
    """Explicit flag > env > config default. None = pool order decides."""
    if pinned:
        return pinned
    env = os.environ.get("ALTALAY_ACCOUNT")
    if env:
        return env
    return get_default(backend)


# ------------------------------------------------------- firefox discovery
def profile_roots() -> list[Path]:
    home = Path.home()
    roots = []
    for base in (
        home / ".var/app/org.mozilla.firefox/config/mozilla/firefox",
        home / ".mozilla/firefox",
        home / ".librewolf",
        home / ".var/app/app.zen_browser.zen/.zen",
    ):
        if base.is_dir():
            roots.extend(d for d in base.iterdir() if d.is_dir())
    aph = home / ".config/aph/profile"
    if aph.is_dir():
        roots.append(aph)
    return roots


def _copy_db(path: Path) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="altlay-acct")) / path.name
    import shutil
    shutil.copy(path, tmp)
    wal = Path(str(path) + "-wal")
    if wal.exists():
        shutil.copy(wal, Path(str(tmp) + "-wal"))
    return tmp


def read_cookies_grouped(db_path: Path, domains: tuple[str, ...]) -> dict[str, list[dict]]:
    """cookies.sqlite -> {container_id: [playwright-format cookies]}.
    Container 'default' = no userContextId (normal windows)."""
    db = _copy_db(db_path)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT host,name,value,path,expiry,isSecure,isHttpOnly,sameSite,"
            " originAttributes FROM moz_cookies").fetchall()
    finally:
        con.close()
    grouped: dict[str, list[dict]] = {}
    for host, name, value, path, expiry, sec, ho, ss, origin in rows:
        domain = host.lstrip(".")
        if not any(domain == w or domain.endswith("." + w) for w in domains):
            continue
        m = re.search(r"userContextId=(\d+)", origin or "")
        container = m.group(1) if m else "default"
        grouped.setdefault(container, []).append({
            "name": name, "value": value, "domain": host, "path": path,
            "expires": (expiry / 1000.0) if expiry > 10_000_000_000 else float(expiry),
            "httpOnly": bool(ho), "secure": bool(sec),
            "sameSite": _SAMESITE.get(ss, "Lax")})
    return grouped


def snapshot_firefox() -> list[dict]:
    """Find every logged-in session in every profile+container.

    Returns [{backend, profile, container, cookies|userToken, device_id}]."""
    found = []
    for prof in profile_roots():
        cookies_db = prof / "cookies.sqlite"
        if cookies_db.exists():
            for backend in ("chatgpt", "claude"):
                try:
                    grouped = read_cookies_grouped(cookies_db, DOMAINS[backend])
                except Exception:
                    continue
                for container, cookies in grouped.items():
                    if any(c["name"] == MARKERS[backend] for c in cookies):
                        found.append({"backend": backend, "profile": str(prof),
                                      "container": container, "cookies": cookies})
        store = prof / "storage" / "default"
        if store.is_dir():
            for child in store.iterdir():
                if not child.name.startswith("https+++chat.deepseek.com"):
                    continue
                db = child / "ls" / "data.sqlite"
                if not db.exists():
                    continue
                try:
                    tmp = _copy_db(db)
                    con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
                    try:
                        row = con.execute(
                            "SELECT value FROM data WHERE key='userToken'").fetchone()
                        dev = con.execute(
                            "SELECT value FROM data WHERE key='deepseek-device-id:chat'").fetchone()
                    finally:
                        con.close()
                except Exception:
                    continue
                if not row:
                    continue
                v = row[0].decode() if isinstance(row[0], bytes) else row[0]
                try:
                    token = json.loads(v).get("value")
                except (json.JSONDecodeError, AttributeError):
                    token = None
                if token:
                    device = None
                    if dev:
                        dv = dev[0].decode() if isinstance(dev[0], bytes) else dev[0]
                        device = dv if isinstance(dv, str) else None
                    m = re.search(r"userContextId=(\d+)", child.name)
                    found.append({"backend": "deepseek", "profile": str(prof),
                                  "container": m.group(1) if m else "default",
                                  "userToken": token, "device_id": device})
    return found


# ------------------------------------------------------- identity probes
def _stdlib_get(url: str, headers: dict, timeout: float = 30.0):
    import urllib.request
    req = urllib.request.Request(url, headers=headers)
    return urllib.request.urlopen(req, timeout=timeout)


_UA_FF = ("Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0")


def identify_chatgpt(cookies: list[dict]) -> str | None:
    try:
        from curl_cffi import requests as cr
    except ImportError:
        return None
    jar = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
    try:
        r = cr.get("https://chatgpt.com/api/auth/session",
                   headers={"Cookie": jar, "Accept": "application/json"},
                   impersonate="chrome131", timeout=30)
        if r.status_code == 200:
            return r.json().get("user", {}).get("email")
    except Exception:
        pass
    return None


def identify_claude(cookies: list[dict]) -> str | None:
    jar = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
    try:
        r = _stdlib_get("https://claude.ai/api/organizations",
                        {"Cookie": jar, "Accept": "application/json",
                         "User-Agent": _UA_FF})
        orgs = json.load(r)
        if orgs:
            name = orgs[0].get("name", "")
            m = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", name)
            return m.group(0) if m else name
    except Exception:
        pass
    return None


def identify_deepseek(token: str) -> str | None:
    try:
        r = _stdlib_get("https://chat.deepseek.com/api/v0/users/current",
                        {"Authorization": f"Bearer {token}",
                         "Accept": "application/json", "User-Agent": _UA_FF})
        return json.load(r)["data"]["biz_data"].get("email")
    except Exception:
        pass
    return None


IDENTIFY = {
    "chatgpt": lambda s: identify_chatgpt(s["cookies"]),
    "claude": lambda s: identify_claude(s["cookies"]),
    "deepseek": lambda s: identify_deepseek(s["userToken"]),
}


def check_account(backend: str, entry: dict) -> tuple[bool, str]:
    """Lightweight validity probe (no generation)."""
    try:
        email = IDENTIFY[backend](entry)
    except Exception as e:
        return False, f"probe error: {e}"
    if email:
        return True, email
    return False, "session invalid or expired"


# ------------------------------------------------------- paste import
def parse_paste(secret: str) -> tuple[str, dict]:
    """Detect backend + session from pasted text. Returns (backend, session)."""
    s = secret.strip()
    if "__Secure-next-auth.session-token" in s:
        cookies = []
        for part in re.split(r";\s*", s):
            if "=" not in part:
                continue
            name, value = part.split("=", 1)
            name = name.strip()
            if "chatgpt" in name.lower() or "session-token" in name or "oai" in name \
                    or name in ("__cf_bm", "cf_clearance", "__cflb", "__oailb"):
                cookies.append({"name": name, "value": value, "domain": ".chatgpt.com",
                                "path": "/", "expires": time.time() + 90 * 86400,
                                "httpOnly": True, "secure": True, "sameSite": "Lax"})
        if not any(c["name"].startswith("__Secure-next-auth.session-token") for c in cookies):
            raise ValueError("no ChatGPT session-token chunk in paste")
        return "chatgpt", {"cookies": cookies}
    if "sessionKey" in s or "sk-ant-sid" in s:
        cookies = []
        for part in re.split(r";\s*", s):
            if "=" not in part:
                continue
            name, value = part.split("=", 1)
            cookies.append({"name": name.strip(), "value": value,
                            "domain": ".claude.ai", "path": "/",
                            "expires": time.time() + 90 * 86400,
                            "httpOnly": True, "secure": True, "sameSite": "Lax"})
        if not any(c["name"] == "sessionKey" for c in cookies):
            single = s.strip().strip('"')
            cookies = [{"name": "sessionKey", "value": single, "domain": ".claude.ai",
                        "path": "/", "expires": time.time() + 90 * 86400,
                        "httpOnly": True, "secure": True, "sameSite": "Lax"}]
        return "claude", {"cookies": cookies}
    token = s.strip().strip('"')
    if token.startswith("{"):
        try:
            token = json.loads(token).get("value", token)
        except json.JSONDecodeError:
            pass
    if len(token) >= 32:
        return "deepseek", {"userToken": token, "device_id": None}
    raise ValueError("unrecognized secret format")
