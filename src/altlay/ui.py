"""Shared browser-UI helpers: instant prompt fill + live answer streaming."""

from __future__ import annotations


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
