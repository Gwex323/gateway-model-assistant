"""Regression coverage for the workbench Promise removed by solo-lite."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import trae_solo_adapter as adapter

ROOT = 'vscode-file://vscode-app/d:/app/resources/app/out/vs/workbench/'
SLIM = ROOT + 'workbench.desktop.main.solo-lite-slim.css'
NORMAL = ROOT + 'workbench.desktop.main.solo-lite.css'


@unittest.skipUnless(shutil.which('node'), 'Node is required for renderer tests')
class WorkbenchEntryTests(unittest.TestCase):
    def run_js(self, expression, styles=(), resources=(), setup=''):
        script = '''
        global.window={};
        global.document={readyState:'complete',querySelectorAll:()=>STYLES.map(href=>({href}))};
        global.performance={getEntriesByType:()=>RESOURCES.map(name=>({name}))};
        global.setTimeout=fn=>{fn();return 0;};
        '''.replace('STYLES', json.dumps(styles)).replace('RESOURCES', json.dumps(resources))
        script += setup + '\nPromise.resolve(' + expression + ').then(x=>console.log(JSON.stringify(x))).catch(e=>{console.error(e.message);process.exitCode=1;});'
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_deleted_promise_and_empty_resources_resolve_both_styles(self):
        for style in (SLIM, NORMAL, NORMAL.replace('vscode-file://vscode-app/', 'file:///')):
            with self.subTest(style=style):
                self.assertEqual(self.run_js(adapter._WORKBENCH_SOURCE_EXPRESSION, [style]), style[:-4]+'.js')
                self.assertTrue(self.run_js(adapter._RENDERER_READY_EXPRESSION, [style])['hasWorkbench'])

    def test_reject_remote_wrong_path_and_ambiguous_styles(self):
        for styles in ([SLIM.replace('vscode-file:', 'https:')], [ROOT+'fake.css'],
                       [SLIM.replace('/out/vs/workbench/', '/other/')], [SLIM, NORMAL]):
            with self.subTest(styles=styles):
                self.assertIsNone(self.run_js(adapter._WORKBENCH_SOURCE_EXPRESSION, styles))
                self.assertFalse(self.run_js(adapter._RENDERER_READY_EXPRESSION, styles)['hasWorkbench'])

    def test_resource_fallback_and_styles_priority(self):
        self.assertEqual(self.run_js(adapter._WORKBENCH_SOURCE_EXPRESSION, [], [NORMAL[:-4]+'.js']), NORMAL[:-4]+'.js')
        self.assertEqual(self.run_js(adapter._WORKBENCH_SOURCE_EXPRESSION, [SLIM, SLIM], [NORMAL[:-4]+'.js']), SLIM[:-4]+'.js')
        self.assertIsNone(self.run_js(adapter._WORKBENCH_SOURCE_EXPRESSION, [], [NORMAL[:-4]+'.js', SLIM[:-4]+'.js']))

    def test_full_importer_falls_back_after_missing_invalid_or_rejected_promise(self):
        # Only substitute the dynamic module loader; all adapter logic is real.
        expression = adapter._expression({'models': []}).replace('await import(src)', 'await global.loadModule(src)')
        for promise in ('undefined', 'Promise.resolve({})', "Promise.reject(new Error('removed'))"):
            setup = '''
            window.workbenchPromise=PROMISE;
            global.loadModule=async src=>{
              if(src!==EXPECTED) throw new Error('wrong module');
              return {createDecorator:x=>x,icubeStore:{serviceCollection:{get:()=>({
                getDefaultClient:async()=>({request:async request=>{
                  if(request.method!=='model_list') throw new Error('unexpected write');
                  return {code:0,data:{model_list:[]}};
                }})
              })}}};
            };
            '''.replace('PROMISE', promise).replace('EXPECTED', json.dumps(SLIM[:-4]+'.js'))
            with self.subTest(promise=promise):
                self.assertEqual(self.run_js(expression, [SLIM], setup=setup),
                                 {'added': [], 'updated': [], 'kept': [], 'failed': []})

    def test_invalid_module_exports_fail_before_service_calls(self):
        expression = adapter._expression({'models': []}).replace('await import(src)', 'await global.loadModule(src)')
        caught = '(' + expression + ").catch(e=>({stage:e.message}))"
        result = self.run_js(caught, [SLIM], setup='global.loadModule=async()=>({});')
        self.assertEqual(result, {'stage': 'workbench_module_missing'})
