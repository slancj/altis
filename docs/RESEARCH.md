# ChatGPT + Claude web-API reverse-engineering notes

How `altlay` generates text with a free ChatGPT account, and why it drives a
real browser instead of calling HTTP directly. Verified Oct 2026.

## Session source

The user is signed in on Firefox. Auth state lives in `cookies.sqlite`
(`moz_cookies` table):

| Cookie | Host | Notes |
|---|---|---|
| `__Secure-next-auth.session-token.0` + `.1` | `.chatgpt.com` | JWE (dir/A256GCM), chunked — send both chunks verbatim |
| `oai-did` | `.chatgpt.com` | device id, also sent as `oai-device-id` header |
| `oai-sc` | `.chatgpt.com` | session context |
| `cf_clearance`, `__cf_bm` | `.chatgpt.com` | Cloudflare clearance, IP/UA-bound |

Firefox `moz_cookies.sameSite`: `0=None, 1=Lax, 2=Strict, 256=unset`.
`moz_cookies.expiry` on this host is **milliseconds**; Playwright wants seconds.

## Handshake (all reachable with plain HTTP + browser TLS)

1. `GET https://chatgpt.com/api/auth/session` with cookies →
   `{accessToken (RS256 JWS, ~2KB), expires, user: {email, ...}}`.
   Plain curl gets Cloudflare-403; `curl_cffi` with Chrome impersonation → 200.
2. `POST https://chatgpt.com/backend-api/sentinel/chat-requirements` (`{}` body,
   `Authorization: Bearer <accessToken>`) → 200:
   `{persona, token, expire_after, expire_at, turnstile, proofofwork, so}`.
3. `GET https://chatgpt.com/backend-api/models` → model slugs (e.g. `gpt-5-5`).
4. `GET https://chatgpt.com/backend-api/conversations?offset=0&limit=N` works.

Useful page scrapes: build hash for the `oai-client-version` header lives in
`chatgpt.com` HTML as `<html data-build="prod-<hash>">`.

## The wall: `POST /backend-api/conversation`

Minimal body that reaches the abuse filter:

```json
{"action":"next",
 "messages":[{"id":"<uuid>","author":{"role":"user"},
              "content":{"content_type":"text","parts":["<prompt>"]},"metadata":{}}],
 "conversation_mode":{"kind":"primary_assistant"},
 "force_paragen":false,"history_and_training_disabled":false,
 "model":"gpt-5-5","parent_message_id":"<same uuid>"}
```

With `Authorization`, full cookies, `oai-device-id`, `oai-client-version`,
`oai-language`, `Origin`, `Referer` and the sentinel token header
(`openai-sentinel-chat-requirements-token`), the response is still:

```json
{"detail":"Unusual activity has been detected from your device. ..."}
```

Because `chat-requirements` returns, for this account:

- `proofofwork: {required: true, seed (17 chars), difficulty: "063ffa"}`
  — SHA3-512 nonce search, solvable in pure Python (threshold ≈ 2.4%/try).
- `turnstile: {required: true, dx: <29KB challenge blob>}`
  — Cloudflare Turnstile widget, needs real browser rendering.
- `so: {required: true, collector_dx, snapshot_dx}`
  — obfuscated JS device collector, needs a real browser JS runtime.

PoW alone is not enough; Turnstile + `so` tokens can't be minted headlessly
without an arms race. So the transport is a genuine browser.

## Claude: direct HTTPS works (no browser needed)

Session cookies on `.claude.ai`: `sessionKey` (`sk-ant-sid...`), `lastActiveOrg`
(org uuid), `anthropic-device-id`, `cf_clearance`. Plain stdlib `urllib` with a
Firefox UA is enough — no TLS impersonation, no Turnstile.

1. `GET /api/organizations` → org list; use `lastActiveOrg` cookie (else first).
2. `POST /api/organizations/{org}/chat_conversations` `{"uuid","name":""}`
   → `201 {uuid, model: "claude-sonnet-5-5", ...}`.
3. `POST .../chat_conversations/{id}/completion`
   `{"prompt","timezone","attachments":[],"files":[],"sync_sources":[]}` →
   SSE `event: completion` / `data: {"completion":"<chunk>",...}` lines;
   concatenate `completion` fields until `stop_reason` is set.
4. `DELETE .../chat_conversations/{id}` removes the run from history.

`--headed` runs Claude in the visible browser instead (`/new` → composer
`div[contenteditable="true"]` → `button[aria-label="Send message"]` →
`[data-testid="assistant-message"]` last, done when the send button reads
"Send message" again and text is stable; cleanup reuses the direct DELETE).

## DeepSeek: direct HTTPS + proof-of-work (no browser needed)

Auth is **localStorage**, not cookies: `userToken` (`{"value":"<64-char token>"}`)
in Firefox `storage/default/https+++chat.deepseek.com*/ls/data.sqlite`
(`moz_cookies` has nothing). Account here: `and*******216@gmail.com`
(flatpak Firefox; two older sessions exist in the Zen profile — most-recent
valid token wins). Plain stdlib `urllib` + Firefox UA suffices.

1. `GET /api/v0/users/current` (Bearer) → identity check.
2. `POST /api/v0/chat_session/create` `{}` → `{id}` (default model).
3. `POST /api/v0/chat/create_pow_challenge` `{"target_path":
   "/api/v0/chat/completion"}` → `{algorithm: "DeepSeekHashV1", challenge,
   salt, signature, difficulty: 144000, expire_at (5 min)}`.
4. Solve: `prefix = f"{salt}_{expire_at}_"`, find `nonce` in `[0, difficulty)`
   with `DeepSeekHashV1(prefix + str(nonce)) == challenge`. DeepSeekHashV1 =
   SHA3-256 with Keccak-f[1600] **round 0 skipped** (rounds 1..23 only);
   pure-Python solver in `deepseek/pow.py` (~25 s, verified byte-identical
   against a browser-captured answer). Header: `X-Ds-Pow-Response` =
   base64(JSON `{algorithm, challenge, salt, answer, signature, target_path}`).
5. `POST /api/v0/chat/completion` `{chat_session_id, parent_message_id: null,
   prompt, ref_file_ids: [], thinking_enabled, search_enabled}` → JSON-patch
   SSE: content arrives as `{"p":"response/fragments/-1/content","o":"APPEND",
   "v":"<text>"}` with continuations as bare `{"v":"<text>"}`; short answers
   arrive inline in the state snapshot (`v.response.fragments[].content`).
6. `DELETE /api/v0/chat_session/{id}` removes the run from history.

`--headed` seeds `userToken` into a visible Chromium profile and drives the UI
(`textarea` composer → Enter → last assistant bubble, API DELETE after).

## Transport: stealth Chromium (CloakBrowser, visible window)

- `launch_persistent_context(headless=False, humanize=True)` + `add_cookies()`
  with the Firefox export → logged in, Turnstile/PoW/`so` handled by page JS.
- `GET https://chatgpt.com/?temporary-chat=true` keeps runs out of history.
- UI contract: composer `#prompt-textarea`, send `[data-testid="send-button"]`,
  streaming indicator `[data-testid="stop-button"]` (+ `stop-streaming-button`
  and `button[aria-label="Stop streaming"]` variants),
  answers `[data-message-author-role="assistant"]` (take last, wait 4s stable).
- Free-tier note: no key needed for CloakBrowser's bundled v146 binary;
  `cloakbrowser login` unlocks the latest build (1 session free).
