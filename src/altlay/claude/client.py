"""Claude web-API client (direct HTTPS, no browser needed)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid

from altlay.chatgpt.cookies import cookie_header, export_cookies

BASE = "https://claude.ai"
_DOMAINS = ("claude.ai", "anthropic.com")
_UA = ("Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0")


class Claude:
    def __init__(self, firefox_profile: str | os.PathLike | None = None,
                 model: str | None = None, timeout: float = 120.0,
                 transport: str = "direct",
                 profile_dir: str | os.PathLike | None = None,
                 headless: bool = False) -> None:
        self.firefox_profile = firefox_profile or os.environ.get("ALTALAY_FIREFOX_PROFILE")
        self.model = model
        self.timeout = timeout
        self.transport = transport
        self.profile_dir = profile_dir
        self.headless = headless
        self._cookie: str | None = None
        self._org: str | None = None

    def _req(self, method: str, path: str, payload: dict | None = None) -> urllib.request.addinfourl:
        if self._cookie is None:
            cookies = export_cookies(self.firefox_profile, domains=_DOMAINS,
                                     session_marker="sessionKey")
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
            body = e.read().decode(errors="replace")[:500]
            raise RuntimeError(f"claude.ai {method} {path} -> {e.code}: {body}") from e

    def generate(self, prompt: str) -> str:
        if self.transport == "browser":
            import asyncio

            from altlay.claude.browser import generate_browser

            return asyncio.run(generate_browser(
                prompt, profile_dir=self.profile_dir,
                firefox_profile=self.firefox_profile, timeout=self.timeout,
                headless=self.headless))
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
                if isinstance(chunk, dict) and chunk.get("completion"):
                    parts.append(chunk["completion"])
            return "".join(parts).strip()
        finally:
            try:
                self._req("DELETE",
                          f"/api/organizations/{org}/chat_conversations/{convo_id}")
            except RuntimeError:
                pass

    def _org_path(self) -> str:
        if self._org is None:  # trigger lazy auth
            self._req("GET", "/api/organizations")
        assert self._org is not None, "not logged in: no Claude organization found"
        return self._org


def _last_active_org(cookies: list[dict]) -> str | None:
    for c in cookies:
        if c["name"] == "lastActiveOrg":
            return c["value"]
    return None


def generate(prompt: str, transport: str = "direct", **kwargs) -> str:
    return Claude(transport=transport, **kwargs).generate(prompt)
