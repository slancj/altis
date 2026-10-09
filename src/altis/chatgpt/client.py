"""Drive chatgpt.com in a stealth browser (headed by default)."""

from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path

from altis.chatgpt import native

native.ensure_native_libs()

from cloakbrowser import launch_persistent_context_async  # noqa: E402

from altis.chatgpt.cookies import export_cookies  # noqa: E402
from altis.pool import AccountDead, RateLimited, env as _getenv, parse_reset_time  # noqa: E402
from altis.ui import (  # noqa: E402
    Streamer,
    arm_overlay_handlers,
    disarm_overlay_handlers,
    enter_prompt,
    goto_with_retries,
    wait_for_composer,
)

COMPOSER = "#prompt-textarea"
SEND = '[data-testid="send-button"]'
STOP = '[data-testid="stop-button"], [data-testid="stop-streaming-button"], button[aria-label="Stop streaming"]'
ASSISTANT = '[data-message-author-role="assistant"]'


def default_profile_dir() -> Path:
    env = _getenv("PROFILE")
    if env:
        return Path(env)
    from altis.pool import vault_home
    return vault_home() / "profile"


class ChatGPT:
    def __init__(self, profile_dir: str | os.PathLike | None = None,
                 firefox_profile: str | os.PathLike | None = None,
                 viewport: dict | None = None,
                 headless: bool = False,
                 session: dict | None = None,
                 account_name: str | None = None) -> None:
        self.profile_dir = Path(profile_dir) if profile_dir else default_profile_dir()
        self.firefox_profile = firefox_profile or _getenv("FIREFOX_PROFILE")
        self.viewport = viewport or {"width": 1280, "height": 900}
        self.headless = headless
        self.session = session
        self.account_name = account_name

    def _cookies(self) -> list[dict]:
        if self.session is not None:
            return self.session["cookies"]
        return export_cookies(self.firefox_profile)

    async def generate(self, prompt: str, timeout: float = 300.0,
                       stream: bool = False) -> str:
        out = Streamer(stream)
        ctx = await launch_persistent_context_async(
            self.profile_dir, headless=self.headless, humanize=True, viewport=self.viewport)
        try:
            await ctx.add_cookies(self._cookies())
            page = await ctx.new_page()
            await arm_overlay_handlers(page)
            await goto_with_retries(
                page, "https://chatgpt.com/?temporary-chat=true")
            try:
                await wait_for_composer(page, COMPOSER)
            except Exception:
                try:
                    expired = await page.locator(
                        '[data-testid="modal-expired-session"]').count() > 0
                    login_visible = expired or await page.locator(
                        'a:has-text("Log in"), button:has-text("Log in")').count() > 0
                    if not login_visible:
                        body = await page.locator("body").inner_text()
                        login_visible = "log in again" in body.lower()
                except Exception:
                    login_visible = False
                if login_visible:
                    raise AccountDead("session logged out in browser") from None
                raise RateLimited(
                    time.time() + 300,
                    "cloudflare challenge did not clear; benched 5 min") from None
            await disarm_overlay_handlers(page)
            if await _modal_present(page):
                await _heal_or_die(page, ctx, self.account_name)
            for _ in range(2):
                try:
                    await enter_prompt(page, COMPOSER, prompt)
                    await asyncio.sleep(0.3)
                    await page.locator(SEND).click()
                    break
                except Exception:
                    if await _modal_present(page):
                        await _heal_or_die(page, ctx, self.account_name)
                        continue
                    raise

            deadline = time.time() + timeout
            last_text, stable_since = "", time.time()
            while time.time() < deadline:
                await asyncio.sleep(1)
                msgs = page.locator(ASSISTANT)
                cur = await msgs.last.inner_text() if await msgs.count() else ""
                if not cur or (len(cur) < 300 and LIMIT_RE.search(cur)):
                    # limit notice with no real answer (a short error bubble
                    # also matches) — a genuine essay about limits won't.
                    await _raise_if_limited(page)
                stoppers = page.locator(STOP)
                try:
                    generating = (await stoppers.count() > 0
                                  and await stoppers.first.is_visible())
                except Exception:
                    generating = False
                if cur != last_text:
                    last_text, stable_since = cur, time.time()
                out.update(cur)
                if not generating and cur and time.time() - stable_since > 2.5:
                    out.finish(last_text)
                    return last_text
            raise TimeoutError("generation did not finish in time")
        finally:
            await ctx.close()


LIMIT_RE = re.compile(
    r"(reached|hit|exceeded).{0,60}(limit|quota)|usage limit|try again (later|at|until)",
    re.IGNORECASE)


async def _modal_present(page) -> bool:
    try:
        if await page.locator('[data-testid="modal-expired-session"]').count():
            return True
        body = await page.locator("body").inner_text()
        return "log in again" in body.lower()
    except Exception:
        return False


async def _heal_or_die(page, ctx, account_name: str | None) -> None:
    """Expired-session modal: a present user re-logs in inside the visible
    window and the vault entry self-heals; otherwise AccountDead."""
    import sys
    if account_name is None or not sys.stdin.isatty():
        raise AccountDead("session expired (re-login needs a terminal)") from None
    print(f"[altis] {account_name}: session expired — log in again in the "
          f"browser window, then press Enter here. (Ctrl-C aborts)",
          flush=True)
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        raise AccountDead("re-login aborted") from None
    if await _modal_present(page):
        raise AccountDead("still expired after re-login") from None
    try:
        from altis.accounts import update_session
        cookies = [c for c in await ctx.cookies()
                   if c["domain"].endswith("chatgpt.com")
                   or c["domain"].endswith("openai.com")]
        if cookies:
            update_session("chatgpt", account_name, {"cookies": cookies})
            print("[altis] vault entry refreshed", flush=True)
    except Exception as e:
        print(f"[altis] vault refresh failed: {e}", flush=True)


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


async def generate(prompt: str, **kwargs) -> str:
    return await ChatGPT(**kwargs).generate(prompt)
