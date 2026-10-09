# altlay

Generate text with ChatGPT's, Claude's and DeepSeek's private web APIs, using
your existing Firefox logins.

- **Claude** (`--backend claude`): direct HTTPS, fast, no browser.
- **DeepSeek** (`--backend deepseek`): visible browser like ChatGPT.
- **ChatGPT** (default): direct HTTP is blocked by Turnstile + proof-of-work +
  device checks, so `altlay` drives a visible stealth-Chromium window
  (CloakBrowser) with your session imported.

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
uv run altlay --headless "Explain recursion in one sentence"
```

ChatGPT/DeepSeek open a browser window and send the prompt (temporary chat /
history cleanup where supported); Claude answers over direct HTTPS unless
`--headed` forces a visible window. `--headless` hides the browser for any
backend that uses one (headless trips bot checks more often, so visible is
the default). Headed runs type the prompt visibly; headless runs paste it
instantly. The answer streams live in all cases.

## Accounts (Alt + Relay)

Hand over accounts and altlay pools them with automatic failover on limits:

```sh
uv run altlay accounts add --from-firefox     # snapshot all Firefox sessions
uv run altlay accounts add claude work --token '...'   # paste a session
uv run altlay accounts list
uv run altlay accounts use chatgpt dan        # default per backend
uv run altlay accounts check                  # validity probes, no generation
uv run altlay --account dan "prompt"          # pin (still fails over on limits)
```

Everything lives in `./.altlay/` (git-ignored): `accounts.json` (your
sessions), `state.json` (cooldowns), `config.toml` (defaults), `profiles/`
(per-account browser dirs).

Env overrides: `ALTALAY_HOME` (vault root), `ALTALAY_PROFILE` (browser profile
dir), `ALTALAY_FIREFOX_PROFILE` (source Firefox profile),
`ALTALAY_ACCOUNT` (account alias).

Nix/NixOS: if Playwright fails with `libstdc++.so.6` missing, export it, e.g.
`export LD_LIBRARY_PATH=/run/current-system/sw/share/nix-ld/lib:$LD_LIBRARY_PATH`
(the client also preloads it automatically when found).
