"""`altlay [--backend B] [--account A] "prompt"` — print the answer (with failover)."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="altlay", description=__doc__)
    p.add_argument("prompt", nargs="?", help="prompt to send")
    p.add_argument("--backend", choices=("chatgpt", "claude", "deepseek"),
                   default="chatgpt",
                   help="which assistant to ask (default: chatgpt)")
    p.add_argument("--account", default=None,
                   help="account alias (tried first, pool fails over on limits)")
    p.add_argument("--profile", default=None, help="CloakBrowser profile dir override")
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


def build_accounts_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="altlay accounts")
    sub = p.add_subparsers(dest="action", required=True)

    add = sub.add_parser("add", help="add an account (you hand it over, altlay files it)")
    add.add_argument("backend", nargs="?", choices=("chatgpt", "claude", "deepseek"))
    add.add_argument("name", nargs="?", help="alias (default: from email)")
    add.add_argument("--from-firefox", action="store_true",
                     help="snapshot live Firefox session(s), all containers")
    add.add_argument("--token", default=None, help="paste a session (cookie string/token)")

    lst = sub.add_parser("list", help="list vault accounts")
    lst.add_argument("backend", nargs="?", choices=("chatgpt", "claude", "deepseek"))

    rm = sub.add_parser("remove", help="remove an account")
    rm.add_argument("backend", choices=("chatgpt", "claude", "deepseek"))
    rm.add_argument("name")

    rn = sub.add_parser("rename", help="rename an account")
    rn.add_argument("backend", choices=("chatgpt", "claude", "deepseek"))
    rn.add_argument("old")
    rn.add_argument("new")

    use = sub.add_parser("use", help="set the default account for a backend")
    use.add_argument("backend", choices=("chatgpt", "claude", "deepseek"))
    use.add_argument("name")

    chk = sub.add_parser("check", help="validity-probe stored accounts (no generation)")
    chk.add_argument("backend", nargs="?", choices=("chatgpt", "claude", "deepseek"))

    imp = sub.add_parser("import", help="import a minimal pool JSON file")
    imp.add_argument("--file", required=True)
    return p


def profile_for(backend: str, name: str | None, override: str | None) -> str | None:
    if override:
        return override
    if name is None:
        return None  # legacy shared-profile default
    from altlay.pool import vault_home
    return str(vault_home() / "profiles" / backend / name)


def build_attempt(backend: str, args):
    headless = args.headless
    timeout = args.timeout

    def attempt(name: str, entry: dict):
        profile = profile_for(backend, name, args.profile)
        kwargs = {"firefox_profile": args.firefox_profile,
                  "profile_dir": profile, "headless": headless}
        if backend == "chatgpt":
            from altlay.chatgpt import ChatGPT
            print(f"[altlay] {backend}/{name}", file=sys.stderr, flush=True)
            return asyncio.run(ChatGPT(session=entry, **kwargs).generate(
                args.prompt, timeout=timeout, stream=True))
        if backend == "claude":
            from altlay.claude import Claude
            use_browser = args.headed or args.headless
            print(f"[altlay] {backend}/{name}"
                  + ("" if use_browser else " (direct)"),
                  file=sys.stderr, flush=True)
            answer = Claude(session=entry, model=args.model, timeout=timeout,
                            transport="browser" if use_browser else "direct",
                            headless=headless, stream=use_browser,
                            profile_dir=profile,
                            firefox_profile=args.firefox_profile).generate(args.prompt)
            if not use_browser:
                print(answer)
            return answer
        from altlay.deepseek import DeepSeek
        print(f"[altlay] {backend}/{name}", file=sys.stderr, flush=True)
        return DeepSeek(session=entry, timeout=timeout, headless=headless,
                        profile_dir=profile,
                        firefox_profile=args.firefox_profile,
                        stream=True).generate(args.prompt)
    return attempt


def main(argv: list[str] | None = None) -> None:
    raw = list(argv) if argv is not None else sys.argv[1:]
    if raw and raw[0] == "accounts":
        accounts_main(build_accounts_parser().parse_args(raw[1:]))
        return
    args = build_parser().parse_args(raw)
    if not args.prompt:
        build_parser().print_help()
        raise SystemExit(2)
    backend = args.backend
    from altlay.accounts import resolve_name
    from altlay.pool import AccountPool, AllLimited
    pool = AccountPool(backend)
    if not pool.names():
        legacy_main(backend, args)  # no vault yet: old live-discovery behavior
        return
    pinned = resolve_name(backend, args.account)
    if pinned and pinned not in pool.names():
        print(f"error: no {backend} account {pinned!r} "
              f"(have: {', '.join(pool.names())})", file=sys.stderr)
        raise SystemExit(2)
    order = ([pinned] if pinned else []) + [n for n in pool.names() if n != pinned]
    try:
        pool.run(build_attempt(backend, args), order=order)
    except AllLimited as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(3)


def legacy_main(backend: str, args) -> None:
    """Pre-vault behavior: live Firefox discovery, shared profile."""
    if backend == "chatgpt":
        from altlay.chatgpt import ChatGPT
        asyncio.run(ChatGPT(
            profile_dir=args.profile, firefox_profile=args.firefox_profile,
            headless=args.headless,
        ).generate(args.prompt, timeout=args.timeout, stream=True))
    elif backend == "claude":
        from altlay.claude import Claude
        use_browser = args.headed or args.headless
        answer = Claude(firefox_profile=args.firefox_profile,
                        model=args.model, timeout=args.timeout,
                        transport="browser" if use_browser else "direct",
                        headless=args.headless, stream=use_browser,
                        profile_dir=args.profile).generate(args.prompt)
        if not use_browser:
            print(answer)
    else:
        from altlay.deepseek import DeepSeek
        DeepSeek(firefox_profile=args.firefox_profile, timeout=args.timeout,
                 headless=args.headless, profile_dir=args.profile,
                 stream=True).generate(args.prompt)


def accounts_main(args) -> None:
    from altlay import accounts as A
    action = args.action
    if action == "list":
        backends = [args.backend] if getattr(args, "backend", None) else list(A.BACKENDS)
        from altlay.pool import load_state
        state = load_state()
        import time as _t
        for b in backends:
            default = A.get_default(b)
            for e in A.entries(b):
                cd = state.get(f"{b}/{e['name']}", {}).get("cooldown_until", 0)
                flag = ""
                if e["name"] == default:
                    flag += " [default]"
                if cd > _t.time():
                    flag += f" [cooldown until {_t.strftime('%H:%M', _t.localtime(cd))}]"
                print(f"{b}/{e['name']}{flag}  email={e.get('email') or '?'}  "
                      f"added={e.get('added_at', '?')}")
        return
    if action == "add":
        accounts_add(args)
        return
    if action == "remove":
        A.remove_entry(args.backend, args.name)
        print(f"removed {args.backend}/{args.name}")
        return
    if action == "rename":
        A.rename_entry(args.backend, args.old, args.new)
        import shutil
        from altlay.pool import load_state, save_state, vault_home
        old_dir = vault_home() / "profiles" / args.backend / args.old
        new_dir = vault_home() / "profiles" / args.backend / args.new
        if old_dir.is_dir() and not new_dir.exists():
            shutil.move(str(old_dir), str(new_dir))
            print(f"moved browser profile too")
        st = load_state()
        old_key, new_key = f"{args.backend}/{args.old}", f"{args.backend}/{args.new}"
        if old_key in st:
            st[new_key] = st.pop(old_key)
            save_state(st)
        cfg_default = A.get_default(args.backend)
        if cfg_default == args.old:
            A.set_default(args.backend, args.new)
        print(f"renamed {args.backend}/{args.old} -> {args.new}")
        return
    if action == "use":
        if args.name not in [e["name"] for e in A.entries(args.backend)]:
            print(f"error: no {args.backend} account {args.name!r}", file=sys.stderr)
            raise SystemExit(2)
        A.set_default(args.backend, args.name)
        print(f"default {args.backend} account: {args.name}")
        return
    if action == "check":
        backends = [args.backend] if args.backend else list(A.BACKENDS)
        failed = False
        for b in backends:
            for e in A.entries(b):
                ok, info = A.check_account(b, e)
                print(f"{b}/{e['name']}: {'OK' if ok else 'DEAD'} ({info})")
                failed = failed or not ok
        raise SystemExit(1 if failed else 0)
    if action == "import":
        n = accounts_import(args.file)
        print(f"imported {n} account(s)")


def accounts_add(args) -> None:
    from altlay import accounts as A
    if args.from_firefox:
        found = A.snapshot_firefox()
        if args.backend:
            found = [f for f in found if f["backend"] == args.backend]
        if not found:
            print("no logged-in sessions found in Firefox profiles")
            return
        for item in found:
            _store_snapshot(args, item)
        return
    if args.token:
        backend, session = A.parse_paste(args.token)
        if args.backend and backend != args.backend:
            print(f"error: pasted secret looks like {backend}, "
                  f"not {args.backend}", file=sys.stderr)
            raise SystemExit(2)
        email = A.IDENTIFY[backend](session)
        taken = [e["name"] for e in A.entries(backend)]
        name = args.name or A.suggest_alias(email, taken, backend)
        entry = {"name": name, "email": email, **session,
                 "added_at": _now_iso(), "source": "paste"}
        A.add_entry(backend, entry)
        ok, info = A.check_account(backend, entry)
        print(f"added {backend}/{name}  email={email or '?'}  check={'OK' if ok else 'DEAD: ' + info}")
        return
    print("error: specify --from-firefox or --token", file=sys.stderr)
    raise SystemExit(2)


def _store_snapshot(args, item: dict) -> None:
    from altlay import accounts as A
    backend = item["backend"]
    email = A.IDENTIFY[backend](item)
    taken = [e["name"] for e in A.entries(backend)]
    if email and any(e.get("email") == email for e in A.entries(backend)):
        print(f"skip {backend}: {email} already vaulted")
        return
    name = args.name or A.suggest_alias(
        email, taken, f"{backend}-c{item['container']}")
    entry = {"name": name, "email": email, "added_at": _now_iso(),
             "source": f"firefox:{item['profile']}#c{item['container']}"}
    if backend == "deepseek":
        entry.update({"userToken": item["userToken"], "device_id": item.get("device_id")})
    else:
        entry["cookies"] = item["cookies"]
    try:
        A.add_entry(backend, entry)
    except ValueError as e:
        print(f"skip: {e}")
        return
    ok, info = A.check_account(backend, entry)
    print(f"added {backend}/{name}  email={email or '?'}  check={'OK' if ok else 'DEAD: ' + info}")


def _now_iso() -> str:
    import datetime
    return datetime.datetime.now().isoformat(timespec="seconds")


def accounts_import(path: str) -> int:
    from altlay import accounts as A
    data = json.load(open(path))
    n = 0
    for backend, items in data.items():
        if backend not in A.BACKENDS:
            print(f"skip unknown backend {backend!r}")
            continue
        for item in items:
            name = item.get("name") or A.suggest_alias(
                None, [e["name"] for e in A.entries(backend)], backend)
            entry = {"name": name, "email": item.get("email"),
                     "added_at": _now_iso(), "source": f"import:{path}"}
            if "userToken" in item:
                entry.update({"userToken": item["userToken"],
                              "device_id": item.get("device_id")})
            elif "sessionKey" in item:
                entry["cookies"] = [{"name": "sessionKey", "value": item["sessionKey"],
                                     "domain": ".claude.ai", "path": "/",
                                     "expires": 9999999999.0,
                                     "httpOnly": True, "secure": True, "sameSite": "Lax"}]
            elif "cookies" in item:
                entry["cookies"] = item["cookies"]
            else:
                print(f"skip {backend}/{name}: no session material")
                continue
            try:
                A.add_entry(backend, entry)
                n += 1
            except ValueError as e:
                print(f"skip: {e}")
    return n


if __name__ == "__main__":
    sys.exit(main())
