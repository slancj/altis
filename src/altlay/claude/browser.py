"""Claude in a stealth browser (headed by default)."""

from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from altlay.chatgpt import native

native.ensure_native_libs()

from cloakbrowser import launch_persistent_context_async  # noqa: E402

from altlay.chatgpt.cookies import export_cookies  # noqa: E402
from altlay.pool import RateLimited, parse_reset_time  # noqa: E402
from altlay.ui import (  # noqa: E402
    Streamer,
    arm_overlay_handlers,
    disarm_overlay_handlers,
    enter_prompt,
    goto_with_retries,
)

DOMAINS = ("claude.ai", "anthropic.com")
COMPOSER = 'div[contenteditable="true"]'
SEND = 'button[aria-label="Send message"]'
ASSISTANT = '[data-testid="assistant-message"]'
LIMIT_RE = re.compile(
    r"(reached|hit|exceeded).{0,60}(limit|quota)|usage limit|try again (later|at|until)",
    re.IGNORECASE)


async def _raise_if_limited(page) -> None:
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


def default_profile_dir() -> Path:
    env = os.environ.get("ALTALAY_PROFILE")
    if env:
        return Path(env)
    from altlay.pool import vault_home
    return vault_home() / "profile"


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
    ff = firefox_profile or os.environ.get("ALTALAY_FIREFOX_PROFILE")
    ctx = await launch_persistent_context_async(
        profile, headless=headless, humanize=True,
        viewport=viewport or {"width": 1280, "height": 900})
    try:
        cookies = session["cookies"] if session is not None else export_cookies(
            ff, domains=DOMAINS, session_marker="sessionKey")
        await ctx.add_cookies(cookies)
        page = await ctx.new_page()
        await arm_overlay_handlers(page)
        await goto_with_retries(page, "https://claude.ai/new")
        try:
            await page.locator(COMPOSER).wait_for(state="visible", timeout=90_000)
        except Exception:
            raise RuntimeError(
                "not logged in: no Claude composer found. "
                "Log into Claude in Firefox, then retry.")
        await disarm_overlay_handlers(page)
        await enter_prompt(page, COMPOSER, prompt, instant=headless)
        await asyncio.sleep(0.3)
        await page.locator(SEND).click()

        deadline = time.time() + timeout
        last_text, stable_since = "", time.time()
        while time.time() < deadline:
            await asyncio.sleep(1)
            try:
                send_label = await page.locator(SEND).get_attribute("aria-label") or ""
            except Exception:
                send_label = ""
            busy = send_label.lower() != "send message"
            msgs = page.locator(ASSISTANT)
            cur = await msgs.last.inner_text() if await msgs.count() else ""
            if not cur or (len(cur) < 300 and LIMIT_RE.search(cur)):
                await _raise_if_limited(page)
            if cur != last_text:
                last_text, stable_since = cur, time.time()
            out.update(cur)
            if not busy and cur and time.time() - stable_since > 2.5:
                try:
                    convo = urlparse(page.url).path.rsplit("/", 1)[-1]
                    _delete_conversation(cookies, convo)
                except Exception:
                    pass
                out.finish(last_text)
                return cur
        raise TimeoutError("generation did not finish in time")
    finally:
        await ctx.close()


def _delete_conversation(cookies: list[dict], convo_id: str) -> None:
    """Best-effort history cleanup via the direct API."""
    from altlay.claude.client import Claude
    client = Claude(session={"cookies": cookies})
    client._req("DELETE", f"/api/organizations/{client._org_path()}"
                          f"/chat_conversations/{convo_id}")
