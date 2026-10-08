"""`altlay [--backend chatgpt|claude|deepseek] "prompt"` — print the answer."""

from __future__ import annotations

import argparse
import asyncio
import sys


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="altlay", description=__doc__)
    p.add_argument("prompt", nargs="?", help="prompt to send")
    p.add_argument("--backend", choices=("chatgpt", "claude", "deepseek"),
                   default="chatgpt",
                   help="which assistant to ask (default: chatgpt)")
    p.add_argument("--profile", default=None, help="CloakBrowser profile dir (browser transports)")
    p.add_argument("--firefox-profile", default=None, help="source Firefox profile")
    p.add_argument("--timeout", type=float, default=300.0, help="generation timeout (s)")
    p.add_argument("--model", default=None, help="model override (claude)")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--headed", action="store_true",
                       help="force a visible browser window "
                            "(default for chatgpt/deepseek; claude uses direct HTTPS unless set)")
    group.add_argument("--headless", action="store_true",
                       help="run the browser hidden (chatgpt/deepseek default to visible; "
                            "claude uses direct HTTPS unless --headed/--headless selects its browser)")
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if not args.prompt:
        build_parser().print_help()
        raise SystemExit(2)
    if args.backend == "deepseek":
        from altlay.deepseek import DeepSeek
        print(DeepSeek(firefox_profile=args.firefox_profile,
                       timeout=args.timeout, headless=args.headless,
                       profile_dir=args.profile).generate(args.prompt))
    elif args.backend == "claude":
        from altlay.claude import Claude
        use_browser = args.headed or args.headless
        print(Claude(firefox_profile=args.firefox_profile,
                     model=args.model, timeout=args.timeout,
                     transport="browser" if use_browser else "direct",
                     headless=args.headless,
                     profile_dir=args.profile).generate(args.prompt))
    else:
        from altlay.chatgpt import ChatGPT
        answer = asyncio.run(ChatGPT(
            profile_dir=args.profile,
            firefox_profile=args.firefox_profile,
            headless=args.headless,
        ).generate(args.prompt, timeout=args.timeout))
        print(answer)


if __name__ == "__main__":
    sys.exit(main())
