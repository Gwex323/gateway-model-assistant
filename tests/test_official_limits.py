"""Offline official-spec fixtures: public docs, never real models or keys."""
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core
import official_limits as limits

TABLE = '''<table>
<tr><td colspan="3">MODEL</td><td>deepseek-test-a<sup>(1)</sup></td><td>deepseek-test-b</td></tr>
<tr><td colspan="3">CONTEXT LENGTH</td><td colspan="2">1M</td></tr>
<tr><td colspan="3">MAX OUTPUT</td><td colspan="2">MAXIMUM: 384K</td></tr>
</table>'''
API = '<p>max_tokens must be between 1 and 384K (393216).</p>'


class OfficialLimitsTests(unittest.TestCase):
    def connection(self, url='https://api.deepseek.com/anthropic'):
        return core.resolve_connection(url, 'anthropic')

    def test_table_colspan_and_footnote(self):
        self.assertEqual(limits.parse_deepseek_specs(TABLE, API), {
            'deepseek-test-a': (1000000, 393216), 'deepseek-test-b': (1000000, 393216),
        })

    def test_shorthand_not_guessed_for_output(self):
        self.assertEqual(limits.parse_deepseek_specs(TABLE, ''), {})

    def test_missing_or_changed_table_is_safe(self):
        for table in ('<html>Error</html>', TABLE.replace('MAX OUTPUT', 'UNKNOWN'),
                      TABLE.replace('colspan="2"', 'colspan="1"')):
            self.assertEqual(limits.parse_deepseek_specs(table, API), {})

    def test_bad_table_span_and_conflicting_units_rejected(self):
        with self.assertRaises(ValueError):
            limits.parse_deepseek_specs(TABLE.replace('colspan="3"', 'colspan="100000"'), API)
        with self.assertRaises(ValueError):
            limits.parse_deepseek_specs(TABLE, API + '<p>384K (384000)</p>')

    def test_input_budget_reserves_output(self):
        model = core.GatewayModel('deepseek-test-a')
        with patch.object(limits, 'fetch_deepseek_specs', return_value={'deepseek-test-a': (1000000, 393216)}):
            result = limits.enrich_catalog(self.connection(), [model])[0]
        self.assertEqual(result.token_limits, {'maxInputTokens': 606784, 'maxOutputTokens': 393216})
        self.assertIn('保守输入预算', result.limit_source)
        self.assertEqual(result.limit_source_url, limits.PRICING_URL)

    def test_complete_upstream_metadata_does_not_fetch_docs(self):
        model = core.GatewayModel('deepseek-test-a', 12345, 6789)
        with patch.object(limits, 'fetch_deepseek_specs') as fetch:
            self.assertEqual(limits.enrich_catalog(self.connection(), [model]), [model])
        fetch.assert_not_called()

    def test_partial_upstream_metadata_wins(self):
        model = core.GatewayModel('deepseek-test-a', None, 8192)
        with patch.object(limits, 'fetch_deepseek_specs', return_value={'deepseek-test-a': (1000000, 393216)}):
            result = limits.enrich_catalog(self.connection(), [model])[0]
        self.assertEqual(result.max_output_tokens, 8192)
        self.assertEqual(result.max_input_tokens, 991808)

    def test_conflicting_upstream_input_not_overwritten(self):
        model = core.GatewayModel('deepseek-test-a', 999999)
        with patch.object(limits, 'fetch_deepseek_specs', return_value={'deepseek-test-a': (1000000, 393216)}):
            result = limits.enrich_catalog(self.connection(), [model])[0]
        self.assertEqual(result.token_limits, model.token_limits)
        self.assertIn('冲突', result.limit_source)

    def test_unknown_model_does_not_inherit_other_model_limits(self):
        with patch.object(limits, 'fetch_deepseek_specs', return_value={'deepseek-test-a': (1000000, 393216)}):
            model = limits.enrich_catalog(self.connection(), [core.GatewayModel('deepseek-future')])[0]
        self.assertEqual(model.token_limits, {})
        self.assertIn('未匹配', model.limit_source)

    def test_docs_failure_preserves_catalog(self):
        with patch.object(limits, 'fetch_deepseek_specs', side_effect=OSError('offline')):
            model = limits.enrich_catalog(self.connection(), [core.GatewayModel('deepseek-test-a')])[0]
        self.assertEqual(model.id, 'deepseek-test-a')
        self.assertEqual(model.token_limits, {})

    def test_third_party_host_or_path_never_uses_official_limits(self):
        for url in ('https://gateway.example/anthropic', 'https://api.deepseek.com.evil.example/anthropic',
                    'https://api.deepseek.com:8443/anthropic', 'https://api.deepseek.com/proxy'):
            with patch.object(limits, 'fetch_deepseek_specs') as fetch:
                model = core.GatewayModel('deepseek-test-a')
                self.assertEqual(limits.enrich_catalog(self.connection(url), [model]), [model])
                fetch.assert_not_called()

    def test_public_documents_never_receive_auth_headers(self):
        with patch.object(limits.urllib.request, 'build_opener') as build:
            build.return_value.open.return_value = io.BytesIO(b'<html></html>')
            limits._read_document(limits.PRICING_URL)
        request = build.return_value.open.call_args.args[0]
        self.assertIsNone(request.get_header('Authorization'))
        self.assertIsNone(request.get_header('X-api-key'))

    def test_public_redirect_refuses_other_origins(self):
        redirect = limits.PublicDocsRedirect()
        for url in ('http://api-docs.deepseek.com/', 'https://evil.example/',
                    'https://api-docs.deepseek.com:8443/'):
            self.assertIsNone(redirect.redirect_request(None, None, 302, '', {}, url))

    def test_fetch_pipeline_supports_both_protocols(self):
        for protocol in ('openai', 'anthropic'):
            fetcher = core.fetch_model_catalog if protocol == 'openai' else core.fetch_anthropic_model_catalog
            url = 'https://api.deepseek.com' + ('/anthropic' if protocol == 'anthropic' else '')
            with patch.object(core, '_request_json', return_value={'data': [{'id': 'deepseek-test-a'}]}), \
                    patch.object(limits, '_read_document', side_effect=[TABLE, API]):
                result = fetcher(url, 'fake-official-test-key')
            self.assertEqual(result[0].max_output_tokens, 393216)
            self.assertEqual(result[0].max_input_tokens, 606784)


if __name__ == '__main__':
    unittest.main()
