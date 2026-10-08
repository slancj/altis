"""DeepSeek client (visible-browser transport)."""

from __future__ import annotations

import asyncio
import os

from altlay.deepseek.browser import generate_browser


class DeepSeek:
    def __init__(self, firefox_profile: str | os.PathLike | None = None,
                 timeout: float = 300.0,
                 profile_dir: str | os.PathLike | None = None) -> None:
        self.firefox_profile = firefox_profile or os.environ.get("ALTALAY_FIREFOX_PROFILE")
        self.timeout = timeout
        self.profile_dir = profile_dir

    def generate(self, prompt: str) -> str:
        return asyncio.run(generate_browser(
            prompt, profile_dir=self.profile_dir,
            firefox_profile=self.firefox_profile, timeout=self.timeout))


def generate(prompt: str, **kwargs) -> str:
    return DeepSeek(**kwargs).generate(prompt)
