"""DeepSeek client (browser transport, headed by default)."""

from __future__ import annotations

import asyncio
import os

from altis.deepseek.browser import generate_browser
from altis.pool import env as _getenv


class DeepSeek:
    def __init__(self, firefox_profile: str | os.PathLike | None = None,
                 timeout: float = 300.0,
                 profile_dir: str | os.PathLike | None = None,
                 headless: bool = False, stream: bool = False,
                 session: dict | None = None) -> None:
        self.firefox_profile = firefox_profile or _getenv("FIREFOX_PROFILE")
        self.timeout = timeout
        self.profile_dir = profile_dir
        self.headless = headless
        self.stream = stream
        self.session = session

    def generate(self, prompt: str) -> str:
        return asyncio.run(generate_browser(
            prompt, profile_dir=self.profile_dir,
            firefox_profile=self.firefox_profile, timeout=self.timeout,
            headless=self.headless, stream=self.stream,
            session=self.session))


def generate(prompt: str, **kwargs) -> str:
    return DeepSeek(**kwargs).generate(prompt)
