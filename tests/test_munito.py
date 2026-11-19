"""Munito guard (munito.py). No network, no database: `post` and `db` are injected, and the
DB helpers are replaced by recorders. The key point: without MUNITO_API_KEY nothing happens."""
import json
import os
import sys
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import munito  # noqa: E402

QWEN = "qwen/qwen3.8-27b"
ALL_FLAGS = {env: "1" for env in munito.FEATURES.values() if env}
MSGS = [{"role": "system", "content": "You check one chatbot answer against the documents it was given."},
        {"role": "user", "content": "Context:\nReturns are accepted within 30 days with a receipt.\n\n"
                                    "Question:\nCan I return my phone after 10 days?\n\nAnswer:\nYes, within 30 days."}]


def boom(*a, **k):
    raise AssertionError("must not be called")


@contextmanager
def fake_db():
    yield object()


class Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body or {}, headers or {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


def ok_body(content='{"verdict": "supported"}', tin=104, tout=50, finish="stop"):
    return {"choices": [{"message": {"content": content}, "finish_reason": finish}],
            "usage": {"prompt_tokens": tin, "completion_tokens": tout}, "service_tier": "flex"}


class KeyMissing(unittest.TestCase):
    """Every Munito feature is skipped when MUNITO_API_KEY is not set: no network, no database."""

    def test_every_feature_skipped_without_key(self):
        env = dict(ALL_FLAGS)
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("MUNITO_API_KEY", None)
            for feature in munito.FEATURES:
                self.assertFalse(munito.enabled(feature), feature)
                with self.assertRaises(munito.Skipped):
                    munito.chat(feature, QWEN, MSGS, 300, post=boom, db=boom)

    def test_blank_key_is_missing(self):
        with mock.patch.dict(os.environ, {**ALL_FLAGS, "MUNITO_API_KEY": "   "}):
            with self.assertRaises(munito.Skipped):
                munito.chat("coverage", QWEN, MSGS, 300, post=boom, db=boom)

    def test_features_default_off_even_with_key(self):
        with mock.patch.dict(os.environ, {"MUNITO_API_KEY": "test"}):
            for env in ALL_FLAGS:
                os.environ.pop(env, None)
            for feature in ("coverage", "topics", "weekly"):
                self.assertFalse(munito.enabled(feature))
                with self.assertRaises(munito.Skipped):
                    munito.chat(feature, QWEN, MSGS, 300, post=boom, db=boom)

    def test_unknown_feature_and_model(self):
        with mock.patch.dict(os.environ, {**ALL_FLAGS, "MUNITO_API_KEY": "test"}):
            with self.assertRaises(munito.Skipped):
                munito.chat("nope", QWEN, MSGS, 300, post=boom, db=boom)
            with self.assertRaises(munito.Skipped):
                munito.chat("coverage", "some/other-model", MSGS, 300, post=boom, db=boom)


class Estimate(unittest.TestCase):
    def test_estimate_is_above_real_usage(self):
        # step 0 smoke test: a ~430-character prompt was 104 prompt tokens, 50 output tokens
        self.assertGreater(munito.estimate_input_tokens(MSGS), 60)
        real = munito.cost(QWEN, 104, 50)
        self.assertAlmostEqual(real, 0.00006936, places=9)
        big = [{"role": "user", "content": "x" * 4000}]
        self.assertGreaterEqual(munito.estimate_input_tokens(big), 4000 / 4)      # ~4 chars per token in practice
        self.assertGreater(munito.estimate(QWEN, big, 300), munito.cost(QWEN, 1000, 300))

