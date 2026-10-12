"""Offline regression fixtures derived from official document schemas, no secrets."""
from pathlib import Path
import json
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core
import official_limits as official
import provider_specs as specs
import sgt_cn_specs as cn

QWEN = '''<p>模型调用 ID（ model 参数取值）： qwen3.8-max</p><table>
<tr><td>参数</td><td>值</td><td>参数</td><td>值</td></tr>
<tr><td>最大输入长度</td><td>991808</td><td>最大输出长度</td><td>131072</td></tr>
<tr><td>最大输入长度（思考模式下）</td><td>983616</td><td>最大输出长度（思考模式下）</td><td>131072</td></tr>
<tr><td>上下文长度</td><td>1000000</td><td>最大思维链长度</td><td>262144</td></tr></table>'''
GLM = 'Model Code：`glm-5.3`\n支持 1M 上下文窗口，最大输出 Tokens 为 128K。'
GLM_FLASH = '''Model Code：`glm-5.3-flash`
<Card title="上下文窗口" icon={<svg />} >1M</Card>
<Card title="最大输出" icon={<svg />} >128K</Card>'''.replace('/} >','/}>')
MIMO = '''<table><tr><td>`mimo-v2.5`</td><td>Text Generation</td><td>Context Window: 1M<br/>Maximum Output: 128K</td><td>TPM: 10M</td></tr></table>'''
KIMI = '''<Accordion title="Kimi 大模型的输出长度是多少">
* 对于 `kimi-k3` 模型而言，`max_completion_tokens` 默认为 131072，最大输出长度是 `1024*1024 - prompt_tokens`；
* 对于 `kimi-k2.7-code`、`kimi-k2.6` 模型而言，最大输出长度是 `256*1024 - prompt_tokens`；
</Accordion>'''
VOLC_MD = '|[doubao-seed-2-1-pro-260628](https://example.invalid) |文本生成 |上下文窗口: 256k<br>最大输入: 256k<br>[最大回答(默认 4k)](https://example.invalid): 256k<br>最大思维链: 512k |最大 TPM: 1000000 |'
VOLC = json.dumps({'Result':{'DocumentID':1330310,'LibraryID':82379,'MDContent':VOLC_MD}})
HY = json.dumps({'code':0,'data':{'hyModelConfig':{'models':[{'tryLink':'/?model=hy4-preview','modelDesc':'1M 上下文'}]}}})


