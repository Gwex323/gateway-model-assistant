"""Offline MiMo table-schema regression; no real credentials or inference."""
import unittest
from unittest.mock import patch

import core
import provider_specs
import sgt_cn_specs


# Synthetic rows covering multiple IDs, retirement notes, and rowspan-shifted cells.
MIMO_TABLE = '''<table>
<tr><th>Model ID</th><th>Capability</th><th>Length</th><th>Rate</th></tr>
<tr><td><span>`mimo-v2.6-pro`</span><br/><code>mimo-v2.6-flash</code></td>
<td rowspan="3">Text Generation</td>
<td>Context Window: 1M<br/>Maximum Output: 128K</td><td>TPM: 10M</td></tr>
<tr><td><code>mimo-v2.6-pro-ultraspeed</code></td>
<td>Context Window: 1M<br/>Maximum Output: 128K</td><td>Contact</td></tr>
<tr><td>`mimo-v2.5`(to be deprecated)</td>
<td>Context Window: 1M<br/>Maximum Output: 128K</td><td>TPM: 10M</td></tr>
<tr><td><span>`mimo-v2.5-pro`(to be deprecated)</span></td><td>Text</td>
<td>Context Window: 1M<br/>Maximum Output: 128K</td><td>TPM: 10M</td></tr>
</table>'''
TEXT_IDS = ('mimo-v2.6-pro', 'mimo-v2.6-flash', 'mimo-v2.6-pro-ultraspeed',
            'mimo-v2.5', 'mimo-v2.5-pro')


class MiMoSpecsTests(unittest.TestCase):
    def test_shared_id_cell_rowspan_and_retirement_note(self):
        for mid in TEXT_IDS:
            with self.subTest(mid=mid):
                spec = sgt_cn_specs.parse_mimo(MIMO_TABLE, mid)
                self.assertIsNotNone(spec)
                self.assertEqual((spec.input_limit, spec.output_limit), (1000000, 128000))
                self.assertTrue(spec.shared_context)

    def test_routes_preserve_exact_ids_and_allow_only_explicit_namespaces(self):
        for mid in TEXT_IDS:
            for prefix in ('', 'xiaomi/', 'mimo/'):
                with self.subTest(mid=prefix + mid):
                    self.assertEqual(provider_specs.model_document(prefix + mid),
                                     ('cn', mid, sgt_cn_specs.MIMO_URL))
        for mid in ('cx-mimo-v2.6-pro', 'unknown/mimo-v2.6-pro', 'mimo-v2.6-pro-custom',
                    'mimo-v2.5-asr', 'mimo-v2.5-tts'):
            self.assertIsNone(sgt_cn_specs.route(mid))

    def test_no_cross_row_identity_prefix_or_rate_limit_guess(self):
        bad_pages = (
            '<table><tr><td>other</td><td>mimo-v2.6-pro</td>'
            '<td>Context Window: 1M Maximum Output: 128K</td></tr></table>',
            '<table><tr><td>mimo-v2.6-pro</td><td>TPM: 10M RPM: 100</td></tr></table>',
            '<table><tr><td>mimo-v2.6-pro</td><td>Context Window: 1M</td></tr>'
            '<tr><td>other</td><td>Maximum Output: 128K</td></tr></table>',
            MIMO_TABLE.replace('mimo-v2.6-pro`', 'mimo-v2.6-pro-other`'),
            MIMO_TABLE.replace('Context Window: 1M', 'Context Window: unknown'),
        )
        for page in bad_pages:
            with self.subTest(page=page):
                self.assertIsNone(sgt_cn_specs.parse_mimo(page, 'mimo-v2.6-pro'))
        conflict = MIMO_TABLE + '<table><tr><td>mimo-v2.6-pro</td>' \
            '<td>Context Window: 256K Maximum Output: 32K</td></tr></table>'
        self.assertIsNone(sgt_cn_specs.parse_mimo(conflict, 'mimo-v2.6-pro'))

    def test_enrichment_uses_shared_window_budget_and_preserves_upstream_values(self):
        ids = (*TEXT_IDS, 'xiaomi/mimo-v2.6-flash')
        with patch.object(provider_specs, 'read_document', return_value=MIMO_TABLE) as fetch:
            models = provider_specs.enrich_sgt_catalog([core.GatewayModel(mid) for mid in ids])
        fetch.assert_called_once_with(sgt_cn_specs.MIMO_URL)
        for model in models:
            self.assertEqual(model.token_limits, {'maxInputTokens': 872000, 'maxOutputTokens': 128000})
            self.assertEqual((model.input_display, model.output_display), ('≤1000000', '128000'))
            self.assertEqual(model.limit_source_url, sgt_cn_specs.MIMO_URL)
        upstream = core.GatewayModel('mimo-v2.6-pro', 200000, 8192)
        with patch.object(provider_specs, 'read_document', return_value=MIMO_TABLE):
            model = provider_specs.enrich_sgt_catalog([upstream])[0]
        self.assertEqual(model.token_limits, upstream.token_limits)

    def test_text_and_audio_model_types_are_not_confused(self):
        for mid in TEXT_IDS:
            self.assertIs(core.chat_model_support(mid), True)
        for mid in ('mimo-v2.5-asr', 'mimo-v2.5-tts', 'mimo-v2.5-tts-voiceclone'):
            self.assertIs(core.chat_model_support(mid), False)
