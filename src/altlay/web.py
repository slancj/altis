"""Bare-bones web UI: `altlay serve [--port N]`. Stdlib only."""

from __future__ import annotations

import argparse
import html
import io
import json
import threading
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

_BUSY = threading.Lock()

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>altlay</title>
<style>body{font-family:sans-serif;max-width:720px;margin:2em auto;padding:0 1em}
textarea{width:100%;height:6em}pre{white-space:pre-wrap;background:#f4f4f4;padding:1em}
.row{margin:.5em 0}</style></head><body>
<h1>altlay</h1>
<div class="row">backend <select id="b"></select>
account <select id="a"><option value="">(default)</option></select>
<label><input type="checkbox" id="h"> headless</label></div>
<div class="row"><textarea id="p" placeholder="prompt"></textarea></div>
<div class="row"><button onclick="ask()">ask</button> <span id="s"></span></div>
<pre id="o"></pre>
<script>
async function accts(){let r=await fetch("/api/accounts");let j=await r.json();
let b=document.getElementById("b");b.innerHTML="";
for(let k of Object.keys(j)){let o=document.createElement("option");o.value=k;o.text=k;b.add(o)}
upd();b.onchange=upd;
function upd(){let a=document.getElementById("a");a.innerHTML='<option value="">(default)</option>';
for(let n of j[b.value]||[]){let o=document.createElement("option");o.value=n;o.text=n;a.add(o)}}}
async function ask(){let s=document.getElementById("s"),o=document.getElementById("o");
s.textContent="…";o.textContent="";
let r=await fetch("/api/ask",{method:"POST",headers:{"Content-Type":"application/json"},
body:JSON.stringify({backend:document.getElementById("b").value,
account:document.getElementById("a").value||null,
prompt:document.getElementById("p").value,
headless:document.getElementById("h").checked})});
let j=await r.json();s.textContent=j.ok?"["+j.account+"]":"error";
o.textContent=j.ok?j.answer:(j.error||"failed")}
accts();
</script></body></html>
"""


def _answer(backend: str, account: str | None, prompt: str, headless: bool) -> tuple[str, str]:
    from altlay.accounts import resolve_name
    from altlay.pool import AccountPool
    pool = AccountPool(backend)
    if not pool.names():
        raise RuntimeError(f"no {backend} accounts in vault")
    pinned = resolve_name(backend, account)
    order = ([pinned] if pinned else []) + [n for n in pool.names() if n != pinned]
    args = SimpleNamespace(prompt=prompt, backend=backend, account=account,
                           profile=None, firefox_profile=None, timeout=300.0,
                           model=None, headed=False, headless=headless)
    used: list[str] = []

    def attempt(name: str, entry: dict):
        used.append(f"{backend}/{name}")
        buf = io.StringIO()
        with redirect_stdout(buf):
            out = _call_quiet(backend, args, name, entry)
        return out or buf.getvalue().strip()

    answer = pool.run(attempt, order=order)
    return (used[-1] if used else "?"), answer


def _call_quiet(backend: str, args, name: str, entry: dict):
    import asyncio
    from altlay.cli import profile_for
    profile = profile_for(backend, name, args.profile)
    kwargs = {"firefox_profile": args.firefox_profile,
              "profile_dir": profile, "headless": args.headless}
    if backend == "chatgpt":
        from altlay.chatgpt import ChatGPT
        return asyncio.run(ChatGPT(session=entry, account_name=name,
                                   **kwargs).generate(
            args.prompt, timeout=args.timeout, stream=False))
    if backend == "claude":
        from altlay.claude import Claude
        use_browser = args.headed or args.headless
        return Claude(session=entry, model=args.model, timeout=args.timeout,
                      transport="browser" if use_browser else "direct",
                      headless=args.headless, stream=False,
                      profile_dir=profile,
                      firefox_profile=args.firefox_profile).generate(args.prompt)
    from altlay.deepseek import DeepSeek
    return DeepSeek(session=entry, timeout=args.timeout, headless=args.headless,
                    profile_dir=profile,
                    firefox_profile=args.firefox_profile,
                    stream=False).generate(args.prompt)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/accounts":
            from altlay import accounts as A
            self._json({b: [e["name"] for e in A.entries(b)] for b in A.BACKENDS})
        elif self.path == "/":
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path != "/api/ask":
            return self.send_error(404)
        try:
            req = json.loads(self.rfile.read(
                int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
        except (ValueError, OSError):
            return self._json({"ok": False, "error": "bad JSON"}, 400)
        prompt = (req.get("prompt") or "").strip()
        if not prompt:
            return self._json({"ok": False, "error": "empty prompt"}, 400)
        if not _BUSY.acquire(blocking=False):
            return self._json({"ok": False, "error": "busy, try again"}, 409)
        try:
            account, answer = _answer(req.get("backend") or "chatgpt",
                                      req.get("account"), prompt,
                                      bool(req.get("headless")))
        except Exception as e:  # noqa: BLE001 — surfaced as JSON
            return self._json({"ok": False,
                               "error": f"{type(e).__name__}: {html.escape(str(e))}"})
        finally:
            _BUSY.release()
        self._json({"ok": True, "account": account, "answer": answer})


def build_serve_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="altlay serve")
    p.add_argument("--port", type=int, default=8734)
    p.add_argument("--host", default="127.0.0.1")
    return p


def serve(host: str = "127.0.0.1", port: int = 8734) -> None:
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"altlay web UI on http://{host}:{port}")
    srv.serve_forever()