    def test_billed_tier_price(self):
        self.assertGreater(munito.cost(QWEN, 1000, 100, "standard"), munito.cost(QWEN, 1000, 100, "flex"))
        self.assertEqual(munito.cost(QWEN, 1000, 100, "weird"), munito.cost(QWEN, 1000, 100, "priority"))


class Budget(unittest.TestCase):
    NOW = datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc)

    def test_daily_cap(self):
        with mock.patch.dict(os.environ, {"MUNITO_DAILY_USD": "1.00", "MUNITO_TOTAL_USD_CAP": "90"}):
            self.assertTrue(munito.decide({"total_usd": 5}, 0.99, 0.009, self.NOW)[0])
            ok, why = munito.decide({"total_usd": 5}, 0.995, 0.009, self.NOW)
            self.assertFalse(ok); self.assertIn("daily budget", why)

    def test_total_cap_counts_before_the_call(self):
        with mock.patch.dict(os.environ, {"MUNITO_DAILY_USD": "1.00", "MUNITO_TOTAL_USD_CAP": "90"}):
            ok, why = munito.decide({"total_usd": 89.9995}, 0, 0.001, self.NOW)
            self.assertFalse(ok); self.assertIn("total cap", why)
            self.assertTrue(munito.decide({"total_usd": 89.998}, 0, 0.001, self.NOW)[0])
        with mock.patch.dict(os.environ, {"MUNITO_TOTAL_USD_CAP": "95"}):      # only the owner raises it
            self.assertTrue(munito.decide({"total_usd": 89.9995}, 0, 0.001, self.NOW)[0])

    def test_defaults(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            for k in ("MUNITO_DAILY_USD", "MUNITO_TOTAL_USD_CAP", "MUNITO_CREDIT_USD"):
                os.environ.pop(k, None)
            self.assertEqual((munito.daily_cap(), munito.total_cap(), munito.credit()), (1.0, 90.0, 100.0))

    def test_stopped_and_paused(self):
        self.assertFalse(munito.decide({"total_usd": 0, "stopped_reason": "402"}, 0, 0.0001, self.NOW)[0])
        paused = {"total_usd": 0, "paused_day": self.NOW.date(), "paused_reason": "spend limit"}
        self.assertFalse(munito.decide(paused, 0, 0.0001, self.NOW)[0])
        yesterday = dict(paused, paused_day=datetime(2026, 11, 18).date())
        self.assertTrue(munito.decide(yesterday, 0, 0.0001, self.NOW)[0])


class Errors(unittest.TestCase):
    def test_actions(self):
        self.assertEqual(munito.error_action(402, '{"error": {"message": "Paid access is paused"}}')[0], "stop")
        self.assertEqual(munito.error_action(401, "{}")[0], "stop")
        self.assertEqual(munito.error_action(429, json.dumps({"error": {"code": "free_daily_limit_exceeded"}}))[0], "stop")
        self.assertEqual(munito.error_action(429, json.dumps({"error": {"code": "organization_spend_limit_exceeded"}}))[0], "pause")
        self.assertEqual(munito.error_action(429, json.dumps({"error": {"type": "insufficient_quota"}}))[0], "pause")
        self.assertIsNone(munito.error_action(429, json.dumps({"error": {"code": "rate_limit_exceeded"}}))[0])
        self.assertIsNone(munito.error_action(503, "not json")[0])
        self.assertEqual(munito.error_action(403, '{"error": {"message": "Account is not active"}}')[0], "stop")

    def test_tier_names(self):
        self.assertEqual(munito.tier_name(QWEN, "30"), "Tier 1")
        self.assertEqual(munito.tier_name(QWEN, 15), "Free")
        self.assertEqual(munito.tier_name("deepseek-ai/deepseek-v4.1-flash-1017", "400"), "Tier 1")
        self.assertEqual(munito.tier_name(QWEN, 500), "above Tier 4")
        self.assertIsNone(munito.tier_name(QWEN, None))


class Chat(unittest.TestCase):
    """The call path with the DB helpers recorded instead of run."""

    def setUp(self):
        self.calls = []
        p = mock.patch.dict(os.environ, {**ALL_FLAGS, "MUNITO_API_KEY": "test-key"})
        p.start(); self.addCleanup(p.stop)
        for name in ("reserve", "settle", "record_error", "record_tier"):
            def rec(*a, _n=name, **k):
                self.calls.append((_n, a[1:], k))
                return (True, "") if _n == "reserve" else None
            q = mock.patch.object(munito, name, rec)
            q.start(); self.addCleanup(q.stop)

    def test_success_books_estimate_then_real_cost(self):
        sent = {}

        def post(url, json=None, headers=None, timeout=None):
            sent.update(url=url, body=json, headers=headers)
            return Resp(200, ok_body(), {"munito-service-tier": "flex", "x-ratelimit-limit-requests": "30"})

        content, info = munito.chat("coverage", QWEN, MSGS, 5000, post=post, db=fake_db)
        self.assertEqual(content, '{"verdict": "supported"}')
        self.assertEqual(sent["headers"]["munito-service-tier"], "flex")
        self.assertEqual(sent["body"]["max_tokens"], munito.MAX_OUTPUT_CAP)               # capped
        self.assertEqual(sent["body"]["chat_template_kwargs"], {"enable_thinking": False})  # reasoning off
        self.assertTrue(sent["url"].startswith("https://api.munito.ai/inference/v1/"))
        names = [c[0] for c in self.calls]
        self.assertEqual(names[0], "reserve"); self.assertIn("settle", names)
        est = self.calls[0][1][2]
        settle = next(c for c in self.calls if c[0] == "settle")[1]
        self.assertAlmostEqual(settle[3], munito.cost(QWEN, 104, 50)); self.assertGreater(est, settle[3])
        self.assertIn(("record_tier", ("Tier 1",), {}), self.calls)
        self.assertAlmostEqual(info["usd"], munito.cost(QWEN, 104, 50))

    def test_reserve_refused_means_no_call(self):
        with mock.patch.object(munito, "reserve", lambda *a, **k: (False, "daily budget used")):
            with self.assertRaises(munito.Skipped):
                munito.chat("coverage", QWEN, MSGS, 300, post=boom, db=fake_db)

    def test_402_stops_everything_and_refunds_the_estimate(self):
        post = lambda *a, **k: Resp(402, {"error": {"message": "Paid access is paused while we review a payment."}})
        with self.assertRaises(munito.Unavailable):
            munito.chat("coverage", QWEN, MSGS, 300, post=post, db=fake_db)
        settle = next(c for c in self.calls if c[0] == "settle")[1]
        self.assertEqual(settle[3], 0.0)
        err = next(c for c in self.calls if c[0] == "record_error")[1]
        self.assertEqual(err[0], "stop")

    def test_network_error_keeps_the_estimate(self):
        def post(*a, **k):
            raise TimeoutError("slow")
        with self.assertRaises(munito.Unavailable):
            munito.chat("weekly", "deepseek-ai/deepseek-v4.1-flash-1017", MSGS, 400, post=post, db=fake_db)
        self.assertNotIn("settle", [c[0] for c in self.calls])          # booked estimate stays

    def test_tls_failure_was_never_sent_so_it_is_refunded(self):
        class SSLError(Exception):
            pass

        def post(*a, **k):
            raise SSLError("unable to get local issuer certificate")
        with self.assertRaises(munito.Unavailable):
            munito.chat("coverage", QWEN, MSGS, 300, post=post, db=fake_db)
        settle = next(c for c in self.calls if c[0] == "settle")[1]
        self.assertEqual(settle[3], 0.0)

    def test_cut_output_is_unavailable_but_paid(self):
        post = lambda *a, **k: Resp(200, ok_body(content=None, tout=300, finish="length"))
        with self.assertRaises(munito.Unavailable):
            munito.chat("topics", "deepseek-ai/deepseek-v4.1-flash-1017", MSGS, 300, post=post, db=fake_db)
        self.assertIn("settle", [c[0] for c in self.calls])


class Coverage(unittest.TestCase):
    """Feature 1: answers with context the sample did not pick; same prompt and labels."""

    def keys(self):
        import spotcheck
        picked = next(f"r{i}" for i in range(1000) if spotcheck.sampled(5, f"r{i}"))
        unpicked = next(f"r{i}" for i in range(1000) if not spotcheck.sampled(5, f"r{i}"))
        return picked, unpicked

    def test_skipped_without_key(self):
        import munito_coverage as mc
        picked, unpicked = self.keys()
        with mock.patch.dict(os.environ, ALL_FLAGS):
            os.environ.pop("MUNITO_API_KEY", None)
            self.assertFalse(mc.wanted(5, unpicked, "Returns within 30 days."))
            with mock.patch.object(mc.threading, "Thread", boom):
                self.assertFalse(mc.submit(1, 5, "q", "a", "ctx"))

    def test_only_unpicked_answers_with_context(self):
        import munito_coverage as mc
        picked, unpicked = self.keys()
        with mock.patch.dict(os.environ, {**ALL_FLAGS, "MUNITO_API_KEY": "k"}):
            self.assertTrue(mc.wanted(5, unpicked, "Returns within 30 days."))
            self.assertFalse(mc.wanted(5, picked, "Returns within 30 days."))     # the sample stays Groq-only
            self.assertFalse(mc.wanted(5, unpicked, None))
            self.assertFalse(mc.wanted(5, unpicked, "   "))
            os.environ["MUNITO_FULL_COVERAGE"] = "0"
            self.assertFalse(mc.wanted(5, unpicked, "Returns within 30 days."))

    def test_same_prompt_schema_and_labels_as_the_sample(self):
        import munito_coverage as mc
        import spotcheck
        seen = {}

        def chat(model, msgs):
            seen.update(model=model, msgs=msgs)
            return json.dumps({"verdict": "not_supported", "reason": "60 days, not 30", "quote": "60 days"}), 120

        r, why = mc.check("Return window?", "You have 60 days.", "Returns within 30 days.", chat=chat)
        self.assertEqual(seen["msgs"], spotcheck.messages("Return window?", "You have 60 days.", "Returns within 30 days.")[1])
        self.assertEqual(seen["model"], "qwen/qwen3.8-27b")
        self.assertEqual((r["verdict"], r["likely_problem"], r["source"]), ("not_supported", True, "munito_full_coverage"))
        self.assertEqual(r["quote"], "60 days")

    def test_munito_adapter_uses_the_spotcheck_schema(self):
        import munito_coverage as mc
        import spotcheck
        got = {}

        def fake_chat(feature, model, msgs, max_tokens, schema=None, schema_name=None, **k):
            got.update(feature=feature, schema=schema, max_tokens=max_tokens)
            return '{"verdict": "supported", "reason": "ok", "quote": ""}', {"input_tokens": 100, "output_tokens": 30}

        with mock.patch.object(munito, "chat", fake_chat):
            text, tokens = mc.munito_json("qwen/qwen3.8-27b", [])
        self.assertEqual((got["feature"], got["schema"], got["max_tokens"], tokens), ("coverage", spotcheck.SCHEMA, 300, 130))

    def test_save_writes_coverage_check_only(self):
        import munito_coverage as mc
        sql = []

        class Cur:
            def execute(self, q, args=None):
                sql.append(q)

        class Conn:
            def cursor(self):
                return Cur()

        mc.save(Conn(), 7, {"verdict": "supported"})
        self.assertIn("coverage_check", sql[0]); self.assertNotIn("spot_check", sql[0])


class Topics(unittest.TestCase):
    """Feature 2: names for clusters the investigation already marked as a new topic."""

    def test_skipped_without_key(self):
        import munito_topics as mt
        with mock.patch.dict(os.environ, ALL_FLAGS):
            os.environ.pop("MUNITO_API_KEY", None)
            with mock.patch.object(mt.threading, "Thread", boom):
                self.assertFalse(mt.submit(9, 12))
            import database
            with mock.patch.object(database, "connection", boom):
                self.assertEqual(mt.run(9, 12), [])

    def test_flag_off_with_key(self):
        import munito_topics as mt
        with mock.patch.dict(os.environ, {"MUNITO_API_KEY": "k", "MUNITO_TOPIC_NAMES": "0"}):
            with mock.patch.object(mt.threading, "Thread", boom):
                self.assertFalse(mt.submit(9, 12))

    def test_only_clusters_already_marked_new_topic(self):
        import munito_topics as mt
        pack = {"evidence": [{"kind": "cluster", "id": "C1", "new_topic": True},
                             {"kind": "cluster", "id": "C2", "new_topic": False},
                             {"kind": "stat", "id": "S1"}]}
        self.assertEqual([c["id"] for c in mt.new_topic_clusters(pack)], ["C1"])
        self.assertEqual(mt.new_topic_clusters(None), [])

    def test_name_and_clean(self):
        import munito_topics as mt
        seen = {}

        def chat(msgs):
            seen["msgs"] = msgs
            return '{"topic": "  shipping to Canada. "}'

        self.assertEqual(mt.name(["Do you ship to Canada?", "Customs fees for Toronto?"], chat=chat), ("Shipping to Canada", None))
        self.assertIn("<user_text>Do you ship to Canada?</user_text>", seen["msgs"][1]["content"])
        self.assertIsNone(mt.clean('{"topic": ""}'))
        self.assertIsNone(mt.clean('{"topic": "' + "word " * 12 + '"}'))
        self.assertIsNone(mt.clean("not json"))
        self.assertIsNone(mt.clean('{"topic": "<b>x</b>"}'))
        self.assertEqual(mt.name([], chat=chat), (None, "no example questions"))


if __name__ == "__main__":
    unittest.main()
