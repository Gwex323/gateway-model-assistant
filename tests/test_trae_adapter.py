import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core
import trae_adapter

BASE = 'https://gateway.example/v1'
KEY = 'test-only-trae-key'


def make_db(path):
    con = sqlite3.connect(path)
    con.execute('CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT)')
    exemplar = {
        'name': 'custom_openai_compatible//old', 'display_name': 'old',
        'custom_model_id': 'old', 'provider': 'custom_openai_compatible',
        'config_source': 3, 'base_url': 'https://old.invalid/v1/chat/completions',
        'ak': 'old-key', 'sk': '', 'is_custom_base_url': True,
        'auth_type': 0, 'status': True, 'selectable': True,
        'client_connect': True, 'model_type': 'chat_model', 'multimodal': False,
    }
    builtin = {'name': 'builtin', 'config_source': 1, 'provider': None}
    deepseek = {
        'name': 'deepseek//other', 'display_name': 'DeepSeek Other',
        'custom_model_id': '2600419074', 'provider': 'deepseek',
        'config_source': 3, 'base_url': 'https://old.invalid/v1/chat/completions',
        'ak': 'old-key', 'sk': '',
    }
    sk_only = {
        'name': 'volcengine-plan//sk-only', 'display_name': 'Volc SK',
        'custom_model_id': '2600419075', 'provider': 'volcengine-plan',
        'config_source': 3, 'base_url': 'https://volc.invalid/v1/chat/completions',
        'ak': '', 'sk': 'sk-fallback-key',
    }
    private_http = {
        'name': 'custom_openai_compatible//lan-only', 'display_name': 'LAN Only',
        'custom_model_id': '2600419076', 'provider': 'custom_openai_compatible',
        'config_source': 3, 'base_url': 'http://172.16.116.14/v1/chat/completions',
        'ak': 'lan-test-key', 'sk': '',
    }
    maps = {
        'builder': [builtin, exemplar, deepseek, sk_only, private_http],
        'solo_coder': [builtin, exemplar, deepseek, sk_only, private_http],
    }
    con.execute('INSERT INTO ItemTable VALUES (?, ?)', ('demo_AI.agent.model.model_list_map', json.dumps(maps)))
    con.commit(); con.close()


class TraeAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='gateway-trae-test-')
        self.path = Path(self.temp.name) / 'state.vscdb'
        make_db(self.path)
        self.target = trae_adapter.Target('Test Trae', self.path, 'Trae')
        self.stop = patch.object(trae_adapter, 'ensure_trae_stopped')
        self.stop.start()
        self.crypto = patch.object(core, 'dpapi', side_effect=lambda data, decrypt=False: data[::-1])
        self.crypto.start()

    def tearDown(self):
        self.crypto.stop(); self.stop.stop(); self.temp.cleanup()

    def read_map(self):
        con = sqlite3.connect(self.path)
        value = con.execute('SELECT value FROM ItemTable').fetchone()[0]
        con.close(); return json.loads(value)

    def test_plan_adds_to_all_modes_and_preserves_builtin(self):
        plan = trae_adapter.make_plan(
            self.target, BASE, KEY, ['new'],
            token_limits={'maxInputTokens': 128000, 'maxOutputTokens': 8192},
        )
        self.assertTrue(plan.changed)
        self.assertEqual(plan.rows, [('new', '新增')])
        maps = plan.payloads['demo_AI.agent.model.model_list_map']
        obj = json.loads(maps)
        for models in obj.values():
            imported = [m for m in models if m.get('custom_model_id') == 'new']
            self.assertEqual(len(imported), 1)
            self.assertEqual(imported[0]['ak'], KEY)
            self.assertEqual(imported[0]['base_url'], BASE)
            self.assertEqual(imported[0]['prompt_max_tokens'], 128000)
            self.assertEqual(imported[0]['max_tokens'], 8192)
        self.assertEqual(sum(m.get('config_source') == 1 for m in obj['builder']), 1)
        self.assertNotIn(KEY, repr(plan))

    def test_plan_uses_per_model_token_limits(self):
        plan = trae_adapter.make_plan(
            self.target, BASE, KEY, ['model-a', 'model-b'],
            token_limits_by_model={
                'model-a': {'maxInputTokens': 128000, 'maxOutputTokens': 8192},
                'model-b': {'maxInputTokens': 64000, 'maxOutputTokens': 4096},
            },
        )
        obj = json.loads(plan.payloads['demo_AI.agent.model.model_list_map'])
        for models in obj.values():
            imported = {m.get('custom_model_id'): m for m in models}
            self.assertEqual(imported['model-a']['prompt_max_tokens'], 128000)
            self.assertEqual(imported['model-a']['max_tokens'], 8192)
            self.assertEqual(imported['model-b']['prompt_max_tokens'], 64000)
            self.assertEqual(imported['model-b']['max_tokens'], 4096)

    def test_existing_custom_is_preserved_without_update(self):
        plan = trae_adapter.make_plan(self.target, 'https://old.invalid/v1', KEY, ['old'])
        self.assertFalse(plan.changed)
        self.assertEqual(plan.rows, [('old', '保留已有')])
        builder = self.read_map()['builder']
        old = next(m for m in builder if m.get('custom_model_id') == 'old')
        self.assertEqual(old['ak'], 'old-key')

    def test_update_changes_only_custom_model(self):
        plan = trae_adapter.make_plan(
            self.target, 'https://old.invalid/v1', KEY, ['old'], update_existing=True,
        )
        obj = json.loads(plan.payloads['demo_AI.agent.model.model_list_map'])
        old = next(m for m in obj['builder'] if m.get('custom_model_id') == 'old')
        self.assertEqual(old['ak'], KEY)
        self.assertEqual(next(m for m in obj['builder'] if m.get('name') == 'builtin')['config_source'], 1)

    def test_update_preserves_unspecified_token_limits(self):
        con = sqlite3.connect(self.path)
        maps = json.loads(con.execute('SELECT value FROM ItemTable').fetchone()[0])
        for models in maps.values():
            old = next(m for m in models if m.get('custom_model_id') == 'old')
            old['prompt_max_tokens'] = 64000
            old['max_tokens'] = 4096
        con.execute('UPDATE ItemTable SET value=?', (json.dumps(maps),))
        con.commit(); con.close()
        plan = trae_adapter.make_plan(
            self.target, 'https://old.invalid/v1', KEY, ['old'], update_existing=True,
        )
        obj = json.loads(plan.payloads['demo_AI.agent.model.model_list_map'])
        old = next(m for m in obj['builder'] if m.get('custom_model_id') == 'old')
        self.assertEqual(old['prompt_max_tokens'], 64000)
        self.assertEqual(old['max_tokens'], 4096)

    def test_overwrite_does_not_repoint_same_id_from_other_gateway(self):
        plan = trae_adapter.make_plan(self.target, BASE, KEY, ['old'], update_existing=True)
        self.assertFalse(plan.changed)
        self.assertEqual(plan.rows, [('old', '保留其他连接')])
        self.assertIn('保留其他连接 1', plan.summary())
        old = next(m for m in self.read_map()['builder'] if m.get('custom_model_id') == 'old')
        self.assertEqual(old['base_url'], 'https://old.invalid/v1/chat/completions')

    def test_apply_backup_restore_and_race_guard(self):
        plan = trae_adapter.make_plan(self.target, BASE, KEY, ['new'])
        backup = trae_adapter.apply_plan(self.target, plan)
        self.assertTrue(backup.name.endswith('.traebackup'))
        self.assertNotIn(KEY.encode(), backup.read_bytes())
        self.assertEqual(len([m for m in self.read_map()['builder'] if m.get('custom_model_id') == 'new']), 1)
        with patch.object(trae_adapter, 'discover_targets', return_value=[self.target]):
            safety = trae_adapter.restore_backup_file(backup)
        self.assertEqual(len([m for m in self.read_map()['builder'] if m.get('custom_model_id') == 'new']), 0)
        self.assertTrue(safety.name.endswith('.traebackup'))

    def test_preview_race_blocks_write(self):
        plan = trae_adapter.make_plan(self.target, BASE, KEY, ['new'])
        con = sqlite3.connect(self.path)
        maps = json.loads(con.execute('SELECT value FROM ItemTable').fetchone()[0])
        maps['builder'].append({'name': 'unrelated', 'config_source': 1})
        con.execute('UPDATE ItemTable SET value=?', (json.dumps(maps),)); con.commit(); con.close()
        with self.assertRaises(core.UserError): trae_adapter.apply_plan(self.target, plan)
        self.assertFalse(any(m.get('custom_model_id') == 'new' for m in self.read_map()['builder']))

    def test_bad_backup_target_rejected(self):
        plan = trae_adapter.make_plan(self.target, BASE, KEY, ['new'])
        backup = trae_adapter.apply_plan(self.target, plan)
        other = self.path.with_name('other.vscdb')
        with self.assertRaises(core.UserError): trae_adapter._read_backup(backup, other)


if __name__ == '__main__': unittest.main()
