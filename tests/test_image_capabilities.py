import json
from pathlib import Path
import tempfile
import subprocess
import shutil
import unittest
from unittest.mock import patch
import core
import trae_adapter
import trae_solo_adapter
from model_capabilities import image_input_support
from test_trae_adapter import make_db

BASE = 'https://platform.sgt.site/v1'
KEY = 'test-only-key'

class ImageCapabilityTests(unittest.TestCase):
    def test_metadata_overrides_names_and_does_not_confuse_output_images(self):
        self.assertTrue(image_input_support('opaque-name', {'architecture': {'input_modalities': ['text', 'image']}}))
        self.assertFalse(image_input_support('gpt-4o', {'supportsImages': False}))
        self.assertFalse(image_input_support('gpt-4o', {'input_modalities': ['text']}))
        self.assertIsNone(image_input_support('opaque-name', {'output_modalities': ['image']}))
        self.assertIsNone(image_input_support('gpt-image-1'))
        self.assertIsNone(image_input_support('unknown-text'))
        self.assertTrue(image_input_support('cx-deepseek-v4-flash-vision-exp'))

    def test_catalog_metadata_survives_duplicates_and_native_anthropic(self):
        models = core._parse_openai_catalog({'data': [
            {'id': 'a', 'input_modalities': ['text', 'image']},
            {'id': 'a', 'supportsImages': False},
            {'id': 'b', 'capabilities': {'vision': {'supported': True}}}]})
        self.assertFalse(models[0].supports_images)
        self.assertTrue(models[1].supports_images)
        self.assertTrue(core._anthropic_model({'id': 'claude-3-7-sonnet-latest'}).supports_images)

    def test_workbuddy_appends_exactly_once_and_matches_legacy_urls(self):
        for url in (BASE, BASE+'/', BASE+'/chat/completions', BASE+'/chat/completions/'):
            plan = core.make_plan(None, url, KEY, ['gpt-4o'])
            self.assertEqual(plan.document[0]['url'], BASE+'/chat/completions')
            self.assertTrue(plan.document[0]['supportsImages'])
        legacy = [{'id':'gpt-4o', 'vendor':'Custom', 'url':BASE, 'apiKey':KEY}]
        kept = core.make_plan(json.dumps(legacy).encode(), BASE, KEY, ['gpt-4o'])
        self.assertEqual(kept.document, legacy)
        updated = core.make_plan(json.dumps(legacy).encode(), BASE, KEY, ['gpt-4o'], update_existing=True)
        self.assertEqual(updated.document[0]['url'], BASE+'/chat/completions')
        repeated = core.make_plan(json.dumps(updated.document).encode(), BASE, KEY, ['gpt-4o'], update_existing=True)
        self.assertFalse(repeated.changed)

    def test_workbuddy_per_model_defaults_and_explicit_disable(self):
        metadata = {'opaque': {'supportsImages': True}, 'gpt-4o': {'supportsImages': False}}
        plan = core.make_plan(None, BASE, KEY, ['opaque','gpt-4o','unknown'], capabilities_by_model=metadata)
        self.assertEqual([m['supportsImages'] for m in plan.document], [True, False, False])
        plan = core.make_plan(None, BASE, KEY, ['opaque'], capabilities={'supportsImages':False}, capabilities_by_model=metadata)
        self.assertFalse(plan.document[0]['supportsImages'])

    def test_trae_base_url_and_mixed_model_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            target = trae_adapter.Target('Trae', Path(directory)/'state.vscdb', 'Trae')
            make_db(target.path)
            plan = trae_adapter.make_plan(target, BASE+'/chat/completions', KEY, ['gpt-4o','unknown'], capabilities_by_model={'unknown':{'supportsImages':False}})
            seen = {}
            for payload in plan.payloads.values():
                for models in json.loads(payload).values():
                    for model in models:
                        mid = model.get('name', '').split('//')[-1]
                        if mid in ('gpt-4o','unknown'):
                            self.assertEqual(model['base_url'], BASE)
                            seen[mid] = model['multimodal']
            self.assertEqual(seen, {'gpt-4o':True, 'unknown':False})

    def test_traework_payload_is_per_model_for_both_protocols(self):
        with tempfile.TemporaryDirectory() as directory:
            target = trae_adapter.Target(trae_solo_adapter.SOLO_NAME,Path(directory)/'state.vscdb','test')
            for protocol in ('openai','anthropic'):
                plan = trae_solo_adapter.make_plan(target, BASE, KEY, ['gpt-4o','unknown'], protocol=protocol,
                    capabilities_by_model={'unknown': {'supportsImages':False}})
                with patch.object(trae_solo_adapter,'ensure_solo_stopped'), patch.object(trae_solo_adapter,'discover_executable',return_value=Path('test.exe')), patch.object(trae_solo_adapter,'_run_client_import',return_value={'added':['gpt-4o','unknown'],'updated':[],'kept':[],'failed':[]}) as run:
                    trae_solo_adapter.apply_plan(target,plan)
                payload=run.call_args.args[1]
                self.assertEqual(payload['endpoint'],BASE if protocol=='openai' else BASE+'/messages')
                self.assertEqual([m['supportsImages'] for m in payload['models']],[True,False])
                self.assertIn('model.supportsImages??input.supportsImages??previousDetail.multimodal',trae_solo_adapter._expression(payload))

    @unittest.skipUnless(shutil.which('node'), 'Node required for renderer test')
    def test_real_renderer_sends_mixed_image_flags_without_global_leakage(self):
        payload = {'apiKey':KEY, 'protocol':'openai', 'provider':'custom_openai_compatible',
                   'baseUrl':BASE, 'endpoint':BASE, 'supportsImages':None, 'updateExisting':False,
                   'models':[{'id':'vision','supportsImages':True}, {'id':'text','supportsImages':False}]}
        script = """
const records=[],writes=[];
const service={getDefaultClient:async()=>({request:async({method,data})=>{
 if(method==='model_list')return {code:0,data:{model_list:records}};
 writes.push(data);
 records.push({config_source:3,name:data.provider+'//'+data.model_name,provider:data.provider,
   base_url:data.base_url,custom_model_id:data.model_name,config_detail:data.config_detail});
 return {code:0};
}})};
global.window={workbenchPromise:Promise.resolve({createDecorator:x=>x,icubeStore:{serviceCollection:{get:()=>service}}})};
EXPRESSION.then(result=>console.log(JSON.stringify({result,writes}))).catch(()=>process.exit(1));
""".replace('EXPRESSION',trae_solo_adapter._expression(payload))
        completed = subprocess.run(['node','-e',script],capture_output=True,text=True,timeout=15)
        self.assertEqual(completed.returncode,0)
        result=json.loads(completed.stdout)
        self.assertEqual(result['result']['added'],['vision','text'])
        self.assertEqual([w['config_detail']['multimodal'] for w in result['writes']],[True,False])
        self.assertEqual([w['base_url'] for w in result['writes']],[BASE,BASE])

    def test_invalid_capabilities_rejected(self):
        for value in ([], {'m':{'supportsImages':'true'}}, {'m':{'bogus':True}}):
            with self.assertRaises(core.UserError):
                core.make_plan(None,BASE,KEY,['m'],capabilities_by_model=value)
