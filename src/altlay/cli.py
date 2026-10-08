"""`altlay [--backend chatgpt|claude] "prompt"` — print the answer."""

from __future__ import annotations

import argparse
import asyncio
import sys


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="altlay", description=__doc__)
    p.add_argument("prompt", nargs="?", help="prompt to send")
    p.add_argument("--backend", choices=("chatgpt", "claude"), default="chatgpt",
                   help="which assistant to ask (default: chatgpt)")
    p.add_argument("--profile", default=None, help="CloakBrowser profile dir (chatgpt)")
    p.add_argument("--firefox-profile", default=None, help="source Firefox profile")
    p.add_argument("--timeout", type=float, default=300.0, help="generation timeout (s)")
    p.add_argument("--model", default=None, help="model override (claude)")
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if not args.prompt:
        build_parser().print_help()
        raise SystemExit(2)
    if args.backend == "claude":
        from altlay.claude import Claude
        print(Claude(firefox_profile=args.firefox_profile,
                     model=args.model, timeout=args.timeout).generate(args.prompt))
    else:
        from altlay.chatgpt import ChatGPT
        answer = asyncio.run(ChatGPT(
            profile_dir=args.profile,
            firefox_profile=args.firefox_profile,
        ).generate(args.prompt, timeout=args.timeout))
        print(answer)


if __name__ == "__main__":
    sys.exit(main())
