"""Display names are Trae-only labels, never provider model identifiers."""
from pathlib import Path
from contextlib import closing
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core
import trae_adapter as trae
import trae_solo_adapter as solo
from test_trae_adapter import make_db

BASE = 'https://old.invalid/v1'
KEY = 'fake-display-name-test-key'


class DisplayNameAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'state.vscdb'
        make_db(self.path)
        self.target = trae.Target('Test Trae', self.path, 'Trae')
        self.solo = trae.Target(solo.SOLO_NAME, self.path, solo.SOLO_NAME)

    def tearDown(self):
        self.temp.cleanup()

    def plan_records(self, plan):
        return json.loads(next(iter(plan.payloads.values())))['builder']

    def test_validation_blank_trim_unicode_and_control_characters(self):
        self.assertEqual(trae.validate_display_names({'m':' 中文 名称 '}, ['m']), {'m':'中文 名称'})
        self.assertEqual(trae.validate_display_names({'m':'   '}, ['m']), {})
        for values in ([], {'other':'Name'}, {'m':3}, {'m':'x\ny'}, {'m':'x\x00'}, {'m':'x'*129}):
            with self.subTest(values=values), self.assertRaises(core.UserError):
                trae.validate_display_names(values,['m'])

    def test_trae_new_name_does_not_change_identity_and_applies_to_all_modes(self):
        plan = trae.make_plan(self.target, BASE, KEY, ['model-a'], display_names_by_model={'model-a':'代码助手'})
        for models in json.loads(next(iter(plan.payloads.values()))).values():
            model = next(m for m in models if m.get('custom_model_id')=='model-a')
            self.assertEqual(model['name'], 'custom_openai_compatible//model-a')
            self.assertEqual(model['display_name'], '代码助手')
        self.assertEqual(plan.display_names, {'model-a':'代码助手'})

    def test_trae_unspecified_new_name_is_id_not_exemplar_name(self):
        model = next(m for m in self.plan_records(trae.make_plan(self.target,BASE,KEY,['model-a'])) if m.get('custom_model_id')=='model-a')
        self.assertEqual(model['display_name'], 'model-a')

    def test_trae_existing_keep_policy_ignores_new_label(self):
        plan=trae.make_plan(self.target,BASE,KEY,['old'],display_names_by_model={'old':'新名称'})
        self.assertFalse(plan.changed)
        self.assertEqual(plan.rows,[('old','保留已有')])

    def test_trae_other_connection_is_not_renamed(self):
        plan=trae.make_plan(self.target,'https://another.invalid/v1',KEY,['old'],update_existing=True,display_names_by_model={'old':'新名称'})
        self.assertFalse(plan.changed)
        self.assertEqual(plan.rows,[('old','保留其他连接')])

    def test_trae_overwrite_changes_only_label_not_model_identity(self):
        plan=trae.make_plan(self.target,BASE,KEY,['old'],update_existing=True,display_names_by_model={'old':'新名称'})
        record=next(m for m in self.plan_records(plan) if m.get('custom_model_id')=='old')
        self.assertEqual(record['display_name'],'新名称')
        self.assertEqual(record['name'],'custom_openai_compatible//old')

    def test_trae_overwrite_without_explicit_name_preserves_existing(self):
        with closing(sqlite3.connect(self.path)) as conn:
            key,raw=conn.execute('SELECT key,value FROM ItemTable').fetchone()
            maps=json.loads(raw)
            for records in maps.values():
                for record in records:
                    if record.get('custom_model_id')=='old':record['display_name']='用户已有的名称'
            conn.execute('UPDATE ItemTable SET value=? WHERE key=?',(json.dumps(maps),key))
            conn.commit()
        plan=trae.make_plan(self.target,BASE,KEY,['old'],update_existing=True,display_names_by_model={'old':''})
        record=next(m for m in self.plan_records(plan) if m.get('custom_model_id')=='old')
        self.assertEqual(record['display_name'],'用户已有的名称')

    def test_solo_plan_copies_names_and_payload_keeps_ids_both_protocols(self):
        for protocol in ('openai','anthropic'):
            names={'model-a':'编码助手'}
            plan=solo.make_plan(self.solo,BASE,KEY,['model-a'],protocol=protocol,display_names_by_model=names)
            names['model-a']='外部变更'
            with patch.object(solo,'ensure_solo_stopped'), patch.object(solo,'discover_executable',return_value=Path('fake.exe')), patch.object(solo,'_run_client_import',return_value={'added':['model-a'],'failed':[]}) as run:
                solo.apply_plan(self.solo,plan)
            self.assertEqual(run.call_args.args[1]['models'],[{'id':'model-a','displayName':'编码助手'}])

    def test_solo_unspecified_name_not_forced_into_payload(self):
        plan=solo.make_plan(self.solo,BASE,KEY,['model-a'],display_names_by_model={'model-a':''})
        with patch.object(solo,'ensure_solo_stopped'), patch.object(solo,'discover_executable',return_value=Path('fake.exe')), patch.object(solo,'_run_client_import',return_value={'added':[],'failed':[]}) as run:
            solo.apply_plan(self.solo,plan)
        self.assertEqual(run.call_args.args[1]['models'],[{'id':'model-a'}])


