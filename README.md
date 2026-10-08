# altlay

Generate text with ChatGPT's private web API, using your existing Firefox
login. Direct HTTP calls are blocked by Turnstile + proof-of-work + device
checks, so `altlay` drives a visible stealth-Chromium window (CloakBrowser)
with your session cookies imported. See `docs/RESEARCH.md` for the full
reverse-engineering notes.

## Setup

```sh
uv sync
uv run python -m cloakbrowser install   # one-time stealth-binary download
```

## Use

Be signed into ChatGPT in Firefox, then:

```sh
uv run altlay "Explain recursion in one sentence"
```

A browser window opens, the prompt is sent as a temporary chat, and the
answer is printed. Browser profile persists at `~/.config/altlay/profile`.

Env overrides: `ALTALAY_PROFILE` (browser profile dir),
`ALTALAY_FIREFOX_PROFILE` (source Firefox profile).

Nix/NixOS: if Playwright fails with `libstdc++.so.6` missing, export it, e.g.
`export LD_LIBRARY_PATH=/run/current-system/sw/lib:$LD_LIBRARY_PATH`
(the client also preloads it automatically when found).
