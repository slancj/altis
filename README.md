# altlay

Generate text with ChatGPT's, Claude's and DeepSeek's private web APIs, using
your existing Firefox logins.

- **Claude** (`--backend claude`): direct HTTPS, fast, no browser.
- **DeepSeek** (`--backend deepseek`): direct HTTPS + proof-of-work solver.
- **ChatGPT** (default): direct HTTP is blocked by Turnstile + proof-of-work +
  device checks, so `altlay` drives a visible stealth-Chromium window
  (CloakBrowser) with your session cookies imported.

See `docs/RESEARCH.md` for the full reverse-engineering notes.

## Setup

```sh
uv sync
uv run python -m cloakbrowser install   # one-time stealth-binary download (chatgpt)
```

## Use

Be signed into ChatGPT / Claude in Firefox, then:

```sh
uv run altlay "Explain recursion in one sentence"
uv run altlay --backend claude "Explain recursion in one sentence"
uv run altlay --backend deepseek "Explain recursion in one sentence"
uv run altlay --backend claude --headed "Explain recursion in one sentence"
```

ChatGPT opens a browser window and sends the prompt as a temporary chat;
Claude/DeepSeek answer over direct HTTPS unless `--headed` forces a visible
window. The answer is printed in all cases. Browser profile persists at
`~/.config/altlay/profile`.

Env overrides: `ALTALAY_PROFILE` (browser profile dir),
`ALTALAY_FIREFOX_PROFILE` (source Firefox profile).

Nix/NixOS: if Playwright fails with `libstdc++.so.6` missing, export it, e.g.
`export LD_LIBRARY_PATH=/run/current-system/sw/lib:$LD_LIBRARY_PATH`
(the client also preloads it automatically when found).
