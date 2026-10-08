"""DeepSeek web-API client (direct HTTPS + PoW solver)."""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request

from altlay.deepseek.pow import solve_challenge
from altlay.deepseek.storage import find_session

BASE = "https://chat.deepseek.com"
TARGET = "/api/v0/chat/completion"
_UA = ("Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0")


class DeepSeek:
    def __init__(self, firefox_profile: str | os.PathLike | None = None,
                 model: str | None = None, timeout: float = 180.0,
                 transport: str = "direct",
                 profile_dir: str | os.PathLike | None = None,
                 thinking: bool = False, search: bool = False) -> None:
        self.firefox_profile = firefox_profile or os.environ.get("ALTALAY_FIREFOX_PROFILE")
        self.model = model
        self.timeout = timeout
        self.transport = transport
        self.profile_dir = profile_dir
        self.thinking = thinking
        self.search = search
        self._session: dict | None = None

    def _headers(self) -> dict:
        if self._session is None:
            self._session = find_session(self.firefox_profile)
        h = {"Authorization": f"Bearer {self._session['userToken']}",
             "Accept": "application/json", "Content-Type": "application/json",
             "User-Agent": _UA, "Origin": BASE, "Referer": BASE + "/"}
        if self._session.get("device_id"):
            h["X-Client-Device-Id"] = self._session["device_id"]
        return h

    def _call(self, method: str, path: str, payload: dict | None = None,
              extra: dict | None = None):
        data = json.dumps(payload).encode() if payload is not None else None
        headers = self._headers()
        if extra:
            headers.update(extra)
        req = urllib.request.Request(BASE + path, data=data, method=method,
                                     headers=headers)
        try:
            return urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:500]
            raise RuntimeError(f"chat.deepseek.com {method} {path} -> "
                               f"{e.code}: {body}") from e

    def _pow_header(self) -> str:
        resp = self._call("POST", "/api/v0/chat/create_pow_challenge",
                          {"target_path": TARGET})
        biz = json.load(resp)["data"]["biz_data"]
        challenge = biz["challenge"]
        answer = solve_challenge(challenge)
        return base64.b64encode(json.dumps({
            "algorithm": challenge.get("algorithm", "DeepSeekHashV1"),
            "challenge": challenge["challenge"], "salt": challenge["salt"],
            "answer": answer, "signature": challenge["signature"],
            "target_path": challenge.get("target_path", TARGET),
        }).encode()).decode()

    def generate(self, prompt: str) -> str:
        if self.transport == "browser":
            import asyncio

            from altlay.deepseek.browser import generate_browser

            return asyncio.run(generate_browser(
                prompt, profile_dir=self.profile_dir,
                firefox_profile=self.firefox_profile, timeout=self.timeout))
        created = json.load(self._call("POST", "/api/v0/chat_session/create", {}))
        session_id = created["data"]["biz_data"]["id"]
        try:
            pow_header = self._pow_header()
            body: dict = {"chat_session_id": session_id,
                          "parent_message_id": None, "prompt": prompt,
                          "ref_file_ids": [],
                          "thinking_enabled": self.thinking,
                          "search_enabled": self.search}
            if self.model:
                body["model"] = self.model
            resp = self._call("POST", TARGET, body,
                              {"X-Ds-Pow-Response": pow_header})
            return _collect_sse(resp)
        finally:
            try:
                self._call("DELETE", f"/api/v0/chat_session/{session_id}")
            except RuntimeError:
                pass


def _collect_sse(resp) -> str:
    """DeepSeek streams JSON-patch ops: content arrives as
    {"p": "response/fragments/-1/content", "o": "APPEND", "v": "<text>"}."""
    parts: list[str] = []
    snapshot: list[str] = []
    in_content = False
    for raw in resp:
        line = raw.decode(errors="replace").strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if not isinstance(chunk, dict):
            continue
        if "o" in chunk:  # explicit op ends any bare-value run
            in_content = False
            if (chunk.get("o") == "APPEND"
                    and isinstance(chunk.get("p"), str)
                    and chunk["p"].endswith("/content")
                    and isinstance(chunk.get("v"), str)):
                parts.append(chunk["v"])
                in_content = True
        elif in_content and set(chunk) == {"v"} and isinstance(chunk["v"], str):
            parts.append(chunk["v"])
        else:
            # full-state snapshot (short answers arrive inline, no APPENDs)
            try:
                for frag in chunk["v"]["response"]["fragments"]:
                    if isinstance(frag.get("content"), str) and frag["content"]:
                        snapshot.append(frag["content"])
            except (KeyError, TypeError):
                pass
    text = "".join(parts) if parts else "".join(snapshot)
    return text.strip()


def generate(prompt: str, transport: str = "direct", **kwargs) -> str:
    return DeepSeek(transport=transport, **kwargs).generate(prompt)