@unittest.skipUnless(shutil.which('node'), 'Node is required for renderer tests')
class DisplayNameRendererTests(unittest.TestCase):
    def run_renderer(self, records=(), name='代码助手', overwrite=True, protocol='openai', persist=True):
        provider=solo.PROVIDERS[protocol]
        model={'id':'model-a'}
        if name is not None:model['displayName']=name
        payload={'provider':provider,'protocol':protocol,'baseUrl':BASE,'endpoint':BASE+('/messages' if protocol=='anthropic' else '/chat/completions'), 'apiKey':KEY,'models':[model],'updateExisting':overwrite}
        script=r'''
let records=RECORDS;
const writes=[];
global.setTimeout=fn=>{fn();return 0;};
const service={getDefaultClient:async()=>({request:async({method,data})=>{
 if(method==='model_list')return {code:0,data:{model_list:records}};
 writes.push({method,data});
 if(method==='add_custom_model')records.push({name:data.provider+'//'+data.model_name,provider:data.provider,custom_model_id:'server-record-42',base_url:data.base_url,config_source:3,display_name:PERSIST?data.display_name:'not-saved'});
 if(method==='update_custom_model'&&PERSIST){const existing=records.find(r=>r.custom_model_id===data.id);if(existing)existing.display_name=data.display_name;}
 return {code:0};
}})};
global.window={workbenchPromise:Promise.resolve({createDecorator:x=>x,icubeStore:{serviceCollection:{get:()=>service}}})};
EXPRESSION.then(result=>console.log(JSON.stringify({result,writes,records}))).catch(e=>{console.error(e.message);process.exitCode=1;});
'''.replace('RECORDS',json.dumps(records)).replace('PERSIST','true' if persist else 'false').replace('EXPRESSION',solo._expression(payload))
        output=subprocess.run(['node','-e',script],capture_output=True,text=True,encoding='utf-8',timeout=15)
        self.assertEqual(output.returncode,0,output.stderr)
        return json.loads(output.stdout)

    def record(self, protocol='openai', model_id='model-a'):
        return {'name':solo.PROVIDERS[protocol]+'//'+model_id,'provider':solo.PROVIDERS[protocol],'custom_model_id':'server-record-42','base_url':BASE+'/chat/completions','config_source':3,'display_name':'已有名称'}

    def test_add_label_and_original_model_name_both_protocols(self):
        for protocol in ('openai','anthropic'):
            result=self.run_renderer(protocol=protocol)
            self.assertEqual(result['result']['added'],['model-a'])
            self.assertEqual(result['writes'][0]['data']['model_name'],'model-a')
            self.assertEqual(result['writes'][0]['data']['display_name'],'代码助手')

    def test_rename_existing_does_not_add_duplicate(self):
        result=self.run_renderer([self.record()])
        self.assertEqual(result['result']['updated'],['model-a'])
        self.assertEqual(len(result['records']),1)
        self.assertEqual(result['writes'][0]['data']['id'],'server-record-42')
        self.assertNotIn('model_name',result['writes'][0]['data'])

    def test_keep_policy_preserves_label(self):
        result=self.run_renderer([self.record()],overwrite=False)
        self.assertEqual(result['writes'],[])
        self.assertEqual(result['records'][0]['display_name'],'已有名称')

    def test_unspecified_name_preserved_when_updating(self):
        result=self.run_renderer([self.record()],name=None)
        self.assertEqual(result['writes'][0]['data']['display_name'],'已有名称')

    def test_display_name_is_not_model_identity(self):
        record=self.record(model_id='other-model')
        record['display_name']='model-a'
        result=self.run_renderer([record])
        self.assertEqual(result['result']['added'],['model-a'])
        self.assertEqual(len(result['records']),2)
        self.assertEqual(result['records'][0]['name'],'custom_openai_compatible//other-model')

    def test_rename_is_verified_not_just_record_presence(self):
        for records in ([],[self.record()]):
            result=self.run_renderer(records,persist=False)
            self.assertFalse(result['result']['added'])
            self.assertFalse(result['result']['updated'])
            self.assertEqual(result['result']['failed'][0]['stage'],'persistence_verify')

    def test_unicode_quotes_and_script_text_are_plain_label_data(self):
        label='中文 "别名" </script> ${notCode}'
        result=self.run_renderer(name=label)
        self.assertEqual(result['records'][0]['display_name'],label)

    def test_other_connection_same_id_never_renamed(self):
        record=self.record();record['base_url']='https://another.invalid/v1/chat/completions'
        result=self.run_renderer([record])
        self.assertEqual(result['writes'],[])
        self.assertEqual(result['result']['kept'],['model-a'])


if __name__=='__main__':unittest.main()
