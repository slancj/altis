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
    await loc.click(timeout=15000)
    await loc.type(text, timeout=15000)


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
    cool = 0
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
        # verification takes seconds; don't re-click (and uncheck) too fast
        if cool > 0:
            cool -= 1
        elif clicks < 3 and await _click_turnstile(page):
            clicks += 1
            cool = 2
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


async def goto_with_retries(page, url: str, tries: int = 3) -> None:
    """Goto with retries for flaky networks (net::ERR_ABORTED etc.)."""
    last = None
    for _ in range(tries):
        try:
            await page.goto(url, wait_until="domcontentloaded")
            return
        except Exception as e:
            last = e
            await asyncio.sleep(2)
    raise last


async def _cdp_turnstile_boxes(page) -> list[dict]:
    """Find Turnstile widget iframes via CDP DOM traversal with shadow-DOM
    piercing (Playwright locators can't see closed shadow roots or
    src-less widget frames). Returns viewport-space boxes."""
    boxes = []
    try:
        cdp = await page.context.new_cdp_session(page)
        doc = await cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})

        found: list = []

        def walk(node):
            if not isinstance(node, dict):
                return
            if node.get("nodeName") == "IFRAME":
                attrs = dict(zip(node.get("attributes", [])[::2],
                                 node.get("attributes", [])[1::2]))
                found.append((node.get("backendNodeId"),
                              attrs.get("src", "")))
            for child in node.get("children", []) or []:
                walk(child)
            for shadow in node.get("shadowRoots", []) or []:
                walk(shadow)

        walk(doc.get("root", {}))
        out = []
        for node_id, src in found:
            try:
                model = await cdp.send("DOM.getBoxModel",
                                       {"backendNodeId": node_id})
                q = model["model"]["content"]
                xs, ys = q[::2], q[1::2]
                out.append({"x": min(xs), "y": min(ys),
                            "width": max(xs) - min(xs),
                            "height": max(ys) - min(ys), "src": src[:80]})
            except Exception:
                continue
        return out
    except Exception:
        return []


async def _click_turnstile(page) -> bool:
    """Click the Turnstile checkbox. Discovery via CDP DOM traversal with
    shadow-DOM piercing (locators miss src-less/shadow widgets); the click
    itself goes through Playwright's mouse pipeline (correct OOPIF routing,
    unlike raw CDP dispatch which silently misses)."""
    import sys as _sys
    try:
        boxes = await _cdp_turnstile_boxes(page)
        print(f"[altlay] turnstile candidates: {boxes}",
              file=_sys.stderr, flush=True)
        box = next((b for b in boxes
                    if "challenges.cloudflare.com" in b["src"]
                    or "turnstile" in b["src"] or "cf-turnstile" in b["src"]),
                   None)
        if box is None:  # size fallback: centered ~300x65 iframe
            vw = (page.viewport_size or {}).get("width", 1280)
            box = next((b for b in boxes
                        if 200 <= b["width"] <= 400 and 30 <= b["height"] <= 120
                        and abs((b["x"] + b["width"] / 2) - vw / 2) < 200),
                       None)
        if box is None:
            return False
        x, y = box["x"] + 28, box["y"] + box["height"] / 2
        print(f"[altlay] turnstile click at ({x:.0f}, {y:.0f})",
              file=_sys.stderr, flush=True)
        await page.mouse.click(x, y)
        try:  # keyboard fallback
            await page.keyboard.press("Tab")
            await asyncio.sleep(0.3)
            await page.keyboard.press(" ")
        except Exception:
            pass
        try:  # evidence snapshot (git-ignored debug dir)
            from altlay.pool import vault_home
            dbg = vault_home() / "debug"
            dbg.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(dbg / "turnstile.png"))
        except Exception:
            pass
        return True
    except Exception:
        return False
