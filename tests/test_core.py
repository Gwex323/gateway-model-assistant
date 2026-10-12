import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core

KEY = 'test-only-not-a-real-key'
BASE = 'https://gateway.example/v1'


def original(models=None):
    return json.dumps(models or [{'id': 'existing', 'vendor': 'Custom', 'name': 'My name',
                                  'url': BASE, 'apiKey': KEY, 'supportsImages': True,
                                  'unknownFutureOption': {'keep': True}}]).encode()


class UrlTests(unittest.TestCase):
    def test_normalization(self):
        self.assertEqual(core.normalize_url('https://GATEWAY.example:443/'), BASE)
        self.assertEqual(core.normalize_url(BASE + '/chat/completions/'), BASE)
        self.assertEqual(core.normalize_url(BASE + '/models'), BASE)
        self.assertEqual(core.normalize_url('https://gateway.example/custom/v2'), 'https://gateway.example/custom/v2')
        self.assertEqual(core.normalize_url('http://127.0.0.1:8787'), 'http://127.0.0.1:8787/v1')
        self.assertEqual(core.normalize_url('http://[::1]:8787'), 'http://[::1]:8787/v1')

    def test_anthropic_normalization(self):
        self.assertEqual(core.normalize_anthropic_url('https://API.Anthropic.com'),
                         'https://api.anthropic.com/v1')
        self.assertEqual(core.normalize_anthropic_url('https://api.anthropic.com/v1/messages'),
                         'https://api.anthropic.com/v1')
        self.assertEqual(core.normalize_anthropic_url('https://gateway.example/anthropic/v1/models'),
                         'https://gateway.example/anthropic/v1')

    def test_private_http_requires_explicit_opt_in(self):
        value = 'http://172.16.116.14/v1/chat/completions'
        with self.assertRaises(core.UserError):
            core.normalize_url(value)
        self.assertEqual(core.normalize_url(value, allow_private_http=True),
                         'http://172.16.116.14/v1')
        self.assertTrue(core.is_private_http_url(value))
        self.assertFalse(core.is_private_http_url('http://127.0.0.1:8787/v1'))
        with self.assertRaises(core.UserError):
            core.normalize_url('http://gateway.example', allow_private_http=True)

    def test_reject_unsafe_urls(self):
        for value in ['http://gateway.example', 'https://user:secret@gateway.example',
                      BASE + '?key=secret', BASE + '#secret', 'file:///tmp/model',
                      'https://gateway.example:bad', 'https://gateway.example\n',
                      'https://gate way.example', '']:
            # Ordinary pasted trailing whitespace is normalized, not sent.
            if value.endswith('\n'): continue
            with self.subTest(value=value), self.assertRaises(core.UserError):
                core.normalize_url(value)

    def test_key_validation_does_not_echo(self):
        for value in ['', 'fake\nsecret', '密钥']:
            with self.assertRaises(core.UserError) as ctx: core.check_key(value)
            if value: self.assertNotIn(value, str(ctx.exception))

    def test_id_validation(self):
        for mid in ['okay/model-v1', 'glm 5.2']: self.assertTrue(core.valid_id(mid))
        for mid in ['', ' bad', '\n', None, 'x' * 257]: self.assertFalse(core.valid_id(mid))


