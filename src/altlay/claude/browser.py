"""Claude in a visible stealth browser (the `--headed` path)."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from urllib.parse import urlparse

from altlay.chatgpt import native

native.ensure_native_libs()

from cloakbrowser import launch_persistent_context_async  # noqa: E402

from altlay.chatgpt.cookies import export_cookies  # noqa: E402

DOMAINS = ("claude.ai", "anthropic.com")
COMPOSER = 'div[contenteditable="true"]'
SEND = 'button[aria-label="Send message"]'
ASSISTANT = '[data-testid="assistant-message"]'


def default_profile_dir() -> Path:
    env = os.environ.get("ALTALAY_PROFILE")
    if env:
        return Path(env)
    return Path.home() / ".config" / "altlay" / "profile"


async def generate_browser(prompt: str,
                           profile_dir: str | os.PathLike | None = None,
                           firefox_profile: str | os.PathLike | None = None,
                           viewport: dict | None = None,
                           timeout: float = 300.0) -> str:
    profile = Path(profile_dir) if profile_dir else default_profile_dir()
    ff = firefox_profile or os.environ.get("ALTALAY_FIREFOX_PROFILE")
    ctx = await launch_persistent_context_async(
        profile, headless=False, humanize=True,
        viewport=viewport or {"width": 1280, "height": 900})
    try:
        await ctx.add_cookies(export_cookies(ff, domains=DOMAINS,
                                             session_marker="sessionKey"))
        page = await ctx.new_page()
        await page.goto("https://claude.ai/new", wait_until="domcontentloaded")
        try:
            await page.locator(COMPOSER).wait_for(state="visible", timeout=90_000)
        except Exception:
            raise RuntimeError(
                "not logged in: no Claude composer found. "
                "Log into Claude in Firefox, then retry.")
        composer = page.locator(COMPOSER)
        await composer.click()
        await composer.fill(prompt)
        await asyncio.sleep(1)
        await page.locator(SEND).click()

        deadline = time.time() + timeout
        last_text, stable_since = "", time.time()
        while time.time() < deadline:
            await asyncio.sleep(2)
            try:
                send_label = await page.locator(SEND).get_attribute("aria-label") or ""
            except Exception:
                send_label = ""
            busy = send_label.lower() != "send message"
            msgs = page.locator(ASSISTANT)
            cur = await msgs.last.inner_text() if await msgs.count() else ""
            if cur != last_text:
                last_text, stable_since = cur, time.time()
            if not busy and cur and time.time() - stable_since > 5:
                try:
                    convo = urlparse(page.url).path.rsplit("/", 1)[-1]
                    _delete_conversation(ff, convo)
                except Exception:
                    pass
                return cur
        raise TimeoutError("generation did not finish in time")
    finally:
        await ctx.close()


def _delete_conversation(firefox_profile, convo_id: str) -> None:
    """Best-effort history cleanup via the direct API."""
    from altlay.claude.client import Claude
    client = Claude(firefox_profile=firefox_profile)
    client._req("DELETE", f"/api/organizations/{client._org_path()}"
                          f"/chat_conversations/{convo_id}")
