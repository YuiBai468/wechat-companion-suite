# -*- coding: utf-8 -*-
"""Portable backend checks for the public suite; no network, no real data."""
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DESKTOP = ROOT / "desktop"
for path in (DESKTOP, DESKTOP / "video", DESKTOP / "isolated-desktop"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import config_loader  # noqa: E402
import companion_diagnostics  # noqa: E402
import companion_planner  # noqa: E402
import companion_organizer  # noqa: E402
import companion_insights  # noqa: E402
import group_behavior  # noqa: E402

TODAY = "2026-11-03"


def sample_day(prefix="old"):
    timeline = []
    for hour in range(24):
        timeline.append({"from": "%02d:00" % hour,
                         "to": "%02d:00" % (hour + 1),
                         "activity": "%s-%d" % (prefix, hour),
                         "place": "home", "detail": ""})
    timeline[-1]["to"] = "24:00"
    timeline[0]["extra"] = {"preserved": True}
    return {"date": TODAY, "timeline": timeline}


class FakeManager:
    def __init__(self):
        self._lock = threading.RLock()
        self._job = None
        self._on_success = None

    def public(self, date=None):
        return {"state": "running" if self._job else "idle"}


class ConfigTest(unittest.TestCase):
    def test_env_refs_and_relative_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({
                "apiKey": "${TEST_SUITE_KEY}",
                "model": "example-model",
                "contextFile": "data/contexts.json",
                "chats": [],
            }), encoding="utf-8")
            old = os.environ.get("TEST_SUITE_KEY")
            try:
                os.environ["TEST_SUITE_KEY"] = "not-a-real-key"
                cfg = config_loader.load_config(path, {}, ROOT)
            finally:
                if old is None:
                    os.environ.pop("TEST_SUITE_KEY", None)
                else:
                    os.environ["TEST_SUITE_KEY"] = old
            self.assertEqual(cfg["apiKey"], "not-a-real-key")
            self.assertTrue(os.path.isabs(cfg["contextFile"]))
            self.assertTrue(cfg["contextFile"].endswith(os.path.join("data", "contexts.json")))

    def test_check_config_does_not_echo_secret_values(self):
        warnings = config_loader.check_config({
            "model": "m", "baseUrl": "https://example.invalid", "chats": [],
            "apiKey": "hidden-value", "health": {"enabled": True, "token": ""},
        })
        self.assertTrue(any("token" in item for item in warnings))
        self.assertFalse(any("hidden-value" in item for item in warnings))


class DiagnosticsTest(unittest.TestCase):
    def test_empty_store_is_truthy_and_first_message_counts(self):
        store = companion_diagnostics.DiagnosticsStore()
        self.assertTrue(store)
        self.assertEqual(len(store), 0)
        first = store.start("sample-chat", "sample-member", "hello")
        self.assertIsNotNone(first)
        self.assertEqual(len(store), 1)
        data = store.list()
        self.assertEqual(data["counts"]["received"], 1)
        store.send(first, True)
        data = store.list()
        self.assertEqual(data["counts"]["received"], 1)
        self.assertEqual(data["counts"]["sent"], 1)


class GroupTest(unittest.TestCase):
    def test_followup_needs_fresh_target_reply(self):
        decision = group_behavior.decide_group(
            None, at_self=False, followup_active=True,
            followup_renewable=True, cooldown_remaining=100,
            now=1000, last_bot_at=990, random_value=0.99)
        self.assertTrue(decision["trigger"])
        exhausted = group_behavior.decide_group(
            None, at_self=False, followup_active=True,
            followup_renewable=False, now=1000, last_bot_at=990,
            random_value=0.99)
        self.assertFalse(exhausted["trigger"])


class PlannerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.current = sample_day()
        self.manager = FakeManager()
        self.planner = companion_planner.CompanionPlanner(
            self.manager,
            lambda _date: json.loads(json.dumps(self.current)),
            self._save,
            lambda *a, **k: sample_day("new"),
            lambda date: os.path.join(self.temp.name, date + ".json"),
            os.path.join(self.temp.name, "planner"),
            lambda: TODAY,
            os.path.join(self.temp.name, "backup"),
        )

    def _save(self, day):
        self.current = json.loads(json.dumps(day))

    def test_initial_version_and_full_field_preservation(self):
        first = self.planner.get()
        self.assertTrue(first["versions"])
        merged, _ = companion_planner.merge_timeline(
            sample_day(), sample_day("new"), 300, [])
        self.assertEqual(merged["timeline"][0], sample_day()["timeline"][0])

    def test_strict_clock_and_non_dict_rejected(self):
        self.assertFalse(companion_planner._valid_timeline([
            {"from": "00:00", "to": "00:60"}, {"from": "01:00", "to": "24:00"}]))
        self.assertFalse(companion_planner._valid_timeline([
            {"from": "00:00", "to": "12:00"}, "not-a-dict"]))


class OrganizerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.mem = {"sample-chat": "old"}
        self.promises = [{"id": "p1", "chat": "sample-chat",
                          "promise": "past", "due": "2025-01-01",
                          "status": "open", "legacy": False}]
        self.store = companion_organizer.OrganizerStore(
            os.path.join(self.temp.name, "organizer.json"),
            lambda c: self.mem.get(c, ""),
            lambda c, t: self.mem.__setitem__(c, t),
            lambda: json.loads(json.dumps(self.promises)),
            lambda x: setattr(self, "promises", json.loads(json.dumps(x))),
            os.path.join(self.temp.name, "backups"),
            today_provider=lambda: TODAY,
            chats_provider=lambda: [{"name": "sample-chat", "kind": "group"},
                                    {"name": "other-chat", "kind": "group"}],
        )

    def test_memory_revision_conflict_and_expired_promise(self):
        view = self.store.get("sample-chat")
        self.assertEqual(self.store.promises_for("sample-chat", TODAY), [])
        self.mem["sample-chat"] = "new background"
        with self.assertRaises(companion_organizer.OrganizerConflict):
            self.store.post("memory_save", "sample-chat", view["revision"],
                            text="stale draft")
        self.assertEqual(self.mem["sample-chat"], "new background")


class ModelsTest(unittest.TestCase):
    def test_routes_follow_live_config(self):
        cfg = {"model": "example", "thinking": "max",
               "image": {"enabled": False, "model": "image-model"}}
        service = companion_insights.ModelsService(None, lambda: cfg)
        routes = {item["id"]: item for item in service.routes()}
        self.assertEqual(routes["primary_chat"]["effort"], "max")
        self.assertFalse(routes["image"]["enabled"])
        cfg["thinking"] = "low"
        routes = {item["id"]: item for item in service.routes()}
        self.assertEqual(routes["primary_chat"]["effort"], "low")


class PrivacyStaticTest(unittest.TestCase):
    def test_native_and_python_sources_have_no_machine_paths(self):
        import re
        offenders = []
        drive_path = re.compile(r"[A-Za-z]:[\\/](?:Users|Python|llm|work)")
        for pattern in ("*.py", "*.ps1", "*.cs", "*.cmd"):
            for path in (DESKTOP.rglob(pattern)):
                if path.name == "image_prompting.py":
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                if drive_path.search(text):
                    offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])

    def test_mobile_health_defaults_are_off_and_empty(self):
        java = (ROOT / "mobile/gadgetbridge/app/src/main/java/nodomain/"
                "freeyourgadget/gadgetbridge/util/HealthPush.java").read_text(
                    encoding="utf-8")
        self.assertIn("PREF_ENABLED, false", java)
        self.assertIn("PREF_URL, \"\"", java)
        self.assertIn("PREF_TOKEN, \"\"", java)


if __name__ == "__main__":
    unittest.main(verbosity=2)
