"""Offline SGT official model specifications; never uses gateway credentials."""
from pathlib import Path
import io
import sys
import unittest
from unittest.mock import patch
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core
import official_limits
import provider_specs as specs

OPENAI_URL = 'https://developers.openai.com/api/docs/models/gpt-test'
OPENAI = '<link rel="canonical" href="'+OPENAI_URL+'"><p>200,000 context window 32,000 max output tokens</p>'
GEMINI = '''<table><tr><td>Model code</td><td>gemini-test</td></tr>
<tr><td>Token limits</td><td>Input token limit 200,000 Output token limit 32,000</td></tr></table>'''
CLAUDE = '''| Platform | Model ID |
| Claude API | `claude-sonnet-4-6` |
| Claude API alias | `claude-sonnet-4-6-alias` |
| [Context window](https://example.invalid/) | 200K tokens |
| Max output | 32K tokens |
| Max output (Batch API, beta) | 300K tokens |
'''


class SGTSpecTests(unittest.TestCase):
    def enrich(self, models, document=OPENAI):
        connection = core.resolve_connection('https://platform.sgt.site/v1', 'openai')
        with patch.object(specs, 'read_document', return_value=document):
            return official_limits.enrich_catalog(connection, models)

    def test_official_image_input_is_carried_into_catalog(self):
        model = self.enrich([core.GatewayModel('gpt-test')], OPENAI + '<p>Image Input only</p>')[0]
        self.assertTrue(model.supports_images)
        model = self.enrich([core.GatewayModel('gpt-test', supports_images=False)], OPENAI + '<p>Image Input only</p>')[0]
        self.assertFalse(model.supports_images)
        model = self.enrich([core.GatewayModel('gpt-test')], OPENAI + '<p>Image Output only</p>')[0]
        self.assertIsNone(model.supports_images)

    def test_openai_exact_context_budget(self):
        model = self.enrich([core.GatewayModel('gpt-test')])[0]
        self.assertEqual(model.token_limits, {'maxInputTokens':168000, 'maxOutputTokens':32000})
        self.assertEqual(model.limit_source_url, OPENAI_URL)
        self.assertIn('SGT', model.limit_source)

    def test_gemini_explicit_input_not_subtracted(self):
        model = self.enrich([core.GatewayModel('gemini-test')], GEMINI)[0]
        self.assertEqual(model.token_limits, {'maxInputTokens':200000, 'maxOutputTokens':32000})

    def test_claude_context_budget_ignores_batch_beta(self):
        model = self.enrich([core.GatewayModel('claude-sonnet-4-6')], CLAUDE)[0]
        self.assertEqual(model.token_limits, {'maxInputTokens':168000, 'maxOutputTokens':32000})
        self.assertIn('单位取保守值', model.limit_source)

    def test_deepseek_exact_specs_reused(self):
        with patch.object(specs, 'fetch_deepseek_specs', return_value={'deepseek-test':(200000,32000)}):
            model = self.enrich([core.GatewayModel('deepseek-test')])[0]
        self.assertEqual(model.max_input_tokens, 168000)
        self.assertEqual(model.max_output_tokens, 32000)

    def test_unknown_alias_never_inherits_similar_name(self):
        with patch.object(specs, 'fetch_deepseek_specs', return_value={'deepseek-test':(200000,32000)}):
            model = self.enrich([core.GatewayModel('deepseek-test-vision-exp')])[0]
        self.assertEqual(model.token_limits, {})

    def test_known_namespace_preserves_original_id(self):
        model = self.enrich([core.GatewayModel('openai/gpt-test')])[0]
        self.assertEqual(model.id, 'openai/gpt-test')
        self.assertEqual(model.max_output_tokens, 32000)
        self.assertIsNone(specs.model_document('anthropic/gpt-test'))
        self.assertIsNone(specs.model_document('sgt/gpt-test'))

    def test_complete_upstream_never_fetches(self):
        model = core.GatewayModel('gpt-test', 1234, 567)
        with patch.object(specs, 'read_document') as fetch:
            self.assertEqual(specs.enrich_sgt_catalog([model]), [model])
        fetch.assert_not_called()

    def test_partial_upstream_output_preserved(self):
        model = self.enrich([core.GatewayModel('gpt-test', None, 10000)])[0]
        self.assertEqual(model.token_limits, {'maxInputTokens':190000, 'maxOutputTokens':10000})

    def test_partial_input_preserved(self):
        model = self.enrich([core.GatewayModel('gpt-test', 10000)])[0]
        self.assertEqual(model.token_limits, {'maxInputTokens':10000, 'maxOutputTokens':32000})

    def test_conflicts_not_overwritten(self):
        for model in (core.GatewayModel('gpt-test',190000), core.GatewayModel('gpt-test',None,300000)):
            result = self.enrich([model])[0]
            self.assertEqual(result.token_limits, model.token_limits)
            self.assertIn('冲突', result.limit_source)

    def test_unavailable_docs_keep_model(self):
        with patch.object(specs, 'read_document', side_effect=OSError('offline')):
            model = specs.enrich_sgt_catalog([core.GatewayModel('gpt-test')])[0]
        self.assertEqual(model.id, 'gpt-test')
        self.assertEqual(model.token_limits, {})

    def test_host_and_path_isolation(self):
        for url in ('https://platform.sgt.site.evil.example/v1', 'https://platform.sgt.site:8443/v1',
                    'https://platform.sgt.site/proxy/v1', 'https://other.example/v1'):
            with patch.object(specs, 'enrich_sgt_catalog') as enrich:
                models = [core.GatewayModel('gpt-test')]
                self.assertEqual(official_limits.enrich_catalog(core.resolve_connection(url,'openai'), models), models)
                enrich.assert_not_called()

    def test_model_page_identity_and_conflicting_specs(self):
        self.assertIsNone(specs.parse_openai(OPENAI.replace('gpt-test','gpt-other'), 'gpt-test', OPENAI_URL))
        self.assertIsNone(specs.parse_openai(OPENAI+'<p>100,000 context window 10,000 max output tokens</p>', 'gpt-test', OPENAI_URL))
        self.assertIsNone(specs.parse_gemini(GEMINI, 'gemini-other', ''))
        self.assertIsNone(specs.parse_gemini(GEMINI.replace('200,000','2M'), 'gemini-test', ''))
        self.assertIsNone(specs.parse_claude(CLAUDE, 'claude-sonnet-4-6-20260101', ''))
        self.assertIsNone(specs.parse_claude(CLAUDE+'| Max output | 64K tokens |', 'claude-sonnet-4-6', ''))

    def test_dated_claude_identity_and_url(self):
        plan = specs.model_document('claude-haiku-4-5-20251001')
        self.assertEqual(plan[2], 'https://platform.claude.com/docs/en/models/haiku-4-5/overview')
        doc = CLAUDE.replace('claude-sonnet-4-6', 'claude-haiku-4-5-20251001')
        self.assertIsNotNone(specs.parse_claude(doc, plan[1], plan[2]))

    def test_malformed_ids_cannot_construct_external_urls(self):
        for name in ('gpt-../../evil','gpt-test?url=https://evil.example','gpt-test#fragment','https://evil.example','gpt-test%2fother'):
            self.assertIsNone(specs.model_document(name))

    def test_number_bounds(self):
        for number in ('0', '-1', '1.2', '100000001', 'unknown', '1.2.3', 'True'):
            self.assertIsNone(specs.number(number))
        self.assertEqual(specs.number('1,048,576'),1048576)

    def test_duplicate_page_download_once(self):
        with patch.object(specs, 'read_document', return_value=OPENAI) as fetch:
            result = specs.enrich_sgt_catalog([core.GatewayModel('gpt-test'),core.GatewayModel('openai/gpt-test')])
        self.assertEqual(fetch.call_count,1)
        self.assertTrue(all(m.max_output_tokens == 32000 for m in result))

    def test_document_count_bounded_without_losing_models(self):
        with patch.object(specs, 'MAX_DOCUMENTS', 1), patch.object(specs, 'read_document', return_value=OPENAI) as fetch:
            result = specs.enrich_sgt_catalog([core.GatewayModel('gpt-test'), core.GatewayModel('gpt-another')])
        self.assertEqual(fetch.call_count,1)
        self.assertEqual(len(result),2)
        self.assertIn('数量达上限',result[1].limit_source)

    def test_unsupported_model_remains_selectable(self):
        result = self.enrich([core.GatewayModel('unknown-model')])[0]
        self.assertEqual(result.id, 'unknown-model')
        self.assertIn('未识别', result.limit_source)

    def test_network_no_credentials_and_redirect_guard(self):
        with patch.object(specs.urllib.request, 'build_opener') as build:
            build.return_value.open.return_value = io.BytesIO(b'<html></html>')
            specs.read_document(OPENAI_URL)
        request = build.return_value.open.call_args.args[0]
        self.assertIsNone(request.get_header('Authorization'))
        self.assertIsNone(request.get_header('X-api-key'))
        self.assertNotIn('Cookie', dict(request.header_items()))
        handler = specs.SameOriginRedirect()
        for url in ('http://developers.openai.com/', 'https://evil.example/',
                    'https://ai.google.dev/', 'https://developers.openai.com:8443/',
                    'https://user:secret@developers.openai.com/'):
            self.assertIsNone(handler.redirect_request(request,None,302,'',{},url))
        with self.assertRaises(ValueError):
            specs.read_document('https://evil.example/')

    def test_both_model_discovery_protocols_use_official_fallback(self):
        for fetcher in (core.fetch_model_catalog, core.fetch_anthropic_model_catalog):
            with patch.object(core, '_request_json', return_value={'data':[{'id':'gpt-test'}]}), patch.object(specs,'read_document',return_value=OPENAI):
                model = fetcher('https://platform.sgt.site/v1','fake-sgt-test-key')[0]
            self.assertEqual(model.max_output_tokens,32000)
            self.assertEqual(model.max_input_tokens,168000)


if __name__ == '__main__':
    unittest.main()
