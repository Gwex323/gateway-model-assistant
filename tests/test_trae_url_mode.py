"""Regression coverage for normalized Trae URLs and base-address mode.

These tests verify serialized cache records, not real client request behavior.
All database contents and connection values are synthetic.
"""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import trae_adapter


BASE = "https://gateway.example/v1"
MODEL_ID = "repro-chat"


class TraeUrlModeTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "state.vscdb"
        self.target = trae_adapter.Target("Trae CN fixture", self.path, "Trae")

    def make_db(self, existing=None):
        builtin = {"name": "builtin-chat", "config_source": 1, "status": True}
        models = [builtin] + ([existing] if existing is not None else [])
        self.original = {"builder": copy.deepcopy(models), "chat_model": copy.deepcopy(models)}
        conn = sqlite3.connect(str(self.path))
        try:
            conn.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT)")
            for profile in ("profile.one.", "profile.two."):
                conn.execute("INSERT INTO ItemTable VALUES (?, ?)",
                             (profile + trae_adapter.MODEL_MAP_SUFFIX, json.dumps(self.original)))
            conn.commit()
        finally:
            conn.close()

    def plan(self, url, **kwargs):
        return trae_adapter.make_plan(self.target, url, "fixture-value", [MODEL_ID], **kwargs)

    def assert_base_mode(self, plan, expected=BASE):
        self.assertEqual(plan.changed_maps, 2)
        self.assertEqual(len(plan.payloads), 2)
        for payload in plan.payloads.values():
            obj = json.loads(payload)
            self.assertEqual(set(obj), {"builder", "chat_model"})
            for mode, models in obj.items():
                self.assertEqual(models[0], self.original[mode][0])
                imported = [m for m in models if m.get("custom_model_id") == MODEL_ID]
                self.assertEqual(len(imported), 1)
                self.assertEqual(imported[0]["base_url"], expected)
                self.assertIs(imported[0]["is_custom_base_url"], False)

    def test_new_models_use_base_mode_for_normalized_inputs(self):
        self.make_db()
        for url, expected in (
            (BASE, BASE),
            (BASE + "/", BASE),
            (BASE + "/chat/completions", BASE),
            (BASE + "/models", BASE),
            ("https://gateway.example", BASE),
            ("https://gateway.example/proxy/v2/chat/completions", "https://gateway.example/proxy/v2"),
        ):
            with self.subTest(url=url):
                plan = self.plan(url)
                self.assertEqual(plan.rows, [(MODEL_ID, "新增")])
                self.assert_base_mode(plan, expected)

    def legacy_model(self):
        return {
            "name": trae_adapter.CUSTOM_PREFIX + MODEL_ID,
            "custom_model_id": MODEL_ID,
            "provider": "custom_openai_compatible",
            "config_source": 3,
            "base_url": BASE,
            "is_custom_base_url": True,
            "display_name": "Existing label",
            "prompt_max_tokens": 4096,
            "max_tokens": 1024,
            "extra_bookkeeping": {"keep": True},
        }

    def test_explicit_update_corrects_legacy_mode_and_is_idempotent(self):
        existing = self.legacy_model()
        self.make_db(existing)
        plan = self.plan(BASE + "/chat/completions", update_existing=True)
        self.assertEqual(plan.rows, [(MODEL_ID, "更新")])
        self.assert_base_mode(plan)
        for payload in plan.payloads.values():
            for models in json.loads(payload).values():
                model = models[1]
                for field in ("display_name", "prompt_max_tokens", "max_tokens", "extra_bookkeeping"):
                    self.assertEqual(model[field], existing[field])
        # Persist only the synthetic preview so the second plan sees the result.
        conn = sqlite3.connect(str(self.path))
        try:
            for key, payload in plan.payloads.items():
                conn.execute("UPDATE ItemTable SET value=? WHERE key=?", (payload, key))
            conn.commit()
        finally:
            conn.close()
        again = self.plan(BASE, update_existing=True)
        self.assertFalse(again.changed)
        self.assertEqual(again.rows, [(MODEL_ID, "无变化")])

    def test_existing_record_still_requires_explicit_update(self):
        self.make_db(self.legacy_model())
        plan = self.plan(BASE + "/chat/completions")
        self.assertFalse(plan.changed)
        self.assertEqual(plan.rows, [(MODEL_ID, "保留已有")])

    def test_new_record_does_not_inherit_legacy_full_url_mode(self):
        existing = self.legacy_model()
        existing["custom_model_id"] = "another-chat"
        existing["name"] = trae_adapter.CUSTOM_PREFIX + "another-chat"
        self.make_db(existing)
        plan = self.plan(BASE)
        for payload in plan.payloads.values():
            for models in json.loads(payload).values():
                self.assertEqual(models[1], existing)
                self.assertEqual(models[2]["base_url"], BASE)
                self.assertIs(models[2]["is_custom_base_url"], False)
        self.assert_base_mode(plan)

    def test_default_exemplar_uses_base_address_mode(self):
        self.assertIs(trae_adapter._find_exemplar([])["is_custom_base_url"], False)


if __name__ == "__main__":
    unittest.main()
