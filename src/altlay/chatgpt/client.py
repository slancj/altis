"""Drive chatgpt.com in a stealth browser (headed by default)."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from altlay.chatgpt import native

native.ensure_native_libs()

from cloakbrowser import launch_persistent_context_async  # noqa: E402

from altlay.chatgpt.cookies import export_cookies  # noqa: E402
from altlay.ui import Streamer, fast_fill  # noqa: E402

COMPOSER = "#prompt-textarea"
SEND = '[data-testid="send-button"]'
STOP = '[data-testid="stop-button"], [data-testid="stop-streaming-button"], button[aria-label="Stop streaming"]'
ASSISTANT = '[data-message-author-role="assistant"]'


def default_profile_dir() -> Path:
    env = os.environ.get("ALTALAY_PROFILE")
    if env:
        return Path(env)
    return Path.home() / ".config" / "altlay" / "profile"


class ChatGPT:
    def __init__(self, profile_dir: str | os.PathLike | None = None,
                 firefox_profile: str | os.PathLike | None = None,
                 viewport: dict | None = None,
                 headless: bool = False) -> None:
        self.profile_dir = Path(profile_dir) if profile_dir else default_profile_dir()
        self.firefox_profile = firefox_profile or os.environ.get("ALTALAY_FIREFOX_PROFILE")
        self.viewport = viewport or {"width": 1280, "height": 900}
        self.headless = headless

    async def generate(self, prompt: str, timeout: float = 300.0,
                       stream: bool = False) -> str:
        out = Streamer(stream)
        ctx = await launch_persistent_context_async(
            self.profile_dir, headless=self.headless, humanize=True, viewport=self.viewport)
        try:
            await ctx.add_cookies(export_cookies(self.firefox_profile))
            page = await ctx.new_page()
            await page.goto("https://chatgpt.com/?temporary-chat=true",
                            wait_until="domcontentloaded")
            try:
                await page.locator(COMPOSER).wait_for(state="visible", timeout=90_000)
            except Exception:
                raise RuntimeError(
                    "not logged in: no chat composer found. "
                    "Log into ChatGPT in Firefox, then retry.")
            await fast_fill(page, COMPOSER, prompt)
            await asyncio.sleep(0.3)
            await page.locator(SEND).click()

            deadline = time.time() + timeout
            last_text, stable_since = "", time.time()
            while time.time() < deadline:
                await asyncio.sleep(1)
                stoppers = page.locator(STOP)
                try:
                    generating = (await stoppers.count() > 0
                                  and await stoppers.first.is_visible())
                except Exception:
                    generating = False
                msgs = page.locator(ASSISTANT)
                cur = await msgs.last.inner_text() if await msgs.count() else ""
                if cur != last_text:
                    last_text, stable_since = cur, time.time()
                out.update(cur)
                if not generating and cur and time.time() - stable_since > 2.5:
                    out.finish(last_text)
                    return last_text
            raise TimeoutError("generation did not finish in time")
        finally:
            await ctx.close()


async def generate(prompt: str, **kwargs) -> str:
    return await ChatGPT(**kwargs).generate(prompt)
