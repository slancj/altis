"""DeepSeek in a visible stealth browser (the `--headed` path)."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from altlay.chatgpt import native

native.ensure_native_libs()

from cloakbrowser import launch_persistent_context_async  # noqa: E402

from altlay.deepseek.storage import _read_value, _ls_candidates  # noqa: E402

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


def _raw_user_token() -> str:
    for db in _ls_candidates():
        v = _read_value(db, "userToken")
        if isinstance(v, dict) and v.get("value"):
            import json as j
            return j.dumps(v)
    raise RuntimeError("no logged-in DeepSeek session found in browser profiles")


async def generate_browser(prompt: str,
                           profile_dir: str | os.PathLike | None = None,
                           firefox_profile: str | os.PathLike | None = None,
                           viewport: dict | None = None,
                           timeout: float = 300.0) -> str:
    profile = Path(profile_dir) if profile_dir else default_profile_dir()
    raw_token = _raw_user_token()
    ctx = await launch_persistent_context_async(
        profile, headless=False, humanize=True,
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
                return cur
        raise TimeoutError("generation did not finish in time")
    finally:
        await ctx.close()
