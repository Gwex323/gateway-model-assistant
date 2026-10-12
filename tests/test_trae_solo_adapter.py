from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import core
import trae_adapter
import trae_solo_adapter


BASE = 'https://gateway.example/v1'
KEY = 'test-only-solo-key'


class TraeSoloAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='gateway-solo-test-')
        self.target = trae_adapter.Target(trae_solo_adapter.SOLO_NAME,
                                          Path(self.temp.name) / 'state.vscdb',
                                          trae_solo_adapter.SOLO_NAME)

    def tearDown(self):
        self.temp.cleanup()

    def test_official_name_uses_legacy_internal_storage_path(self):
        targets = trae_adapter.discover_targets(Path(self.temp.name))
        target = next(item for item in targets if item.name == trae_solo_adapter.APP_NAME)
        self.assertEqual(target.name, 'TraeWork CN')
        self.assertIn('TRAE SOLO CN', str(target.path))
        self.assertEqual(target.process_hint, 'TRAE SOLO CN')

    def test_plan_has_no_secret_repr_and_uses_persistent_preview(self):
        plan = trae_solo_adapter.make_plan(
            self.target, BASE, KEY, ['model-a', 'model-a'],
            token_limits_by_model={
                'model-a': {'maxInputTokens': 128000, 'maxOutputTokens': 8192},
            },
        )
        self.assertEqual(plan.ids, ['model-a'])
        self.assertEqual(plan.protocol, 'openai')
        self.assertEqual(plan.provider, 'custom_openai_compatible')
        self.assertEqual(plan.endpoint, BASE)
        self.assertEqual(plan.rows, [('model-a', '导入时复核')])
        self.assertNotIn(KEY, repr(plan))
        self.assertIn('持久化', plan.summary())
        self.assertEqual(plan.model_token_limits['model-a']['maxInputTokens'], 128000)
        self.assertIn('自动识别 Token 上限 1/1', plan.summary())

    def test_renderer_expression_uses_loaded_workbench_and_update_action(self):
        expression = trae_solo_adapter._expression({
            'apiKey': KEY,
            'endpoint': BASE + '/chat/completions',
            'supportsImages': False,
            'updateExisting': True,
            'models': [{'id': 'model-a', 'maxInputTokens': 128000, 'maxOutputTokens': 8192}],
        })
        self.assertIn('window.workbenchPromise', expression)
        self.assertIn("action:'update'", expression)
        self.assertIn('normalizeUrl', expression)
        self.assertIn("'custom_anthropic_compatible//'", expression)
        self.assertIn("String(m?.provider||'')===provider", expression)
        self.assertIn("provider,ak:input.apiKey", expression)
        self.assertIn('matching.length', expression)
        self.assertIn('prompt_max_tokens=model.maxInputTokens', expression)
        self.assertIn('max_tokens=model.maxOutputTokens', expression)
        self.assertIn('waitForModel', expression)
        self.assertIn('const loadOnce=async()=>', expression)
        self.assertIn('models=await load(30)', expression)
        self.assertIn('responseCode===0?60:30', expression)
        self.assertIn('Verify persisted state', expression)
        self.assertIn('if(existing&&responseCode!==0)', expression)
        self.assertIn("stage:'update_request'", expression)
        self.assertIn("'persistence_verify':'add_request'", expression)
        self.assertIn('attempt<120', expression)
        self.assertIn("if(!wb) throw new Error('workbench_module_missing')", expression)
        self.assertLess(expression.index('window.workbenchPromise'),
                        expression.index("createDecorator('IICubeAiChatConnectionService')"))

    def test_anthropic_plan_uses_native_provider_and_messages_endpoint(self):
        plan = trae_solo_adapter.make_plan(
            self.target, BASE + '/messages', KEY, ['claude-test'],
            protocol='anthropic',
            token_limits_by_model={
                'claude-test': {'maxInputTokens': 200000, 'maxOutputTokens': 8192},
            },
        )
        self.assertEqual(plan.protocol, 'anthropic')
        self.assertEqual(plan.provider, 'custom_anthropic_compatible')
        self.assertEqual(plan.base_url, BASE)
        self.assertEqual(plan.endpoint, BASE + '/messages')
        self.assertIn('Anthropic 原生', plan.summary())

    def test_unknown_protocol_is_rejected(self):
        with self.assertRaises(core.UserError):
            trae_solo_adapter.make_plan(
                self.target, BASE, KEY, ['model-a'], protocol='unknown',
            )

    def test_wait_target_prefers_solo_workbench_page(self):
        targets = [
            {'type': 'page', 'url': 'devtools://devtools/bundled/inspector.html',
             'webSocketDebuggerUrl': 'ws://127.0.0.1:1/devtools/page/devtools'},
            {'type': 'page', 'url': 'vscode-file://vscode-app/out/vs/code/electron-browser/solo/solo-lite.html',
             'webSocketDebuggerUrl': 'ws://127.0.0.1:1/devtools/page/workbench'},
        ]
        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                pass
        response = Response()
        with patch.object(trae_solo_adapter, 'urlopen', return_value=response), \
                patch.object(trae_solo_adapter.json, 'load', return_value=targets):
            value = trae_solo_adapter._wait_target(
                1, trae_solo_adapter.time.monotonic() + 1
            )
        self.assertTrue(value.endswith('/workbench'))

    def test_wait_target_ignores_transient_splash_page(self):
        splash = [{'type': 'page', 'url': 'vscode-file://splash.html',
                   'webSocketDebuggerUrl': 'ws://127.0.0.1:1/devtools/page/splash'}]
        workbench = [{'type': 'page', 'url': 'vscode-file://vscode-app/solo/solo-lite.html',
                      'webSocketDebuggerUrl': 'ws://127.0.0.1:1/devtools/page/workbench'}]
        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                pass
        with patch.object(trae_solo_adapter, 'urlopen', return_value=Response()), \
                patch.object(trae_solo_adapter.json, 'load', side_effect=[splash, workbench]), \
                patch.object(trae_solo_adapter.time, 'monotonic', side_effect=[0, 0, 0, 0.25]), \
                patch.object(trae_solo_adapter.time, 'sleep'):
            value = trae_solo_adapter._wait_target(1, 10)
        self.assertTrue(value.endswith('/workbench'))

    def test_runtime_stage_is_allow_listed(self):
        result = {'exceptionDetails': {'exception': {
            'description': 'Error: connection_service_missing\nsecret renderer details'
        }}}
        self.assertEqual(trae_solo_adapter._safe_runtime_stage(result),
                         'connection_service_missing')
        self.assertIsNone(trae_solo_adapter._safe_runtime_stage({
            'exceptionDetails': {'text': KEY}
        }))

    def test_apply_uses_client_result_not_sqlite_cache(self):
        plan = trae_solo_adapter.make_plan(
            self.target, BASE, KEY, ['model-a'],
            token_limits_by_model={
                'model-a': {'maxInputTokens': 128000, 'maxOutputTokens': 8192},
            },
        )
        value = {'added': ['model-a'], 'updated': [], 'kept': [], 'failed': []}
        with patch.object(trae_solo_adapter, 'ensure_solo_stopped'), \
                patch.object(trae_solo_adapter, 'discover_executable', return_value=Path('solo.exe')), \
                patch.object(trae_solo_adapter, '_run_client_import', return_value=value) as run:
            result = trae_solo_adapter.apply_plan(self.target, plan)
        self.assertTrue(result.changed)
        payload = run.call_args.args[1]
        self.assertEqual(payload['baseUrl'], BASE)
        self.assertEqual(payload['protocol'], 'openai')
        self.assertEqual(payload['provider'], 'custom_openai_compatible')
        self.assertEqual(payload['endpoint'], BASE)
        self.assertNotIn('maxInputTokens', payload)
        self.assertNotIn('maxOutputTokens', payload)
        self.assertEqual(payload['models'], [{
            'id': 'model-a', 'maxInputTokens': 128000, 'maxOutputTokens': 8192,
        }])

    def test_anthropic_apply_passes_provider_endpoint_and_token_limits(self):
        plan = trae_solo_adapter.make_plan(
            self.target, BASE, KEY, ['claude-test'], protocol='anthropic',
            token_limits_by_model={
                'claude-test': {'maxInputTokens': 200000, 'maxOutputTokens': 8192},
            },
        )
        value = {'added': ['claude-test'], 'updated': [], 'kept': [], 'failed': []}
        with patch.object(trae_solo_adapter, 'ensure_solo_stopped'), \
                patch.object(trae_solo_adapter, 'discover_executable', return_value=Path('solo.exe')), \
                patch.object(trae_solo_adapter, '_run_client_import', return_value=value) as run:
            trae_solo_adapter.apply_plan(self.target, plan)
        payload = run.call_args.args[1]
        self.assertEqual(payload['protocol'], 'anthropic')
        self.assertEqual(payload['provider'], 'custom_anthropic_compatible')
        self.assertEqual(payload['baseUrl'], BASE)
        self.assertEqual(payload['endpoint'], BASE + '/messages')
        self.assertEqual(payload['models'], [{
            'id': 'claude-test', 'maxInputTokens': 200000, 'maxOutputTokens': 8192,
        }])

    def test_failed_model_returns_safe_error_without_key(self):
        plan = trae_solo_adapter.make_plan(self.target, BASE, KEY, ['model-a'])
        value = {'added': [], 'updated': [], 'kept': [], 'failed': [{'id': 'model-a', 'code': 42}]}
        with patch.object(trae_solo_adapter, 'ensure_solo_stopped'), \
                patch.object(trae_solo_adapter, 'discover_executable', return_value=Path('solo.exe')), \
                patch.object(trae_solo_adapter, '_run_client_import', return_value=value):
            with self.assertRaises(core.UserError) as ctx:
                trae_solo_adapter.apply_plan(self.target, plan)
        self.assertIn('model-a', str(ctx.exception))
        self.assertNotIn(KEY, str(ctx.exception))

    def test_websocket_header_reader_preserves_first_frame_bytes(self):
        class Sock:
            def __init__(self):
                self.chunks = [b'HTTP/1.1 101 Switching Protocols\r\nX: y\r\n\r\n\x81\x02ok']

            def recv(self, _size):
                return self.chunks.pop(0) if self.chunks else b''

        ws = trae_solo_adapter._WebSocket.__new__(trae_solo_adapter._WebSocket)
        ws.sock = Sock()
        ws._buffer = bytearray()
        headers = ws._read_headers()
        self.assertTrue(headers.endswith(b'\r\n\r\n'))
        self.assertEqual(ws._read_exact(4), b'\x81\x02ok')

    def test_cdp_runtime_exception_is_safe_and_normal_client_relaunches(self):
        class FakeProcess:
            def wait(self, timeout=None):
                return 0

            def terminate(self):
                pass

        class FakeCDP:
            def __init__(self, _url):
                pass

            def call(self, method, params=None, timeout=60):
                if method == 'Runtime.evaluate':
                    if params.get('expression') == trae_solo_adapter._RENDERER_READY_EXPRESSION:
                        return {'result': {'value': {'readyState': 'complete', 'hasWorkbench': True}}}
                    return {
                        'result': {'type': 'object', 'subtype': 'error'},
                        'exceptionDetails': {'text': 'safe test exception'},
                    }
                return {}

            def close(self):
                pass

        popen_calls = []

        def fake_popen(args):
            popen_calls.append(args)
            return FakeProcess()

        with patch.object(trae_solo_adapter, '_free_port', return_value=32123), \
                patch.object(trae_solo_adapter, '_wait_target', return_value='ws://127.0.0.1:32123/devtools/page/test'), \
                patch.object(trae_solo_adapter, '_CDP', FakeCDP), \
                patch.object(trae_solo_adapter.subprocess, 'Popen', side_effect=fake_popen):
            with self.assertRaises(core.UserError):
                trae_solo_adapter._run_client_import(Path('solo.exe'), {
                    'apiKey': KEY, 'endpoint': BASE + '/chat/completions',
                    'supportsImages': False, 'updateExisting': False,
                    'models': [{'id': 'model-a'}],
                })
        self.assertEqual(len(popen_calls), 2)
        self.assertNotIn(KEY, json.dumps(popen_calls))

    def test_read_only_probe_can_skip_normal_relaunch(self):
        class FakeProcess:
            def wait(self, timeout=None):
                return 0
            def terminate(self):
                pass
        class FakeCDP:
            def __init__(self, _url):
                pass
            def call(self, method, params=None, timeout=60):
                if method == 'Runtime.evaluate':
                    if params.get('expression') == trae_solo_adapter._RENDERER_READY_EXPRESSION:
                        return {'result': {'value': {'readyState': 'complete', 'hasWorkbench': True}}}
                    return {'result': {'value': {
                        'added': [], 'updated': [], 'kept': [], 'failed': []
                    }}}
                return {}
            def close(self):
                pass
        with patch.object(trae_solo_adapter, '_free_port', return_value=32123), \
                patch.object(trae_solo_adapter, '_wait_target', return_value='ws://127.0.0.1:32123/devtools/page/test'), \
                patch.object(trae_solo_adapter, '_CDP', FakeCDP), \
                patch.object(trae_solo_adapter.subprocess, 'Popen', return_value=FakeProcess()) as popen:
            trae_solo_adapter._run_client_import(Path('solo.exe'), {
                'apiKey': KEY, 'endpoint': BASE + '/chat/completions',
                'supportsImages': False, 'updateExisting': False, 'models': [],
            }, relaunch=False)
        popen.assert_called_once()

    def test_transient_cdp_navigation_error_reconnects_and_retries(self):
        class FakeProcess:
            def wait(self, timeout=None):
                return 0
            def terminate(self):
                pass
        calls = []
        readiness_calls = 0
        class FakeCDP:
            def __init__(self, url):
                self.url = url
                calls.append(('connect', url))
            def call(self, method, params=None, timeout=60):
                nonlocal readiness_calls
                calls.append(('call', self.url, method))
                if method == 'Runtime.evaluate':
                    if params.get('expression') == trae_solo_adapter._RENDERER_READY_EXPRESSION:
                        readiness_calls += 1
                        if readiness_calls == 1:
                            raise trae_solo_adapter._CDPProtocolError(-32000)
                        return {'result': {'value': {
                            'readyState': 'complete', 'hasWorkbench': True,
                        }}}
                    return {'result': {'value': {
                        'added': [], 'updated': [], 'kept': [], 'failed': []
                    }}}
                return {}
            def close(self):
                calls.append(('close', self.url))
        urls = iter([
            'ws://127.0.0.1:32123/devtools/page/early',
            'ws://127.0.0.1:32123/devtools/page/stable',
        ])
        with patch.object(trae_solo_adapter, '_free_port', return_value=32123), \
                patch.object(trae_solo_adapter, '_wait_target', side_effect=lambda *_: next(urls)), \
                patch.object(trae_solo_adapter, '_CDP', FakeCDP), \
                patch.object(trae_solo_adapter.time, 'sleep'), \
                patch.object(trae_solo_adapter.subprocess, 'Popen', return_value=FakeProcess()):
            result = trae_solo_adapter._run_client_import(Path('solo.exe'), {
                'apiKey': KEY, 'endpoint': BASE + '/chat/completions',
                'supportsImages': False, 'updateExisting': False, 'models': [],
            }, relaunch=False)
        self.assertEqual(result['failed'], [])
        self.assertGreaterEqual(readiness_calls, 3)
        self.assertIn(('connect', 'ws://127.0.0.1:32123/devtools/page/stable'), calls)

    def test_solo_process_check_does_not_match_helper_command_lines(self):
        with patch.object(trae_solo_adapter.subprocess, 'run') as run:
            run.return_value.returncode = 0
            run.return_value.stdout = '0\n'
            trae_solo_adapter.ensure_solo_stopped(self.target)
        script = run.call_args.args[0][-1]
        self.assertNotIn('CommandLine', script)
        self.assertNotIn('ai-agent', script)
        self.assertNotIn("''", script)


if __name__ == '__main__':
    unittest.main()