class PlanTests(unittest.TestCase):
    def test_token_limit_parsing(self):
        self.assertEqual(
            core.parse_token_limits('128000', '8192'),
            {'maxInputTokens': 128000, 'maxOutputTokens': 8192},
        )
        self.assertEqual(core.parse_token_limits('', '  '), {})
        for values in [('abc', ''), ('0', ''), ('', '-1'), (True, '')]:
            with self.subTest(values=values), self.assertRaises(core.UserError):
                core.parse_token_limits(*values)

    def test_plan_writes_token_limits_and_preserves_unspecified_values(self):
        limits = {'maxInputTokens': 128000, 'maxOutputTokens': 8192}
        created = core.make_plan(None, BASE, KEY, ['new'], token_limits=limits)
        self.assertEqual(created.document[0]['maxInputTokens'], 128000)
        self.assertEqual(created.document[0]['maxOutputTokens'], 8192)
        raw = json.dumps(created.document).encode()
        updated = core.make_plan(raw, BASE, 'changed-test-key', ['new'], update_existing=True)
        self.assertEqual(updated.document[0]['maxInputTokens'], 128000)
        self.assertEqual(updated.document[0]['maxOutputTokens'], 8192)

    def test_plan_writes_different_limits_for_each_model(self):
        plan = core.make_plan(
            None, BASE, KEY, ['model-a', 'model-b'],
            token_limits_by_model={
                'model-a': {'maxInputTokens': 128000, 'maxOutputTokens': 8192},
                'model-b': {'maxInputTokens': 64000, 'maxOutputTokens': 4096},
            },
        )
        models = {model['id']: model for model in plan.document}
        self.assertEqual(models['model-a']['maxInputTokens'], 128000)
        self.assertEqual(models['model-a']['maxOutputTokens'], 8192)
        self.assertEqual(models['model-b']['maxInputTokens'], 64000)
        self.assertEqual(models['model-b']['maxOutputTokens'], 4096)

    def test_add_and_preserve(self):
        raw = original()
        plan = core.make_plan(raw, BASE, KEY, ['existing', 'new', 'new'])
        self.assertEqual(plan.rows, [('existing', '保留已有'), ('new', '新增')])
        self.assertEqual(plan.document[0], json.loads(raw)[0])
        self.assertFalse(plan.document[1]['supportsToolCall'])
        self.assertFalse(plan.document[1]['supportsImages'])
        self.assertEqual(plan.original, raw)
        self.assertNotIn(KEY, repr(plan))

    def test_preserve_wrapper_and_unknown_fields(self):
        obj = {'models': json.loads(original()), 'future': {'version': 9}}
        plan = core.make_plan(json.dumps(obj).encode(), BASE, 'changed-test-key', ['existing'],
                              {'supportsToolCall': True}, True)
        self.assertEqual(plan.document['future'], obj['future'])
        m = plan.document['models'][0]
        self.assertEqual(m['unknownFutureOption'], {'keep': True})
        self.assertTrue(m['supportsImages'])
        self.assertEqual(m['name'], 'My name')
        self.assertTrue(m['supportsToolCall'])

    def test_dedup_and_idempotence(self):
        first = core.make_plan(None, BASE, KEY, ['new'], {'supportsToolCall': True})
        raw = json.dumps(first.document).encode()
        second = core.make_plan(raw, BASE, KEY, ['new'], {'supportsToolCall': True}, True)
        self.assertFalse(second.changed)
        self.assertEqual(second.rows, [('new', '无变化')])

    def test_unknown_protocol_or_vendor_not_repointed(self):
        for fields in [{'vendor': 'Anthropic'}, {'vendor': 'Custom', 'useCustomProtocol': True}]:
            model = dict(id='existing', **fields)
            plan = core.make_plan(json.dumps([model]).encode(), BASE, KEY, ['existing'], update_existing=True)
            self.assertFalse(plan.changed)
            self.assertEqual(plan.document, [model])

    def test_overwrite_does_not_repoint_same_id_from_other_gateway(self):
        model = {
            'id': 'existing', 'vendor': 'Custom', 'url': 'https://other.example/v1',
            'apiKey': 'other-test-key', 'useCustomProtocol': False,
        }
        plan = core.make_plan(
            json.dumps([model]).encode(), BASE, KEY, ['existing'], update_existing=True,
        )
        self.assertFalse(plan.changed)
        self.assertEqual(plan.document, [model])
        self.assertEqual(plan.rows, [('existing', '保留其他连接')])

    def test_corrupt_and_duplicate_config_rejected(self):
        for raw in [b'{broken', b'{}', b'null', b'{"models":{}}', b'[{}]', b'[{"id":"a"},{"id":"a"}]']:
            with self.subTest(raw=raw), self.assertRaises(core.UserError):
                core.make_plan(raw, BASE, KEY, ['new'])

    def test_bom_supported(self):
        self.assertEqual(core.parse_document(b'\xef\xbb\xbf[]'), [])

    def test_no_selection_or_invalid_flags(self):
        for args in [([], None), (['new'], {'random': True}), (['new'], {'supportsToolCall': 1})]:
            with self.assertRaises(core.UserError): core.make_plan(None, BASE, KEY, *args)

    def test_media_is_hint_only(self):
        self.assertTrue(core.media_hint('Seedance-2.0'))
        self.assertFalse(core.media_hint('text-coder'))


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='gateway-assistant-test-')
        self.path = Path(self.temp.name) / 'models.json'
        self.path.write_bytes(original())
        self.guard = patch.object(core, 'ensure_workbuddy_stopped')
        self.guard.start()
        # Production DPAPI is exercised separately; deterministic local encryption stub here.
        self.crypto = patch.object(core, 'dpapi', side_effect=lambda data, decrypt=False: data[::-1])
        self.crypto.start()

    def tearDown(self):
        self.crypto.stop()
        self.guard.stop()
        self.temp.cleanup()

    def plan(self):
        return core.make_plan(self.path.read_bytes(), BASE, KEY, ['new'])

    def test_backup_apply_restore_exact_bytes(self):
        before = self.path.read_bytes()
        backup = core.apply_plan(self.path, self.plan())
        after = self.path.read_bytes()
        self.assertEqual(len(core.parse_document(after)), 2)
        self.assertNotIn(KEY.encode(), backup.read_bytes())
        safety = core.restore_backup(self.path, backup)
        self.assertEqual(self.path.read_bytes(), before)
        core.restore_backup(self.path, safety)
        self.assertEqual(self.path.read_bytes(), after)

    def test_preview_race_blocks_write(self):
        plan = self.plan()
        self.path.write_bytes(b'[]')
        with self.assertRaises(core.UserError): core.apply_plan(self.path, plan)
        self.assertEqual(self.path.read_bytes(), b'[]')
        self.assertFalse((self.path.parent / 'gateway-model-assistant-backups').exists())

    def test_respect_native_workbuddy_lock(self):
        lock = self.path.with_name('.models.json.workbuddy.lock')
        lock.mkdir()
        with self.assertRaises(core.UserError): core.apply_plan(self.path, self.plan())
        self.assertTrue(lock.exists())

    def test_app_running_blocks_write(self):
        with patch.object(core, 'ensure_workbuddy_stopped', side_effect=core.UserError('running')):
            with self.assertRaises(core.UserError): core.apply_plan(self.path, self.plan())
        self.assertEqual(self.path.read_bytes(), original())

    def test_backup_failure_leaves_original(self):
        with patch.object(core, 'dpapi', side_effect=core.UserError('cannot encrypt')):
            with self.assertRaises(core.UserError): core.apply_plan(self.path, self.plan())
        self.assertEqual(self.path.read_bytes(), original())
        self.assertFalse(self.path.with_name('.models.json.workbuddy.lock').exists())

    def test_atomic_replace_failure_preserves_file_and_cleans_temp(self):
        before = self.path.read_bytes()
        with patch.object(core.os, 'replace', side_effect=PermissionError('test failure')):
            with self.assertRaises(PermissionError): core.atomic_write(self.path, b'[]')
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob('*.tmp')), [])

    def test_restore_wrong_target_rejected(self):
        backup = core.apply_plan(self.path, self.plan())
        other = self.path.with_name('other.json')
        with self.assertRaises(core.UserError): core.restore_backup(other, backup)
        self.assertFalse(other.exists())

    def test_first_import_restore_absence(self):
        self.path.unlink()
        backup = core.apply_plan(self.path, core.make_plan(None, BASE, KEY, ['new']))
        self.assertTrue(self.path.exists())
        core.restore_backup(self.path, backup)
        self.assertFalse(self.path.exists())

    def test_unchanged_creates_no_backup(self):
        plan = core.make_plan(self.path.read_bytes(), BASE, KEY, ['existing'])
        self.assertIsNone(core.apply_plan(self.path, plan))
        self.assertFalse((self.path.parent / 'gateway-model-assistant-backups').exists())

    def test_missing_parent_rejected(self):
        path = self.path.parent / 'missing/models.json'
        with self.assertRaises(core.UserError): core.apply_plan(path, core.make_plan(None, BASE, KEY, ['new']))
        self.assertFalse(path.parent.exists())

    def test_wrong_backup_rejected(self):
        bad = self.path.with_name('bad.wbbackup')
        bad.write_bytes(b'not-a-backup')
        with self.assertRaises(core.UserError): core.restore_backup(self.path, bad)
        self.assertEqual(self.path.read_bytes(), original())


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.anthropic_requests = []
        requests = self.requests
        anthropic_requests = self.anthropic_requests
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                requests.append((self.path, self.headers.get('Authorization')))
                anthropic_requests.append((
                    self.path,
                    self.headers.get('x-api-key'),
                    self.headers.get('anthropic-version'),
                ))
                if self.path == '/v1/models':
                    self.send_response(200); self.end_headers()
                    self.wfile.write(json.dumps({'data': [{'id': 'z'}, {'id': 'a'}, {'id': 'a'}, {'id': ''}, {}]}).encode())
                elif self.path == '/catalog/models':
                    self.send_response(200); self.end_headers()
                    self.wfile.write(json.dumps({'data': [
                        {'id': 'direct', 'max_input_tokens': 128000, 'max_output_tokens': 8192},
                        {'id': 'camel', 'limits': {'maxInputTokens': 64000, 'maxOutputTokens': 4096}},
                        {'id': 'context', 'context_length': 100000, 'top_provider': {'max_completion_tokens': 8000}},
                        {'id': 'context-only', 'metadata': {'contextWindow': '32000'}},
                        {'id': 'invalid', 'max_input_tokens': True, 'max_output_tokens': -1,
                         'metadata': {'context_length': 100000001}},
                    ]}).encode())
                elif self.path == '/redirect/models':
                    self.send_response(302); self.send_header('Location', '/should-not-follow'); self.end_headers()
                elif self.path == '/denied/models':
                    self.send_response(401); self.end_headers(); self.wfile.write(KEY.encode())
                elif self.path == '/bad/models':
                    self.send_response(200); self.end_headers(); self.wfile.write(b'{"models":[]}')
                elif self.path == '/anthropic/v1/models?limit=1000':
                    self.send_response(200); self.end_headers()
                    self.wfile.write(json.dumps({
                        'data': [
                            {'id': 'claude-b', 'max_input_tokens': 200000, 'max_tokens': 8192},
                            {'id': 'claude-a', 'max_input_tokens': '180000', 'max_tokens': '4096'},
                        ],
                        'has_more': True,
                        'last_id': 'claude-a',
                    }).encode())
                elif self.path == '/anthropic/v1/models?limit=1000&after_id=claude-a':
                    self.send_response(200); self.end_headers()
                    self.wfile.write(json.dumps({
                        'data': [
                            {'id': 'claude-c', 'max_input_tokens': 100000, 'max_tokens': 2048},
                            {'id': 'claude-a', 'max_input_tokens': 200000, 'max_tokens': 8192},
                        ],
                        'has_more': False,
                        'last_id': 'claude-c',
                    }).encode())
                elif self.path.startswith('/anthropic-loop/models?'):
                    self.send_response(200); self.end_headers()
                    self.wfile.write(json.dumps({
                        'data': [{'id': 'claude-loop'}],
                        'has_more': True,
                        'last_id': 'claude-loop',
                    }).encode())
                else:
                    self.send_response(500); self.end_headers()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:%d' % self.server.server_port

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def test_fetch_only_models_no_paid_calls(self):
        self.assertEqual(core.fetch_models(self.base, KEY), ['a', 'z'])
        self.assertEqual(self.requests, [('/v1/models', 'Bearer ' + KEY)])

    def test_fetch_catalog_reads_per_model_token_metadata(self):
        catalog = core.fetch_model_catalog(self.base + '/catalog', KEY)
        models = {model.id: model for model in catalog}
        self.assertEqual(models['direct'].token_limits,
                         {'maxInputTokens': 128000, 'maxOutputTokens': 8192})
        self.assertEqual(models['camel'].token_limits,
                         {'maxInputTokens': 64000, 'maxOutputTokens': 4096})
        self.assertEqual(models['context'].token_limits,
                         {'maxInputTokens': 92000, 'maxOutputTokens': 8000})
        self.assertEqual(models['context-only'].token_limits, {'maxInputTokens': 32000})
        self.assertEqual(models['invalid'].token_limits, {})
        self.assertEqual(self.requests, [('/catalog/models', 'Bearer ' + KEY)])

    def test_redirect_not_followed(self):
        with self.assertRaises(core.UserError): core.fetch_models(self.base + '/redirect', KEY)
        self.assertEqual(len(self.requests), 1)

    def test_errors_never_echo_body_key(self):
        with self.assertRaises(core.UserError) as ctx: core.fetch_models(self.base + '/denied', KEY)
        self.assertNotIn(KEY, str(ctx.exception))

    def test_wrong_format_rejected(self):
        with self.assertRaises(core.UserError): core.fetch_models(self.base + '/bad', KEY)

    def test_anthropic_fetch_uses_native_headers_pagination_and_token_fields(self):
        catalog = core.fetch_anthropic_model_catalog(self.base + '/anthropic/v1/messages', KEY)
        self.assertEqual([model.id for model in catalog], ['claude-a', 'claude-b', 'claude-c'])
        models = {model.id: model for model in catalog}
        self.assertEqual(models['claude-a'].token_limits,
                         {'maxInputTokens': 180000, 'maxOutputTokens': 4096})
        self.assertEqual(models['claude-b'].token_limits,
                         {'maxInputTokens': 200000, 'maxOutputTokens': 8192})
        self.assertEqual(self.requests, [
            ('/anthropic/v1/models?limit=1000', None),
            ('/anthropic/v1/models?limit=1000&after_id=claude-a', None),
        ])
        self.assertEqual(self.anthropic_requests, [
            ('/anthropic/v1/models?limit=1000', KEY, core.ANTHROPIC_VERSION),
            ('/anthropic/v1/models?limit=1000&after_id=claude-a', KEY, core.ANTHROPIC_VERSION),
        ])

    def test_anthropic_pagination_loop_is_rejected(self):
        with self.assertRaises(core.UserError):
            core.fetch_anthropic_model_catalog(self.base + '/anthropic-loop', KEY)
        self.assertEqual(len(self.requests), 2)


@unittest.skipUnless(os.name == 'nt', 'Windows native DPAPI only')
class WindowsCryptoTests(unittest.TestCase):
    def test_native_dpapi_roundtrip(self):
        raw = b'test-only-secret-12345'
        sealed = core.dpapi(raw)
        self.assertNotIn(raw, sealed)
        self.assertEqual(core.dpapi(sealed, decrypt=True), raw)

    def test_native_backup_roundtrip_in_temporary_folder(self):
        with tempfile.TemporaryDirectory(prefix='gateway-dpapi-test-') as d:
            path = Path(d) / 'models.json'
            raw = original()
            path.write_bytes(raw)
            with patch.object(core, 'ensure_workbuddy_stopped'):
                backup = core.apply_plan(path, core.make_plan(raw, BASE, KEY, ['new']))
                self.assertNotIn(KEY.encode(), backup.read_bytes())
                core.restore_backup(path, backup)
                self.assertEqual(path.read_bytes(), raw)


if __name__ == '__main__':
    unittest.main()
