"""Provider resolution and discovery tests. No upstream traffic or real client writes."""
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core
import trae_adapter
import trae_solo_adapter as solo

ARK = 'https://ark.cn-beijing.volces.com/api/coding'
KEY = 'fake-provider-test-key'


class ConnectionTests(unittest.TestCase):
    def test_deepseek_separates_discovery_from_messages(self):
        for suffix in ('/anthropic', '/anthropic/', '/anthropic/v1', '/anthropic/v1/messages'):
            conn = core.resolve_connection('https://api.deepseek.com' + suffix, 'anthropic')
            self.assertEqual(conn.endpoint, 'https://api.deepseek.com/anthropic/v1/messages')
            self.assertEqual(conn.models_urls, ('https://api.deepseek.com/models',))
            self.assertEqual(conn.headers(KEY)['x-api-key'], KEY)
            headers = conn.headers(KEY, models=True)
            self.assertEqual(headers['Authorization'], 'Bearer ' + KEY)
            self.assertNotIn('x-api-key', headers)
            self.assertNotIn('anthropic-version', headers)

    def test_deepseek_catalog_uses_openai_parser_without_anthropic_pagination(self):
        with patch.object(core, '_request_json', return_value={'data': [
            {'id': 'test-model', 'max_tokens': 8192},
        ]}) as request:
            catalog = core.fetch_anthropic_model_catalog('https://api.deepseek.com/anthropic', KEY)
        req = request.call_args.args[0]
        self.assertEqual(req.full_url, 'https://api.deepseek.com/models')
        self.assertEqual(req.get_header('Authorization'), 'Bearer ' + KEY)
        self.assertEqual(catalog[0].token_limits, {})
        request.assert_called_once()

    def test_deepseek_mapping_is_exact_and_does_not_redirect_other_gateways(self):
        for url in ('https://api.deepseek.com.attacker.example/anthropic',
                    'https://api.deepseek.com:8443/anthropic',
                    'https://example.com/deepseek/anthropic'):
            conn = core.resolve_connection(url, 'anthropic')
            self.assertNotIn('https://api.deepseek.com/models', conn.models_urls)

    def test_deepseek_native_plan_keeps_anthropic_and_no_bearer_warning(self):
        target = trae_adapter.Target(solo.SOLO_NAME, Path('fake-state.vscdb'), solo.SOLO_NAME)
        plan = solo.make_plan(target, 'https://api.deepseek.com/anthropic', KEY,
                              ['test-model'], protocol='anthropic')
        self.assertEqual(plan.auth, 'x-api-key')
        self.assertEqual(plan.provider, 'custom_anthropic_compatible')
        self.assertEqual(plan.endpoint, 'https://api.deepseek.com/anthropic/v1/messages')
        self.assertEqual(plan.compatibility_warning, '')

    def test_volc_anthropic_root_and_headers(self):
        for value in (ARK, ARK + '/', ARK + '/v1', ARK + '/v1/messages', ARK + '/v1/models'):
            with self.subTest(value=value):
                conn = core.resolve_connection(value, 'anthropic')
                self.assertEqual(conn.base_url, ARK + '/v1')
                self.assertEqual(conn.endpoint, ARK + '/v1/messages')
                self.assertEqual(conn.models_urls, (ARK + '/v1/models',))
                self.assertEqual(conn.auth, 'bearer')
                self.assertEqual(conn.headers(KEY)['Authorization'], 'Bearer ' + KEY)
                self.assertNotIn('x-api-key', conn.headers(KEY))
                self.assertNotIn(KEY, repr(conn))

    def test_volc_openai_root_uses_coding_v3(self):
        conn = core.resolve_connection(ARK)
        self.assertEqual(conn.endpoint, ARK + '/v3/chat/completions')
        self.assertEqual(conn.models_urls, (ARK + '/v3/models',))
        self.assertNotIn('/api/v3', conn.endpoint)

    def test_host_match_cannot_be_spoofed(self):
        for host in ('ark.cn-beijing.volces.com.attacker.example', 'attacker.example'):
            conn = core.resolve_connection('https://' + host + '/api/coding', 'anthropic')
            self.assertEqual(conn.auth, 'x-api-key')
            self.assertEqual(conn.provider_name, '自定义网关')

    def test_explicit_auth_overrides_provider_default(self):
        conn = core.resolve_connection(ARK, 'anthropic', 'x-api-key')
        self.assertEqual(conn.headers(KEY)['x-api-key'], KEY)
        self.assertNotIn('Authorization', conn.headers(KEY))
        conn = core.resolve_connection('https://example.com/v1', 'anthropic', 'bearer')
        self.assertNotIn('x-api-key', conn.headers(KEY))

    def test_unknown_protocol_or_auth_fails_closed(self):
        for protocol, auth in [('unknown', 'auto'), ('anthropic', 'unknown')]:
            with self.assertRaises(core.UserError):
                core.resolve_connection(ARK, protocol, auth)

    def test_custom_full_endpoint_and_version_are_preserved(self):
        conn = core.resolve_connection('https://example.com/proxy/v4/messages', 'anthropic')
        self.assertEqual(conn.endpoint, 'https://example.com/proxy/v4/messages')
        self.assertEqual(conn.models_urls, ('https://example.com/proxy/v4/models',))

    def test_compat_anthropic_suffix_adds_single_v1(self):
        conn = core.resolve_connection('https://example.com/api/anthropic', 'anthropic')
        self.assertEqual(conn.endpoint, 'https://example.com/api/anthropic/v1/messages')
        self.assertEqual(core.resolve_connection(conn.endpoint, 'anthropic'), conn)

    def test_404_or_405_falls_back_within_same_base_only(self):
        for status in (404, 405):
            with self.subTest(status=status), patch.object(core, '_request_json', side_effect=[
                core.ModelListError(status, 'not found'), {'data': [{'id': 'model-a'}]},
            ]) as request:
                catalog = core.fetch_model_catalog('https://example.com/custom', KEY)
            self.assertEqual(catalog[0].id, 'model-a')
            self.assertEqual([call.args[0].full_url for call in request.call_args_list], [
                'https://example.com/custom/models', 'https://example.com/custom/v1/models',
            ])

    def test_non_path_errors_never_try_another_candidate(self):
        for status in (301, 401, 403, 429, 500):
            with self.subTest(status=status), patch.object(core, '_request_json',
                side_effect=core.ModelListError(status, 'safe error')) as request:
                with self.assertRaises(core.ModelListError):
                    core.fetch_model_catalog('https://example.com/custom', KEY)
                request.assert_called_once()

    def test_unauthorized_does_not_claim_key_expired_or_echo_body(self):
        error = HTTPError('https://example.com', 401, 'secret', {}, io.BytesIO(KEY.encode()))
        with patch.object(core.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(core.ModelListError) as caught:
                core.fetch_model_catalog('https://example.com', KEY)
        self.assertEqual(caught.exception.status, 401)
        self.assertNotIn(KEY, str(caught.exception))
        self.assertNotIn('过期', str(caught.exception))

    def test_paginated_bearer_fetch_keeps_selected_endpoint(self):
        pages = [
            core.ModelListError(404, 'not found'),
            {'data': [{'id': 'a'}], 'has_more': True, 'last_id': 'a'},
            {'data': [{'id': 'b', 'max_input_tokens': 200000, 'max_tokens': 8192}], 'has_more': False},
        ]
        with patch.object(core, '_request_json', side_effect=pages) as request:
            catalog = core.fetch_anthropic_model_catalog('https://example.com/proxy', KEY, auth='bearer')
        self.assertEqual([model.id for model in catalog], ['a', 'b'])
        self.assertEqual(catalog[1].max_output_tokens, 8192)
        calls = [call.args[0] for call in request.call_args_list]
        self.assertEqual(calls[-1].full_url, 'https://example.com/proxy/v1/models?limit=1000&after_id=a')
        for req in calls:
            self.assertEqual(req.get_header('Authorization'), 'Bearer ' + KEY)
            self.assertIsNone(req.get_header('X-api-key'))

    def test_invalid_pagination_and_model_limit(self):
        for page in ({'data': [], 'has_more': 'yes'}, {'data': [], 'has_more': True},
                     {'data': [{'id': str(i)} for i in range(10001)]}):
            with self.subTest(page_size=len(page['data'])), patch.object(core, '_request_json', return_value=page):
                with self.assertRaises(core.UserError):
                    core.fetch_anthropic_model_catalog(ARK, KEY)

    def test_volc_native_plan_does_not_invent_client_auth_enum(self):
        target = trae_adapter.Target(solo.SOLO_NAME, Path('fake-state.vscdb'), solo.SOLO_NAME)
        plan = solo.make_plan(target, ARK, KEY, ['model-a'], protocol='anthropic')
        self.assertEqual(plan.endpoint, ARK + '/v1/messages')
        self.assertEqual(plan.auth, 'bearer')
        self.assertIn('尚未验证', plan.compatibility_warning)
        self.assertEqual(plan.provider, 'custom_anthropic_compatible')


@unittest.skipUnless(shutil.which('node'), 'Node is required for renderer fixture tests')
class RendererTests(unittest.TestCase):
    def run_renderer(self, records, overwrite):
        payload = dict(protocol='anthropic', provider='custom_anthropic_compatible',
                       apiKey=KEY, baseUrl='https://example.com/v1',
                       endpoint='https://example.com/v1/messages', supportsImages=False,
                       updateExisting=overwrite, models=[dict(id='model-a', maxInputTokens=200000)])
        script = '''
let records = RECORDS;
const writes = [];
global.setTimeout = (fn) => { fn(); return 0; };
const service = {getDefaultClient: async () => ({request: async ({method, data}) => {
  if (method === 'model_list') return {code:0, data:{model_list:records}};
  writes.push({method, data});
  if (method === 'add_custom_model') records.push({config_source:3,
    name:data.provider+'//'+data.model_name, provider:data.provider,
    base_url:data.base_url, custom_model_id:'fake-new'});
  return {code:0};
}})};
global.window = {workbenchPromise: Promise.resolve({createDecorator: x=>x,
  icubeStore:{serviceCollection:{get:()=>service}}})};
EXPRESSION.then(result=>console.log(JSON.stringify({result,writes}))).catch(()=>process.exit(1));
'''.replace('RECORDS', json.dumps(records)).replace('EXPRESSION', solo._expression(payload))
        output = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=10)
        self.assertEqual(output.returncode, 0, 'Renderer fixture failed')
        return json.loads(output.stdout)

    def test_other_protocol_same_id_is_kept(self):
        output = self.run_renderer([dict(config_source=3, provider='custom_openai_compatible',
            name='custom_openai_compatible//model-a', base_url='https://example.com/v1/chat/completions')], True)
        self.assertEqual(output['result']['kept'], ['model-a'])
        self.assertEqual(output['writes'], [])

    def test_anthropic_add_and_same_connection_keep_or_update(self):
        added = self.run_renderer([], False)
        self.assertEqual(added['result']['added'], ['model-a'])
        self.assertEqual(added['writes'][0]['data']['provider'], 'custom_anthropic_compatible')
        record = dict(config_source=3, provider='custom_anthropic_compatible',
            name='custom_anthropic_compatible//model-a', base_url='https://example.com/v1/messages',
            custom_model_id='fake-id')
        kept = self.run_renderer([record], False)
        self.assertEqual(kept['writes'], [])
        updated = self.run_renderer([record], True)
        self.assertEqual(updated['result']['updated'], ['model-a'])
        self.assertEqual(updated['writes'][0]['method'], 'update_custom_model')


if __name__ == '__main__':
    unittest.main()
