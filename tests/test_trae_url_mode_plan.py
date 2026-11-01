"""Local-cache plan regressions; these do not establish real client semantics."""
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import trae_adapter


class TraeUrlModePlanTests(unittest.TestCase):
    cases = (
        ("https://gateway.example/v1", "https://gateway.example/v1"),
        ("https://gateway.example/v1/chat/completions", "https://gateway.example/v1"),
        ("https://gateway.example/api/openai/v1", "https://gateway.example/api/openai/v1"),
        ("https://gateway.example/api/openai/v1/chat/completions", "https://gateway.example/api/openai/v1"),
        ("https://gateway.example/v1/models", "https://gateway.example/v1"),
        ("https://gateway.example", "https://gateway.example/v1"),
    )

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "state.vscdb"
        self.map_key = "fixture." + trae_adapter.MODEL_MAP_SUFFIX
        with sqlite3.connect(self.database) as connection:
            connection.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT)")
        self.target = trae_adapter.Target("Trae fixture", self.database, "Trae")
        self.builtin = {"name": "builtin-chat", "config_source": 1, "base_url": "", "is_custom_base_url": True}

    def seed(self, existing=None):
        models = [self.builtin] + ([] if existing is None else [existing])
        value = json.dumps({"builder": models, "chat": copy.deepcopy(models)})
        with sqlite3.connect(self.database) as connection:
            connection.execute("INSERT OR REPLACE INTO ItemTable VALUES (?, ?)", (self.map_key, value))

    def stored(self):
        with sqlite3.connect(self.database) as connection:
            return connection.execute("SELECT value FROM ItemTable WHERE key=?", (self.map_key,)).fetchone()[0]

    def existing(self, url):
        return {
            "name": trae_adapter.CUSTOM_PREFIX + "fixture-chat",
            "custom_model_id": "fixture-chat",
            "provider": "custom_openai_compatible",
            "config_source": 3,
            "base_url": url,
            "is_custom_base_url": True,
            "ak": "fixture-value",
            "display_name": "Saved display",
            "max_tokens": 1234,
            "client_extra": {"keep": True},
        }

    def plan(self, url, **options):
        return trae_adapter.make_plan(self.target, url, "fixture-value", ["fixture-chat"], **options)

    def assert_output(self, plan, root, action):
        self.assertEqual(plan.rows, [("fixture-chat", action)])
        self.assertEqual(plan.changed_maps, 1)
        obj = json.loads(plan.payloads[self.map_key])
        self.assertEqual(set(obj), {"builder", "chat"})
        for models in obj.values():
            self.assertEqual(len(models), 2)
            self.assertEqual(models[0], self.builtin)
            self.assertEqual(models[1]["custom_model_id"], "fixture-chat")
            self.assertEqual(models[1]["base_url"], root)
            self.assertIs(models[1]["is_custom_base_url"], False)
        return obj

    def test_add_matrix_uses_base_address_mode_in_all_lists(self):
        for url, root in self.cases:
            with self.subTest(input_url=url):
                self.seed()
                before = self.stored()
                self.assert_output(self.plan(url), root, "新增")
                self.assertEqual(self.stored(), before)

    def test_update_matrix_repairs_base_and_full_address_records(self):
        for url, root in self.cases:
            for old_url in (root, root + "/chat/completions"):
                with self.subTest(input_url=url, stored_url=old_url):
                    self.seed(self.existing(old_url))
                    before = self.stored()
                    obj = self.assert_output(self.plan(url, update_existing=True), root, "更新")
                    for models in obj.values():
                        self.assertEqual(models[1]["display_name"], "Saved display")
                        self.assertEqual(models[1]["max_tokens"], 1234)
                        self.assertEqual(models[1]["client_extra"], {"keep": True})
                    self.assertEqual(self.stored(), before)

    def test_no_update_preserves_existing_full_address_during_add(self):
        for url, root in self.cases:
            with self.subTest(input_url=url):
                existing = self.existing(root + "/chat/completions")
                self.seed(existing)
                before = self.stored()
                plan = trae_adapter.make_plan(
                    self.target, url, "fixture-value", ["fixture-chat", "fixture-new"]
                )
                self.assertEqual(plan.rows, [("fixture-chat", "保留已有"), ("fixture-new", "新增")])
                for models in json.loads(plan.payloads[self.map_key]).values():
                    self.assertEqual(models[:2], [self.builtin, existing])
                    self.assertEqual(models[2]["base_url"], root)
                    self.assertIs(models[2]["is_custom_base_url"], False)
                self.assertEqual(self.stored(), before)

    def test_update_does_not_repoint_same_id_on_different_subpath(self):
        for stored_root, input_root in (
            ("/v1", "/api/openai/v1"), ("/api/openai/v1", "/v1")
        ):
            with self.subTest(stored_root=stored_root, input_root=input_root):
                self.seed(self.existing("https://gateway.example" + stored_root + "/chat/completions"))
                before = self.stored()
                plan = self.plan("https://gateway.example" + input_root, update_existing=True)
                self.assertEqual(plan.rows, [("fixture-chat", "保留其他连接")])
                self.assertFalse(plan.changed)
                self.assertEqual(plan.payloads, {})
                self.assertEqual(self.stored(), before)


if __name__ == "__main__":
    unittest.main()
