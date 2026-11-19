"""M15 hooks in main.py, with fakes for the DB and Slack: without MUNITO_API_KEY the answers are
exactly as before; the weekly report goes only to a bot's own webhook; the admin page is off
without ADMIN_TOKEN."""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient  # noqa: E402
import main  # noqa: E402

HOOK = "https://hooks.slack.com/services/T0AAA/B0BBB/abcdEFGH1234wxyz"
OLD_HEALTH_KEYS = {"company", "learning_state", "ready_at", "threshold", "threshold_margin", "threshold_margin_pct",
                   "answers_total", "answers_24h", "baseline_review"}


class FakeConn:
    def __enter__(self): return self
    def __exit__(self, *a): return False


class Hooks(unittest.TestCase):
    def setUp(self):
        self.sent = []
        names = ("connection", "get_slack_webhook", "send_slack_text", "get_learning", "bot_counts",
                 "get_baseline_review", "OWNER_COMPANY_IDS")
        self.orig = {n: getattr(main, n) for n in names}
        main.connection = lambda *a, **k: FakeConn()
        main.get_slack_webhook = lambda conn, cid: HOOK if cid == 41 else None
        main.send_slack_text = lambda text, webhook=None: self.sent.append((webhook, text)) or True
        main.get_learning = lambda conn, cid: {"state": "ready"}
        main.bot_counts = lambda conn, cid: {"answers_total": 5, "answers_24h": 2}
        main.get_baseline_review = lambda conn, cid: None
        main.OWNER_COMPANY_IDS = {12}
        main._webhooks.clear()

    def tearDown(self):
        for n, f in self.orig.items():
            setattr(main, n, f)
        main._webhooks.clear()

    def test_weekly_goes_to_the_bots_own_slack_only(self):
        self.assertFalse(main._notify_own(12, "weekly"))          # owner's bot without its own webhook: nothing
        self.assertEqual(self.sent, [])
        self.assertTrue(main._notify(12, "digest"))               # (the existing routing still uses the owner's)
        self.sent.clear()
        self.assertTrue(main._notify_own(41, "weekly"))
        self.assertEqual(self.sent, [(HOOK, "weekly")])

    def test_health_without_key_is_unchanged(self):
        with mock.patch.dict(os.environ, {"MUNITO_FULL_COVERAGE": "1", "MUNITO_WEEKLY_REPORT": "1"}):
            os.environ.pop("MUNITO_API_KEY", None)
            with mock.patch.object(main.munito_coverage, "counts", side_effect=AssertionError("no query")), \
                 mock.patch.object(main.munito_weekly, "latest", side_effect=AssertionError("no query")):
                h = main._bot_health_for({"id": 41, "company_name": "Acme"})
        self.assertEqual(set(h), OLD_HEALTH_KEYS)

    def test_health_with_key_adds_the_munito_fields(self):
        with mock.patch.dict(os.environ, {"MUNITO_API_KEY": "k"}), \
             mock.patch.object(main.munito_coverage, "counts", return_value={"hours": 24}), \
             mock.patch.object(main.munito_weekly, "latest", return_value=None):
            h = main._bot_health_for({"id": 41, "company_name": "Acme"})
        self.assertEqual(set(h) - OLD_HEALTH_KEYS, {"checks", "weekly"})

    def test_incident_detail_without_key_has_no_topics_field(self):
        inc = {"id": 9, "opened_at": main.datetime(2026, 11, 12, tzinfo=main.timezone.utc), "closed_at": None,
               "severity": 2, "investigation": None}
        with mock.patch.object(main, "get_incident", return_value=dict(inc)), \
             mock.patch.object(main, "distance_series", return_value=[]), \
             mock.patch.object(main, "version_timeline", return_value=[]), \
             mock.patch.object(main.munito_topics, "names", side_effect=AssertionError("no query")), \
             mock.patch.dict(os.environ, {"MUNITO_TOPIC_NAMES": "1"}):
            os.environ.pop("MUNITO_API_KEY", None)
            d = main._incident_detail_for({"id": 41, "company_name": "Acme"}, 9)
        self.assertNotIn("new_topics", d)

    def test_admin_page_off_without_token(self):
        c = TestClient(main.app)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ADMIN_TOKEN", None)
            self.assertEqual(c.get("/admin/munito", headers={"X-Admin-Token": "x"}).status_code, 404)
        with mock.patch.dict(os.environ, {"ADMIN_TOKEN": "right-token"}):
            self.assertEqual(c.get("/admin/munito", headers={"X-Admin-Token": "wrong"}).status_code, 401)
            self.assertEqual(c.get("/admin/munito").status_code, 401)


if __name__ == "__main__":
    unittest.main()
