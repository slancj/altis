"""Claude web-API client (direct HTTPS, no browser needed)."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
import uuid

from altis.chatgpt.cookies import cookie_header, export_cookies
from altis.pool import env as _getenv

BASE = "https://claude.ai"
_DOMAINS = ("claude.ai", "anthropic.com")
_UA = ("Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0")


class Claude:
    def __init__(self, firefox_profile: str | os.PathLike | None = None,
                 model: str | None = None, timeout: float = 120.0,
                 transport: str = "direct",
                 profile_dir: str | os.PathLike | None = None,
                 headless: bool = False, stream: bool = False,
                 session: dict | None = None) -> None:
        self.firefox_profile = firefox_profile or _getenv("FIREFOX_PROFILE")
        self.model = model
        self.timeout = timeout
        self.transport = transport
        self.profile_dir = profile_dir
        self.headless = headless
        self.stream = stream
        self.session = session
        self._cookie: str | None = None
        self._org: str | None = None

    def _cookies(self) -> list[dict]:
        if self.session is not None:
            return self.session["cookies"]
        return export_cookies(self.firefox_profile, domains=_DOMAINS,
                              session_marker="sessionKey")

    def _req(self, method: str, path: str, payload: dict | None = None) -> urllib.request.addinfourl:
        if self._cookie is None:
            cookies = self._cookies()
            self._cookie = cookie_header(cookies)
            orgs = json.load(self._req("GET", "/api/organizations"))
            self._org = next(o["uuid"] for o in orgs
                             if o["uuid"] == _last_active_org(cookies)) \
                if _last_active_org(cookies) else orgs[0]["uuid"]
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            BASE + path, data=data, method=method,
            headers={"Cookie": self._cookie, "Accept": "application/json",
                     "Content-Type": "application/json", "User-Agent": _UA,
                     "Origin": BASE, "Referer": BASE + "/"})
        try:
            return urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            raise _as_pool_error(e.code, body, method, path) from e

    def generate(self, prompt: str) -> str:
        if self.transport == "browser":
            import asyncio

            from altis.claude.browser import generate_browser

            return asyncio.run(generate_browser(
                prompt, profile_dir=self.profile_dir,
                firefox_profile=self.firefox_profile, timeout=self.timeout,
                headless=self.headless, stream=self.stream,
                session={"cookies": self._cookies()}))
        org = self._org_path()
        convo = {"uuid": str(uuid.uuid4()), "name": ""}
        if self.model:
            convo["model"] = self.model
        created = json.load(self._req(
            "POST", f"/api/organizations/{org}/chat_conversations", convo))
        convo_id = created["uuid"]
        try:
            resp = self._req(
                "POST", f"/api/organizations/{org}/chat_conversations/{convo_id}/completion",
                {"prompt": prompt, "timezone": "America/New_York",
                 "attachments": [], "files": [], "sync_sources": []})
            parts: list[str] = []
            data_prefix = "data: "
            for raw in resp:
                line = raw.decode(errors="replace").strip()
                if not line.startswith(data_prefix):
                    continue
                try:
                    chunk = json.loads(line[len(data_prefix):])
                except json.JSONDecodeError:
                    continue
                if isinstance(chunk, dict):
                    _raise_if_over_limit(chunk)
                    if chunk.get("completion"):
                        parts.append(chunk["completion"])
            return "".join(parts).strip()
        finally:
            try:
                self._req("DELETE",
                          f"/api/organizations/{org}/chat_conversations/{convo_id}")
            except Exception:
                pass

    def _org_path(self) -> str:
        if self._org is None:  # trigger lazy auth
            self._req("GET", "/api/organizations")
        assert self._org is not None, "not logged in: no Claude organization found"
        return self._org


def _raise_if_over_limit(chunk: dict) -> None:
    """200-OK payloads can still report an exhausted quota."""
    from altis.pool import RateLimited, parse_reset_time
    ml = chunk.get("messageLimit") or {}
    status = str(ml.get("type", "") or "")
    if status and status != "within_limit":
        reset = None
        try:
            reset = float(ml.get("resetsAt") or 0) or None
        except (TypeError, ValueError):
            reset = None
        if reset is None:
            reset = parse_reset_time(json.dumps(chunk))
        raise RateLimited(reset, f"claude quota: {status}")
    if chunk.get("error", {}).get("type") == "over_limit":
        raise RateLimited(parse_reset_time(json.dumps(chunk)), "claude over_limit")


def _last_active_org(cookies: list[dict]) -> str | None:
    for c in cookies:
        if c["name"] == "lastActiveOrg":
            return c["value"]
    return None


def _as_pool_error(code: int, body: str, method: str, path: str) -> Exception:
    from altis.pool import AccountDead, RateLimited, parse_reset_time
    lowered = body.lower()
    if code in (401, 403) and ("unauthorized" in lowered or "invalid" in lowered
                               or "session" in lowered or "login" in lowered):
        return AccountDead(f"claude.ai {method} {path} -> {code}")
    if code == 429 or "rate" in lowered and "limit" in lowered or "over_limit" in lowered:
        reset = parse_reset_time(body)
        if reset is None:
            m = re.search(r"resets?_?at['\"]?\s*[:=]\s*(\d{10})", body)
            if m:
                reset = float(m.group(1))
        return RateLimited(reset, body[:300])
    return RuntimeError(f"claude.ai {method} {path} -> {code}: {body[:500]}")


def generate(prompt: str, transport: str = "direct", **kwargs) -> str:
    return Claude(transport=transport, **kwargs).generate(prompt)
