"""DeepSeek in a visible stealth browser."""

from __future__ import annotations

import asyncio
import json as _json
import os
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from altlay.chatgpt import native

native.ensure_native_libs()

from cloakbrowser import launch_persistent_context_async  # noqa: E402

from altlay.deepseek.storage import find_session  # noqa: E402

COMPOSER = "textarea"
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
    return Path.home() / ".config" / "altlay" / "profile"


def _raw_user_token(firefox_profile=None) -> str:
    session = find_session(firefox_profile)
    return _json.dumps({"value": session["userToken"], "__version": "0"})


def _delete_conversation(firefox_profile, convo_id: str) -> None:
    """Best-effort history cleanup (plain Bearer DELETE, no PoW needed)."""
    try:
        session = find_session(firefox_profile)
        req = urllib.request.Request(
            f"https://chat.deepseek.com/api/v0/chat_session/{convo_id}",
            method="DELETE",
            headers={"Authorization": f"Bearer {session['userToken']}",
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
                           headless: bool = False) -> str:
    profile = Path(profile_dir) if profile_dir else default_profile_dir()
    raw_token = _raw_user_token(firefox_profile)
    ctx = await launch_persistent_context_async(
        profile, headless=headless, humanize=True,
        viewport=viewport or {"width": 1280, "height": 900})
    try:
        await ctx.add_init_script(
            f"localStorage.setItem('userToken', {raw_token!r});")
        page = await ctx.new_page()
        await page.goto("https://chat.deepseek.com/", wait_until="domcontentloaded")
        try:
            await page.locator(COMPOSER).wait_for(state="visible", timeout=90_000)
        except Exception:
            raise RuntimeError(
                "not logged in: no DeepSeek composer found. "
                "Log into DeepSeek in Firefox, then retry.")
        composer = page.locator(COMPOSER)
        await composer.click()
        await composer.fill(prompt)
        await asyncio.sleep(1)
        await page.keyboard.press("Enter")

        deadline = time.time() + timeout
        last_text, stable_since = "", time.time()
        while time.time() < deadline:
            await asyncio.sleep(2)
            texts = await page.evaluate(ANSWER_JS)
            cur = (texts[-1].strip() if texts else "")
            if cur != last_text:
                last_text, stable_since = cur, time.time()
            if cur and time.time() - stable_since > 5:
                try:
                    convo = urlparse(page.url).path.rstrip("/").rsplit("/", 1)[-1]
                    if convo not in ("", "chat.deepseek.com"):
                        _delete_conversation(firefox_profile, convo)
                except Exception:
                    pass
                return cur
        raise TimeoutError("generation did not finish in time")
    finally:
        await ctx.close()
