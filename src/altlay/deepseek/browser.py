"""DeepSeek in a stealth browser (headed by default)."""

from __future__ import annotations

import asyncio
import json as _json
import os
import re
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from altlay.chatgpt import native

native.ensure_native_libs()

from cloakbrowser import launch_persistent_context_async  # noqa: E402

from altlay.deepseek.storage import find_session  # noqa: E402
from altlay.pool import RateLimited, parse_reset_time  # noqa: E402
from altlay.ui import (  # noqa: E402
    Streamer,
    arm_overlay_handlers,
    disarm_overlay_handlers,
    enter_prompt,
    goto_with_retries,
)

COMPOSER = "textarea"
LIMIT_RE = re.compile(
    r"(reached|hit|exceeded).{0,60}(limit|quota)|usage limit|try again (later|at|until)",
    re.IGNORECASE)
ANSWER_JS = """() => {
  const sel = ['[data-testid*="assistant"]', '.ds-message--assistant',
               '[class*="assistant"]', '.message-assistant'];
  for (const s of sel) {
    const els = [...document.querySelectorAll(s)];
    if (els.length) return els.map(e => e.innerText);
  }
  return null;
}"""


def default_profile_dir() -> Path:
    env = os.environ.get("ALTALAY_PROFILE")
    if env:
        return Path(env)
    from altlay.pool import vault_home
    return vault_home() / "profile"


def _session_token(firefox_profile=None, session: dict | None = None) -> str:
    if session is not None:
        return session["userToken"]
    return find_session(firefox_profile)["userToken"]


def _raw_user_token(firefox_profile=None, session: dict | None = None) -> str:
    return _json.dumps({"value": _session_token(firefox_profile, session),
                        "__version": "0"})


async def _raise_if_limited(page, texts: list[str] | None) -> None:
    cur = (texts[-1].strip() if texts else "")
    if cur and not (len(cur) < 300 and LIMIT_RE.search(cur)):
        return
    try:
        if await page.get_by_text(LIMIT_RE).count() == 0:
            return
        body = await page.locator("body").inner_text()
    except Exception:
        return
    m = LIMIT_RE.search(body or "")
    if m:
        raise RateLimited(parse_reset_time(body),
                          (body[m.start():m.start() + 160]).strip())


def _delete_conversation(token: str, convo_id: str) -> None:
    """Best-effort history cleanup (plain Bearer DELETE, no PoW needed)."""
    try:
        req = urllib.request.Request(
            f"https://chat.deepseek.com/api/v0/chat_session/{convo_id}",
            method="DELETE",
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/json",
                     "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64; rv:140.0) "
                                    "Gecko/20100101 Firefox/140.0")})
        urllib.request.urlopen(req, timeout=30)
    except Exception:
        pass


async def generate_browser(prompt: str,
                           profile_dir: str | os.PathLike | None = None,
                           firefox_profile: str | os.PathLike | None = None,
                           viewport: dict | None = None,
                           timeout: float = 300.0,
                           headless: bool = False,
                           stream: bool = False,
                           session: dict | None = None) -> str:
    out = Streamer(stream)
    profile = Path(profile_dir) if profile_dir else default_profile_dir()
    token = _session_token(firefox_profile, session)
    raw_token = _raw_user_token(firefox_profile, session)
    ctx = await launch_persistent_context_async(
        profile, headless=headless, humanize=True,
        viewport=viewport or {"width": 1280, "height": 900})
    try:
        await ctx.add_init_script(
            f"localStorage.setItem('userToken', {raw_token!r});")
        page = await ctx.new_page()
        await arm_overlay_handlers(page)
        await goto_with_retries(page, "https://chat.deepseek.com/")
        try:
            await page.locator(COMPOSER).wait_for(state="visible", timeout=90_000)
        except Exception:
            raise RuntimeError(
                "not logged in: no DeepSeek composer found. "
                "Log into DeepSeek in Firefox, then retry.")
        await disarm_overlay_handlers(page)
        await enter_prompt(page, COMPOSER, prompt, instant=headless)
        await asyncio.sleep(0.3)
        await page.keyboard.press("Enter")

        deadline = time.time() + timeout
        last_text, stable_since = "", time.time()
        while time.time() < deadline:
            await asyncio.sleep(1)
            texts = await page.evaluate(ANSWER_JS)
            cur = (texts[-1].strip() if texts else "")
            await _raise_if_limited(page, texts)
            if cur != last_text:
                last_text, stable_since = cur, time.time()
            out.update(cur)
            if cur and time.time() - stable_since > 2.5:
                try:
                    convo = urlparse(page.url).path.rstrip("/").rsplit("/", 1)[-1]
                    if convo not in ("", "chat.deepseek.com"):
                        _delete_conversation(token, convo)
                except Exception:
                    pass
                out.finish(last_text)
                return cur
        raise TimeoutError("generation did not finish in time")
    finally:
        await ctx.close()
