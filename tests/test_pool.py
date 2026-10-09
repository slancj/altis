"""Pool rotation / cooldown / exhaustion logic (no browsers, no network)."""

import os
import tempfile
import time
import unittest

os.environ["ALTIS_HOME"] = tempfile.mkdtemp(prefix="altis-test")

from altis.pool import (  # noqa: E402
    AccountDead,
    AccountPool,
    AllLimited,
    RateLimited,
    parse_reset_time,
)


def _seed():
    from altis.pool import _save_json
    _save_json("accounts.json", {"claude": [
        {"name": "a1", "cookies": []},
        {"name": "a2", "cookies": []},
    ]})
    _save_json("state.json", {})


class PoolTest(unittest.TestCase):
    def setUp(self):
        _seed()

    def test_fixed_order_first_healthy_wins(self):
        pool = AccountPool("claude")
        self.assertEqual(pool.run(lambda n, e: n), "a1")

    def test_failover_on_limit(self):
        pool = AccountPool("claude")
        calls = []

        def attempt(name, entry):
            calls.append(name)
            if name == "a1":
                raise RateLimited(None, "limit")
            return name

        self.assertEqual(pool.run(attempt), "a2")
        self.assertEqual(calls, ["a1", "a2"])
        # a1 now on cooldown, skipped next round
        self.assertEqual([a["name"] for a in pool.healthy()], ["a2"])

    def test_cooldown_expiry(self):
        from altis.pool import load_state, save_state
        pool = AccountPool("claude")
        pool.mark_limited("a1", time.time() + 1000)
        self.assertEqual([a["name"] for a in pool.healthy()], ["a2"])
        st = load_state()
        st["claude/a1"]["cooldown_until"] = time.time() - 1  # expire it
        save_state(st)
        pool2 = AccountPool("claude")
        self.assertEqual([a["name"] for a in pool2.healthy()], ["a1", "a2"])

    def test_all_limited_raises(self):
        pool = AccountPool("claude")
        with self.assertRaises(AllLimited):
            pool.run(lambda n, e: (_ for _ in ()).throw(RateLimited(None)))

    def test_dead_account_skipped_and_benched(self):
        pool = AccountPool("claude")

        def attempt(name, entry):
            if name == "a1":
                raise AccountDead("logged out")
            return name

        self.assertEqual(pool.run(attempt), "a2")
        self.assertEqual([a["name"] for a in pool.healthy()], ["a2"])

    def test_pinned_order(self):
        pool = AccountPool("claude")
        self.assertEqual(pool.run(lambda n, e: n, order=["a2", "a1"]), "a2")

    def test_parse_reset_time(self):
        base = time.mktime((2026, 10, 9, 15, 0, 0, 0, 0, -1))
        ts = parse_reset_time("limit reached, try again until 4:00 PM", base)
        day = time.localtime(ts)
        self.assertEqual((day.tm_hour, day.tm_min), (16, 0))
        ts2 = parse_reset_time("quota until 16:05", base)
        day2 = time.localtime(ts2)
        self.assertEqual((day2.tm_hour, day2.tm_min), (16, 5))
        self.assertIsNone(parse_reset_time("no time here", base))

    def test_state_persists_across_instances(self):
        pool = AccountPool("claude")
        pool.mark_limited("a1", time.time() + 5000)
        pool2 = AccountPool("claude")  # fresh instance, same HOME
        self.assertEqual([a["name"] for a in pool2.healthy()], ["a2"])


class AccountsTest(unittest.TestCase):
    def test_crud_and_defaults(self):
        from altis import accounts as A
        A.save_vault({})
        A.add_entry("claude", {"name": "x", "cookies": []})
        self.assertEqual([e["name"] for e in A.entries("claude")], ["x"])
        with self.assertRaises(ValueError):
            A.add_entry("claude", {"name": "x", "cookies": []})
        A.rename_entry("claude", "x", "y")
        self.assertEqual([e["name"] for e in A.entries("claude")], ["y"])
        A.set_default("claude", "y")
        self.assertEqual(A.get_default("claude"), "y")
        self.assertEqual(A.resolve_name("claude", None), "y")
        self.assertEqual(A.resolve_name("claude", "y"), "y")
        A.remove_entry("claude", "y")
        self.assertEqual(A.entries("claude"), [])

    def test_suggest_alias(self):
        from altis import accounts as A
        self.assertEqual(A.suggest_alias("Foo.Bar+1@gmail.com", [], "x"), "foobar+1")
        self.assertEqual(A.suggest_alias(None, [], "c9"), "c9")
        self.assertEqual(A.suggest_alias("a@b.c", ["a"], "x"), "a-2")


if __name__ == "__main__":
    unittest.main()
