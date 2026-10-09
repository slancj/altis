"""Shared browser-UI helpers: instant prompt fill + live answer streaming."""

from __future__ import annotations

import asyncio


async def fast_fill(page, selector: str, text: str) -> None:
    """Insert text instantly via the editing pipeline (proper input events),
    falling back to humanized fill if the app doesn't pick it up."""
    await page.locator(selector).click()
    await page.evaluate(
        """([sel, t]) => {
          const el = document.querySelector(sel);
          if (!el) return false;
          el.focus();
          let ok = false;
          try { ok = document.execCommand('insertText', false, t); } catch (e) {}
          if (!ok) {
            if ('value' in el) { el.value = t; }
            else { el.textContent = t; }
            el.dispatchEvent(new InputEvent('input',
              {bubbles: true, data: t, inputType: 'insertText'}));
          }
          return true;
        }""",
        [selector, text])
    if not await _contains(page, selector, text):
        await page.locator(selector).fill(text)


async def _contains(page, selector: str, text: str) -> bool:
    try:
        loc = page.locator(selector)
        try:
            current = await loc.input_value()
        except Exception:
            current = await loc.inner_text()
        return text in (current or "")
    except Exception:
        return False


async def enter_prompt(page, selector: str, text: str, instant: bool = False) -> None:
    """Headed: humanized per-character typing (visible in the window).
    Headless: instant paste (fast)."""
    if instant:
        await fast_fill(page, selector, text)
        return
    loc = page.locator(selector)
    await loc.click()
    await loc.type(text)


class Streamer:
    """Prints answer deltas live; generate() still returns the full text."""
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self.printed = ""

    def update(self, text: str) -> None:
        if not self.enabled or not text.startswith(self.printed):
            return
        suffix = text[len(self.printed):]
        if suffix:
            print(suffix, end="", flush=True)
            self.printed = text

    def finish(self, text: str) -> None:
        if not self.enabled:
            return
        if text != self.printed:
            print(text[len(self.printed):] if text.startswith(self.printed)
                  else text, end="", flush=True)
        print("", flush=True)


async def wait_for_composer(page, selector: str, timeout: float = 60.0) -> None:
    """Wait for the chat composer, clicking through a managed Cloudflare
    Turnstile checkbox ('Verify you are human') if it appears."""
    deadline = asyncio.get_event_loop().time() + timeout
    clicks = 0
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            break
        try:
            await page.locator(selector).wait_for(
                state="visible", timeout=min(3000, remaining * 1000))
            return
        except Exception:
            pass
        if clicks < 5 and await _click_turnstile(page):
            clicks += 1
    await page.locator(selector).wait_for(state="visible", timeout=1000)


OVERLAY_BUTTONS = ["Got it", "Stay logged out", "Dismiss", "Accept all",
                   "Reject all", "Not now", "No thanks"]
# NOTE: never auto-click generic "Close"/"Done" — ChatGPT's own UI contains
# such buttons behind intercepting overlays; the handler's click hangs 30s+.


async def _dismiss(overlay) -> None:
    try:
        await overlay.click(timeout=2000)
    except Exception:
        pass


async def arm_overlay_handlers(page) -> None:
    """Auto-dismiss common overlays (cookie banners, upsell modals, ...).
    Register right after opening the page; disarm once the composer is up."""
    for text in OVERLAY_BUTTONS:
        try:
            await page.add_locator_handler(
                page.get_by_role("button", name=text), _dismiss)
        except Exception:
            pass


async def disarm_overlay_handlers(page) -> None:
    for text in OVERLAY_BUTTONS:
        try:
            await page.remove_locator_handler(
                page.get_by_role("button", name=text))
        except Exception:
            pass


async def _click_turnstile(page) -> bool:
    """Click the Turnstile checkbox at its exact center via raw CDP input
    (instant, trusted, bypasses all mouse-animation wrappers).
    The widget lives in a cross-origin iframe under closed shadow DOM, so
    DOM piercing is unreliable — geometry + raw input always works.
    Returns True if a click was issued."""
    try:
        frame = None
        for sel in ('iframe[src*="challenges.cloudflare.com"]',
                    "#cf-turnstile iframe", 'iframe[src*="turnstile"]'):
            loc = page.locator(sel)
            if await loc.count():
                frame = loc.first
                break
        if frame is None:
            return False
        box = await frame.bounding_box()
        if not box:
            return False
        # checkbox sits at the left edge, vertically centered (~28px in).
        x, y = box["x"] + 28, box["y"] + box["height"] / 2
        import sys as _sys
        print(f"[altlay] turnstile click at ({x:.0f}, {y:.0f})",
              file=_sys.stderr, flush=True)
        try:
            cdp = await page.context.new_cdp_session(page)
            for kind in ("mousePressed", "mouseReleased"):
                await cdp.send("Input.dispatchMouseEvent", {
                    "type": kind, "x": x, "y": y, "button": "left",
                    "clickCount": 1})
        except Exception:
            await page.mouse.click(x, y)
        return True
    except Exception:
        return False