class ChineseSpecsTests(unittest.TestCase):
    def enrich(self, ids, doc):
        with patch.object(specs,'read_document',return_value=doc):
            return specs.enrich_sgt_catalog([core.GatewayModel(mid) for mid in ids])

    def test_qwen_four_column_table_and_thinking_not_output(self):
        spec = cn.parse_qwen(QWEN,'qwen3.8-max')
        self.assertEqual((spec.input_limit,spec.output_limit,spec.safe_input_limit),(991808,131072,983616))
        self.assertEqual(spec.context_limit,1000000)

    def test_qwen_identity_not_navigation_or_similar_name(self):
        self.assertIsNone(cn.parse_qwen(QWEN,'qwen3.8-max-other'))
        self.assertIsNone(cn.parse_qwen(QWEN.replace('模型调用 ID（ model 参数取值）：','导航'),'qwen3.8-max'))

    def test_qwen_conflicting_region_specs_rejected(self):
        self.assertIsNone(cn.parse_qwen(QWEN+QWEN.replace('131072','65536'),'qwen3.8-max'))

    def test_qwen_official_display_separate_from_import_budget(self):
        model = self.enrich(['qwen3.8-max'],QWEN)[0]
        self.assertEqual((model.input_display,model.output_display),('991808','131072'))
        self.assertEqual(model.max_input_tokens,868928)
        self.assertIn('983616',model.limit_note)

    def test_glm_prose_and_identity(self):
        self.assertEqual(cn.parse_glm(GLM,'glm-5.3').output_limit,128000)
        self.assertIsNone(cn.parse_glm(GLM,'glm-5.3-flash'))

    def test_glm_card_and_case_normalization_only_for_specs(self):
        card = 'Model Code：`glm-5.3-flash`<Card title="上下文窗口" icon={<svg />} >1M</Card><Card title="最大输出" icon={<svg />} >128K</Card>'
        # Preserve the exact public JSX close syntax: SVG />, expression }, Card >.
        card = card.replace('/>} >','/>}>')
        model = self.enrich(['GLM-5.3-Flash'],card)[0]
        self.assertEqual(model.id,'GLM-5.3-Flash')
        self.assertEqual(model.max_output_tokens,128000)

    def test_mimo_same_row_limits_not_rate_limits(self):
        self.assertEqual(cn.parse_mimo(MIMO,'mimo-v2.5').output_limit,128000)
        self.assertIsNone(cn.parse_mimo(MIMO,'mimo-v2.5-pro'))

    def test_kimi_exact_formula_not_default_as_maximum(self):
        model = self.enrich(['kimi-k3'],KIMI)[0]
        self.assertEqual(model.input_display,'≤1048576')
        self.assertEqual(model.output_display,'1048576－本次输入')
        self.assertEqual(model.token_limits,{'maxInputTokens':917504,'maxOutputTokens':131072})

    def test_kimi_no_default_uses_explicit_safe_budget(self):
        model = self.enrich(['kimi-k2.7-code'],KIMI)[0]
        self.assertEqual(model.output_display,'262144－本次输入')
        self.assertEqual(model.token_limits,{'maxInputTokens':131072,'maxOutputTokens':131072})
        self.assertIn('半窗口预算',model.limit_note)

    def test_kimi_changed_formula_fails_closed(self):
        self.assertIsNone(cn.parse_kimi(KIMI.replace('1024*1024','unknown'),'kimi-k3'))
        self.assertIsNone(cn.parse_kimi(KIMI,'kimi-k3-other'))

    def test_volc_exact_deployment_id_and_answer_not_thinking_or_default(self):
        spec = cn.parse_volc(VOLC,'doubao-seed-2-1-pro-260628')
        self.assertEqual((spec.input_limit,spec.output_limit),(256000,256000))
        self.assertIsNone(cn.parse_volc(VOLC,'doubao-seed-2-1-pro'))

    def test_volc_ambiguous_rows_and_wrong_doc_rejected(self):
        bad=json.dumps({'Result':{'DocumentID':1330310,'LibraryID':82379,'MDContent':VOLC_MD+'\n'+VOLC_MD.replace(': 256k',': 128k')}})
        self.assertIsNone(cn.parse_volc(bad,'doubao-seed-2-1-pro-260628'))
        self.assertIsNone(cn.parse_volc(VOLC.replace('1330310','1'),'doubao-seed-2-1-pro-260628'))

    def test_volc_equal_caps_do_not_make_zero_input_budget(self):
        model = self.enrich(['doubao-seed-2-1-pro-260628'],VOLC)[0]
        self.assertEqual((model.input_display,model.output_display),('256000','256000'))
        self.assertEqual(model.token_limits,{'maxInputTokens':128000,'maxOutputTokens':128000})

    def test_hunyuan_missing_output_is_not_invented(self):
        model=self.enrich(['hy4-preview'],HY)[0]
        self.assertEqual(model.input_display,'≤1000000')
        self.assertEqual(model.output_display,'未取得官方值')
        self.assertNotIn('maxOutputTokens',model.token_limits)

    def test_hunyuan_unmatched_model_and_error_response(self):
        self.assertIsNone(cn.parse_hunyuan(HY,'hy3-preview'))
        self.assertIsNone(cn.parse_hunyuan(HY.replace('"code": 0','"code": 1'),'hy4-preview'))

    def test_media_models_have_explicit_not_applicable_and_zero_fetches(self):
        ids=['doubao-seedance-2-5-260628','doubao-seedream-5-0-pro-260628','MiniMax-H3']
        with patch.object(specs,'read_document') as fetch:
            models=specs.enrich_sgt_catalog([core.GatewayModel(mid) for mid in ids])
        fetch.assert_not_called()
        for model in models:
            self.assertEqual((model.input_display,model.output_display),('仅支持API导入','仅支持API导入'))
            self.assertEqual(model.token_limits,{})

    def test_malformed_provider_json_does_not_drop_catalog(self):
        for doc in ('[]','{"Result": []}','not json'):
            self.assertEqual(self.enrich(['doubao-seed-test'],doc)[0].token_limits,{})

    def test_official_deepseek_explicit_alias_mapping_only(self):
        pricing='<table><tr><td>MODEL</td><td>deepseek-flash</td></tr><tr><td>CONTEXT LENGTH</td><td>1M</td></tr><tr><td>MAX OUTPUT</td><td>384K</td></tr></table>'
        footnote='<p>Use deepseek-flash as the model name. The legacy names deepseek-v4-flash and deepseek-v4-flash-vision-exp are still accepted, but the corresponding models have been retired, their requests are served by the new model.</p>'
        api='<p>384K (393216)</p>'
        parsed=official.parse_deepseek_specs(pricing+footnote,api)
        self.assertEqual(parsed['deepseek-v4-flash-vision-exp'],parsed['deepseek-flash'])
        self.assertNotIn('deepseek-v4-flash-vision-exp',official.parse_deepseek_specs(pricing,api))

    def test_cn_routes_do_not_remove_gateway_prefixes_or_experiment_suffixes(self):
        self.assertIsNone(cn.route('cx-qwen3.8-max'))
        self.assertIsNone(cn.route('GLM-5.3-Flash-custom'))
        self.assertEqual(cn.route('deepseek-v4-pro-ga-260813')[2],cn.VOLC_URL)

    def test_hunyuan_request_is_readonly_public_config_without_auth(self):
        import io
        with patch.object(specs.urllib.request, 'build_opener') as build:
            build.return_value.open.return_value = io.BytesIO(HY.encode())
            specs.read_document(cn.HY_URL)
        request = build.return_value.open.call_args.args[0]
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(json.loads(request.data), {'keys': ['hyModelConfig']})
        self.assertIsNone(request.get_header('Authorization'))
        self.assertIsNone(request.get_header('Cookie'))

    def test_observed_text_models_all_have_document_routes(self):
        ids = ('qwen3.8-max', 'qwen3.8-flash', 'GLM-5.3-Flash', 'glm-5.3',
               'mimo-v2.5', 'mimo-v2.5-pro', 'kimi-k3', 'kimi-k2.7-code', 'hy4-preview',
               'doubao-seed-2-1-pro-260628', 'doubao-seed-2-1-turbo-260628', 'doubao-seed-evolving',
               'deepseek-v4-flash', 'deepseek-flash', 'deepseek-v4-pro', 'deepseek-v4-flash-vision-exp',
               'deepseek-v4-pro-ga-260813', 'deepseek-v4-flash-ga-260731')
        for model_id in ids:
            with self.subTest(model_id=model_id):
                self.assertIsNotNone(specs.model_document(model_id))

    def test_provider_network_failure_does_not_affect_other_models(self):
        with patch.object(specs,'read_document',side_effect=lambda url: QWEN if 'aliyun' in url else (_ for _ in ()).throw(OSError('offline'))):
            models=specs.enrich_sgt_catalog([core.GatewayModel('qwen3.8-max'),core.GatewayModel('hy4-preview')])
        self.assertEqual(models[0].max_output_tokens,131072)
        self.assertEqual(models[1].token_limits,{})


if __name__ == '__main__':unittest.main()
