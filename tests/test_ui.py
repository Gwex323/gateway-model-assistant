"""Hidden-window UI tests; all model configuration uses a temporary fixture."""
import json
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import core
import trae_adapter
import trae_solo_adapter


class UiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='gateway-ui-test-')
        self.root = tk.Tk()
        self.root.withdraw()
        self.ui = app.App(self.root)
        self.ui.path = Path(self.temp.name) / 'user/models.json'
        self.ui.path.parent.mkdir()
        self.ui.path.write_text('[]', encoding='utf-8')
        for index, target in enumerate(self.ui.trae_targets):
            if target.name == trae_solo_adapter.SOLO_NAME:
                solo_path = Path(self.temp.name) / 'solo/state.vscdb'
                solo_path.parent.mkdir()
                solo_path.write_bytes(b'test-only')
                self.ui.trae_targets[index] = trae_adapter.Target(
                    target.name, solo_path, target.process_hint,
                )
                self.ui.target_exists[target.name] = True
                self.ui.target_buttons[target.name].configure(state='normal')
        self.ui.url.set('https://gateway.example/v1')
        self.ui.key.set('fake-ui-key')
        self.ui.loaded_connection = ('openai', 'auto', 'https://gateway.example/v1', 'fake-ui-key')
        self.ui.ids = ['code-model', 'Seedance-2.0', 'second-code-model']
        self.ui.model_token_limits = {
            'code-model': {'maxInputTokens': 128000, 'maxOutputTokens': 8192},
            'second-code-model': {'maxInputTokens': 64000},
        }
        for var in self.ui.target_vars.values():
            var.set(False)
        self.ui.target_vars['WorkBuddy'].set(True)
        self.ui.refresh()

    def tearDown(self):
        self.ui.close()
        self.temp.cleanup()

    def test_column_drag_keeps_input_right_edge_and_output_fixed(self):
        self.root.deiconify(); self.root.update()
        tree = self.ui.tree
        columns = tuple(tree['displaycolumns'])
        for column in columns:
            tree.column(column, width=150)
        def widths():
            return [tree.column(c, 'width') for c in columns]
        before = widths()
        x = sum(before[:2]) + 1
        self.assertEqual(tree.identify_region(x, 10), 'separator')
        tree.event_generate('<ButtonPress-1>', x=x, y=10)
        tree.event_generate('<B1-Motion>', x=x+30, y=10)
        tree.event_generate('<ButtonRelease-1>', x=x+30, y=10)
        after = widths()
        self.assertEqual(after[1], before[1]+30)
        self.assertEqual(after[2], before[2]-30)
        self.assertEqual(sum(after[:4]), sum(before[:4]))
        self.assertEqual(after[4], before[4])
        self.assertFalse(self.ui.selected)

    def test_input_left_edge_moves_both_ways_when_name_column_is_at_minimum(self):
        self.root.deiconify(); self.root.update()
        tree = self.ui.tree
        columns = tuple(tree['displaycolumns'])
        tree.column('id', width=300)
        tree.column('display_name', width=tree.column('display_name', 'minwidth'))
        before = [tree.column(c, 'width') for c in columns]
        x = sum(before[:3]) + 1
        self.assertEqual(tree.identify_region(x, 10), 'separator')
        self.assertEqual(tree.identify_column(x), '#3')
        tree.event_generate('<ButtonPress-1>', x=x, y=10)
        for movement in (-60, -20, 0):
            tree.event_generate('<B1-Motion>', x=x+movement, y=10)
            now = [tree.column(c, 'width') for c in columns]
            self.assertEqual(sum(now[:3]), sum(before[:3])+movement)
            self.assertEqual(now[3], before[3]-movement)
            self.assertEqual(sum(now[:4]), sum(before[:4]))
            self.assertEqual(now[4], before[4])
        tree.event_generate('<ButtonRelease-1>', x=x, y=10)
        self.assertFalse(self.ui.selected)

    def test_input_and_output_right_separators_are_locked(self):
        for column in ('#4', '#5'):
            before = [self.ui.tree.column(c, 'width') for c in self.ui.tree['displaycolumns']]
            with patch.object(self.ui.tree, 'identify_region', return_value='separator'), \
                    patch.object(self.ui.tree, 'identify_column', return_value=column):
                self.assertEqual(self.ui._start_column_drag(SimpleNamespace(x=100,y=10)), 'break')
            self.ui._resize_column_drag(SimpleNamespace(x=200,y=10))
            self.assertEqual(self.ui._finish_column_drag(SimpleNamespace(x=200,y=10)), 'break')
            self.assertEqual(before, [self.ui.tree.column(c, 'width') for c in self.ui.tree['displaycolumns']])

    def test_column_drag_clamps_minimum_and_resize_cancels_drag(self):
        tree = self.ui.tree
        tree.column('display_name', width=200); tree.column('max_input', width=150)
        self.ui._column_drag = (100, 0, ('display_name', 'max_input'), (200, 150))
        for x in (10000, -10000):
            self.ui._resize_column_drag(SimpleNamespace(x=x))
            self.assertEqual(tree.column('display_name', 'width') + tree.column('max_input', 'width'), 350)
            for c in ('display_name', 'max_input'):
                self.assertGreaterEqual(tree.column(c, 'width'), tree.column(c, 'minwidth'))
        self.ui._fit_model_columns(1000)
        self.assertIsNone(self.ui._column_drag)

    def test_columns_fit_actual_font_and_long_content(self):
        from tkinter import font, ttk
        self.ui.ids = ['long-code-model-' * 12]
        model_id = self.ui.ids[0]
        self.ui.model_display_names[model_id] = '中文显示名称' * 20
        self.ui.model_limit_sources[model_id] = '来源说明' * 30
        self.ui.refresh()
        style = ttk.Style(self.root)
        body = font.Font(root=self.root, font=style.lookup('Treeview', 'font') or 'TkDefaultFont')
        heading = font.Font(root=self.root, font=style.lookup('Treeview.Heading', 'font') or 'TkHeadingFont')
        for column in self.ui.tree['displaycolumns']:
            value = self.ui.tree.set('0', column)
            width = self.ui.tree.column(column, 'width')
            self.assertGreaterEqual(width, body.measure(value) + 24)
            self.assertGreaterEqual(width, heading.measure(self.ui.tree.heading(column, 'text')) + 24)

    def test_source_column_hidden_and_excluded_from_autosizing(self):
        self.assertEqual(tuple(self.ui.tree['displaycolumns']),
                         ('check', 'id', 'display_name', 'max_input', 'max_output'))
        before = dict(self.ui._model_column_widths)
        self.ui.model_limit_sources['code-model'] = '很长的来源信息' * 200
        self.ui.refresh()
        self.assertNotIn('hint', self.ui._model_column_widths)
        self.assertEqual(self.ui._model_column_widths, before)
        self.ui._fit_model_columns(sum(before.values()) + 504)
        self.assertEqual(self.ui.tree.column('id', 'width'), before['id'] + 500)

    def test_hidden_source_remains_available_in_details(self):
        self.ui.model_limit_sources['code-model'] = '测试官方来源'
        self.ui.refresh()
        with patch.object(self.ui.tree, 'identify_row', return_value='0'), patch.object(app.messagebox, 'showinfo') as show:
            self.ui.show_model_spec(SimpleNamespace(y=1))
        self.assertIn('测试官方来源', show.call_args.args[1])

    def test_filter_recalculates_width_and_empty_list_keeps_headings(self):
        self.ui.model_display_names['code-model'] = '长中文名称' * 30
        self.ui.refresh()
        wide = self.ui.tree.column('display_name', 'width')
        self.ui.search.set('second')
        self.assertLess(self.ui.tree.column('display_name', 'width'), wide)
        self.ui.search.set('no-matching-model')
        self.assertFalse(self.ui.tree.get_children())
        self.assertGreater(self.ui.tree.column('display_name', 'width'), 24)
        self.ui.search.set('')
        self.assertEqual(self.ui.tree.column('display_name', 'width'), wide)

    def test_resize_never_squeezes_natural_column_widths(self):
        natural = dict(self.ui._model_column_widths)
        self.ui._fit_model_columns(100)
        for column, width in natural.items():
            self.assertEqual(self.ui.tree.column(column, 'width'), width)
        self.ui._fit_model_columns(sum(natural.values()) + 504)
        self.assertEqual(self.ui.tree.column('id', 'width'), natural['id'] + 500)

    def test_display_name_edit_expands_column(self):
        self.ui.target_vars[trae_solo_adapter.SOLO_NAME].set(True)
        before = self.ui.tree.column('display_name', 'width')
        with patch('modern_ui.simpledialog.askstring', return_value='显示名称' * 30):
            self.ui.edit_display_name('code-model')
        self.assertGreater(self.ui.tree.column('display_name', 'width'), before)

    def test_image_video_hint_does_not_mutate_numeric_limits(self):
        self.ui.model_token_limits['Seedance-2.0'] = {'maxInputTokens': 100, 'maxOutputTokens': 20}
        self.ui.model_specs['Seedance-2.0'] = {'max_input': '100', 'max_output': '20'}
        self.ui.refresh()
        self.assertEqual(self.ui.tree.item('1', 'values')[2:4], ('仅支持API导入', '仅支持API导入'))
        self.assertEqual(self.ui.model_token_limits['Seedance-2.0'], {'maxInputTokens': 100, 'maxOutputTokens': 20})
        self.assertEqual(self.ui.token_display_values('gpt-image-1'), ('仅支持API导入', '仅支持API导入'))

    def test_image_video_detection_excludes_visual_understanding_and_audio(self):
        for model_id in ('Seedream-4', 'Seedance-2', 'MiniMax-H3', 'gpt-image-1', 'gemini-2.5-flash-image'):
            with self.subTest(model_id=model_id):
                self.assertTrue(core.image_video_hint(model_id))
        for model_id in ('qwen-vl', 'deepseek-vision-exp', 'gemini-test', 'embedding-3', 'whisper-1', 'tts-1', 'rerank-3'):
            with self.subTest(model_id=model_id):
                self.assertFalse(core.image_video_hint(model_id))
                self.assertNotIn('仅支持API导入', self.ui.token_display_values(model_id))

    def test_selection_updates_cells_without_rebuilding_or_resizing_table(self):
        with patch.object(self.ui, 'refresh') as refresh, patch.object(self.ui, '_autosize_model_columns') as resize:
            self.ui.tree.focus('0')
            self.ui.toggle_row('0')
            self.assertEqual(self.ui.tree.set('0', 'check'), '✓')
            self.assertEqual(self.ui.tree.focus(), '0')
            self.ui.select_visible(True)
            self.assertEqual(self.ui.selected, {'code-model', 'Seedance-2.0', 'second-code-model'})
            self.ui.select_visible(False)
            self.assertEqual(self.ui.tree.set('0', 'check'), '')
            refresh.assert_not_called()
            resize.assert_not_called()

    def test_selection_keeps_large_list_scroll_position(self):
        self.root.attributes('-alpha', 0.0)
        self.root.deiconify()
        self.ui.ids = [f'model-{i}' for i in range(300)]
        self.ui.refresh()
        self.root.update()
        self.ui.tree.yview_moveto(.5)
        self.root.update_idletasks()
        before = self.ui.tree.yview()
        self.ui.toggle_row('160')
        self.root.update_idletasks()
        self.assertEqual(self.ui.tree.yview(), before)

    def test_filter_selection_preserved(self):
        self.ui.select_visible(True)
        self.assertEqual(self.ui.selected, {'code-model', 'Seedance-2.0', 'second-code-model'})
        self.ui.search.set('second')
        self.assertEqual(len(self.ui.tree.get_children()), 1)
        self.ui.select_visible(False)
        self.assertEqual(self.ui.selected, {'code-model', 'Seedance-2.0'})

    def test_connection_change_requires_refetch(self):
        self.ui.url.set('https://other.example/v1')
        with self.assertRaises(core.UserError): self.ui.connection()

    def test_default_protocol_is_openai(self):
        self.assertEqual(self.ui.protocol.get(), 'OpenAI')
        self.assertEqual(self.ui.current_protocol(), 'openai')

    def test_deepseek_url_automatically_selects_anthropic(self):
        self.ui.url.set('https://api.deepseek.com/anthropic')
        with patch('modern_ui.core.fetch_anthropic_model_catalog', return_value=[]) as fetch, \
                patch.object(self.ui, 'run') as run:
            self.ui.fetch()
            run.call_args.args[1](run.call_args.args[0]())
        self.assertEqual(self.ui.current_protocol(), 'anthropic')
        self.assertEqual(self.ui.selected_import_targets(), [trae_solo_adapter.SOLO_NAME])
        self.assertEqual(fetch.call_args.args[0], 'https://api.deepseek.com/anthropic/v1')
        self.assertEqual(self.ui.connection()[0], 'https://api.deepseek.com/anthropic/v1')

    def test_deepseek_openai_root_does_not_switch_protocol(self):
        self.ui.url.set('https://api.deepseek.com')
        with patch.object(self.ui, 'run'):
            self.ui.fetch()
        self.assertEqual(self.ui.current_protocol(), 'openai')

    def test_official_limits_visible_and_forwarded_to_import(self):
        self.ui.url.set('https://api.deepseek.com/anthropic')
        model = core.GatewayModel('deepseek-test-a', 606784, 393216,
                                  '官方规格·保守输入预算')
        with patch('modern_ui.core.fetch_anthropic_model_catalog', return_value=[model]), \
                patch.object(self.ui, 'run') as run:
            self.ui.fetch()
            run.call_args.args[1](run.call_args.args[0]())
        values = self.ui.tree.item('0', 'values')
        self.assertEqual(values[2:4], ('606784', '393216'))
        self.assertEqual(values[4], '官方规格·保守输入预算')
        self.ui.selected.add(model.id)
        with patch('modern_ui.trae_solo_adapter.make_plan', wraps=trae_solo_adapter.make_plan) as make:
            self.ui.preview()
        self.assertEqual(make.call_args.kwargs['token_limits_by_model'][model.id], model.token_limits)
        for widget in self.root.winfo_children():
            if isinstance(widget, tk.Toplevel):
                widget.destroy()

    def test_sgt_official_specs_flow_through_ui_to_native_import_plan(self):
        import provider_specs
        self.ui.protocol.set('Anthropic')
        self.ui.protocol_changed()
        self.ui.url.set('https://platform.sgt.site/v1')
        card = '<link rel="canonical" href="https://developers.openai.com/api/docs/models/gpt-test"><p>200,000 context window 32,000 max output tokens</p>'
        with patch.object(core, '_request_json', return_value={'data': [{'id': 'gpt-test'}]}), \
                patch.object(provider_specs, 'read_document', return_value=card), \
                patch.object(self.ui, 'run') as run:
            self.ui.fetch()
            run.call_args.args[1](run.call_args.args[0]())
        values = self.ui.tree.item('0', 'values')
        self.assertEqual(values[2:4], ('≤200000', '32000'))
        self.assertIn('官方规格', values[4])
        self.ui.selected.add('gpt-test')
        with patch('modern_ui.trae_solo_adapter.make_plan', wraps=trae_solo_adapter.make_plan) as make:
            self.ui.preview()
        self.assertEqual(make.call_args.kwargs['token_limits_by_model']['gpt-test'],
                         {'maxInputTokens': 168000, 'maxOutputTokens': 32000})
        for widget in self.root.winfo_children():
            if isinstance(widget, tk.Toplevel):
                widget.destroy()

    def test_sgt_shows_media_and_dynamic_caps_separately_from_import_budget(self):
        self.ui.url.set('https://platform.sgt.site/v1')
        catalog = [core.GatewayModel('kimi-k3', 917504, 131072, '官方规格',
                                    'https://platform.kimi.com/docs/guide/troubleshooting.md',
                                    '≤1048576', '1048576－本次输入', '实际输出扣除输入'),
                   core.GatewayModel('MiniMax-H3', limit_source='媒体模型',
                                     input_display='不适用', output_display='不适用')]
        with patch.object(core, 'fetch_model_catalog', return_value=catalog), patch.object(self.ui, 'run') as run:
            self.ui.fetch()
            run.call_args.args[1](run.call_args.args[0]())
        self.assertFalse(hasattr(self.ui, 'hide_media'))
        self.assertEqual(len(self.ui.tree.get_children()), 2)
        self.assertEqual(self.ui.tree.item('0', 'values')[2:4], ('≤1048576', '1048576－本次输入'))
        self.assertEqual(self.ui.tree.item('1', 'values')[2:4], ('仅支持API导入', '仅支持API导入'))
        self.assertIn('1/1', self.ui.status.get())
        self.assertEqual(self.ui.model_specs['kimi-k3']['max_output'], '1048576－本次输入')
        self.assertEqual(self.ui.model_token_limits['kimi-k3']['maxOutputTokens'], 131072)

    def test_model_spec_detail_shows_caps_budget_and_sources_without_key(self):
        self.ui.model_specs['code-model'] = {'max_input': '200000', 'max_output': '32000',
                                           'source_url': 'https://example.invalid/spec', 'note': '共享窗口'}
        with patch.object(self.ui.tree, 'identify_row', return_value='0'), patch.object(app.messagebox, 'showinfo') as show:
            self.ui.show_model_spec(SimpleNamespace(y=1))
        text = show.call_args.args[1]
        self.assertIn('200000', text)
        self.assertIn('128000', text)
        self.assertIn('8192', text)
        self.assertIn('共享窗口', text)
        self.assertNotIn('fake-ui-key', text)

    def test_display_name_editor_is_trae_only(self):
        with patch('modern_ui.simpledialog.askstring') as ask, patch.object(self.ui, 'report') as report:
            self.ui.edit_display_name('code-model')
        ask.assert_not_called()
        self.assertIn('WorkBuddy', str(report.call_args.args[0]))
        self.assertEqual(self.ui.model_display_names, {})

    def test_edit_display_name_and_clear_without_changing_model_id(self):
        self.ui.target_vars[trae_solo_adapter.SOLO_NAME].set(True)
        original=list(self.ui.ids)
        with patch('modern_ui.simpledialog.askstring', return_value=' 中文助手 '):
            self.ui.edit_display_name('code-model')
        self.assertEqual(self.ui.model_display_names, {'code-model':'中文助手'})
        self.assertEqual(self.ui.ids, original)
        self.assertEqual(self.ui.tree.item('0', 'values')[5], '中文助手')
        with patch('modern_ui.simpledialog.askstring', return_value=None):
            self.ui.edit_display_name('code-model')
        self.assertEqual(self.ui.model_display_names['code-model'], '中文助手')
        with patch('modern_ui.simpledialog.askstring', return_value=''):
            self.ui.edit_display_name('code-model')
        self.assertEqual(self.ui.model_display_names, {})

    def test_mixed_workbuddy_and_trae_plan_only_applies_alias_to_trae(self):
        self.ui.target_vars[trae_solo_adapter.SOLO_NAME].set(True)
        self.ui.model_display_names={'code-model':'Trae 专用名称', 'not-selected':'忽略'}
        plans=self.ui.build_import_plans('https://gateway.example/v1', 'fake-ui-key', ['code-model'], {})
        workbuddy=next(item[3] for item in plans if item[1]=='workbuddy')
        solo=next(item[3] for item in plans if item[1]=='trae_solo')
        self.assertEqual(core.model_array(workbuddy.document)[0]['name'], 'code-model')
        self.assertEqual(core.model_array(workbuddy.document)[0]['id'], 'code-model')
        self.assertEqual(solo.display_names, {'code-model':'Trae 专用名称'})

    def test_native_anthropic_plan_receives_display_name(self):
        self.ui.protocol.set('Anthropic')
        self.ui.protocol_changed()
        self.ui.model_display_names={'code-model':'原生助手'}
        plan=self.ui.build_import_plans('https://gateway.example/v1', 'fake-ui-key', ['code-model'], {})[0][3]
        self.assertEqual(plan.protocol, 'anthropic')
        self.assertEqual(plan.display_names, {'code-model':'原生助手'})

    def test_normal_trae_plan_receives_display_name(self):
        target=next(item for item in self.ui.trae_targets if item.name != trae_solo_adapter.SOLO_NAME)
        self.ui.model_display_names={'code-model':'普通 Trae 助手'}
        with patch.object(self.ui, 'selected_import_targets', return_value=[target.name]), patch('modern_ui.trae_adapter.make_plan') as make:
            self.ui.build_import_plans('https://gateway.example/v1', 'fake-ui-key', ['code-model'], {})
        self.assertEqual(make.call_args.kwargs['display_names_by_model'], {'code-model':'普通 Trae 助手'})

    def test_refetch_same_connection_keeps_names_new_connection_clears(self):
        self.ui.model_display_names={'code-model':'已有编辑', 'removed-model':'清除'}
        with patch.object(core, 'fetch_model_catalog', return_value=[core.GatewayModel('code-model')]), patch.object(self.ui, 'run') as run:
            self.ui.fetch()
            run.call_args.args[1](run.call_args.args[0]())
            self.assertEqual(self.ui.model_display_names, {'code-model':'已有编辑'})
            self.ui.url.set('https://different.example/v1')
            self.ui.fetch()
            run.call_args.args[1](run.call_args.args[0]())
        self.assertEqual(self.ui.model_display_names, {})

    def test_display_name_cell_click_does_not_toggle_selection(self):
        with patch.object(self.ui.tree, 'identify_column', return_value='#3'), patch.object(self.ui.tree, 'identify_row', return_value='0'):
            self.ui.toggle(SimpleNamespace(x=1,y=1))
        self.assertEqual(self.ui.selected,set())

    def test_auth_change_invalidates_loaded_models(self):
        self.ui.protocol.set('Anthropic')
        self.ui.auth.set('Bearer')
        with self.assertRaises(core.UserError):
            self.ui.connection()
        self.ui.auth_changed()
        self.assertEqual(self.ui.ids, [])
        self.assertIsNone(self.ui.loaded_connection)

    def test_explicit_bearer_fetch_and_connection_identity(self):
        self.ui.protocol.set('Anthropic')
        self.ui.protocol_changed()
        self.ui.auth.set('Bearer')
        with patch('modern_ui.core.fetch_anthropic_model_catalog', return_value=[]) as fetch, \
                patch.object(self.ui, 'run') as run:
            self.ui.fetch()
            run.call_args.args[1](run.call_args.args[0]())
        self.assertEqual(fetch.call_args.kwargs['auth'], 'bearer')
        self.assertEqual(self.ui.loaded_connection,
                         ('anthropic', 'bearer', 'https://gateway.example/v1', 'fake-ui-key'))

    def test_fetch_completion_rejects_connection_changed_during_request(self):
        with patch.object(self.ui, 'run') as run:
            self.ui.fetch()
        self.ui.key.set('another-fake-key')
        run.call_args.args[1]([core.GatewayModel('stale')])
        self.assertIsNone(self.ui.loaded_connection)
        self.assertNotIn('stale', self.ui.ids)

    def test_volc_anthropic_url_resolves_before_fetch_and_import(self):
        self.ui.protocol.set('Anthropic')
        self.ui.protocol_changed()
        self.ui.url.set('https://ark.cn-beijing.volces.com/api/coding')
        url = self.ui._connection_url(prompt_private=False)
        plans = self.ui.build_import_plans(url, 'fake-ui-key', ['test-model'], {})
        plan = plans[0][3]
        self.assertEqual(plan.endpoint, 'https://ark.cn-beijing.volces.com/api/coding/v1/messages')
        self.assertEqual(plan.auth, 'bearer')
        self.assertTrue(plan.compatibility_warning)

    def test_anthropic_cannot_silently_import_into_workbuddy(self):
        self.ui.protocol.set('Anthropic')
        with patch('modern_ui.core.make_plan') as make:
            with self.assertRaises(core.UserError):
                self.ui.build_import_plans('https://gateway.example/v1', 'fake-ui-key', ['test-model'], {})
        make.assert_not_called()

    def test_anthropic_protocol_only_enables_traework_and_requires_refetch(self):
        self.ui.ids = ['stale-model']
        self.ui.protocol.set('Anthropic')
        self.ui.protocol_changed()
        for name, button in self.ui.target_buttons.items():
            if name == trae_solo_adapter.SOLO_NAME:
                self.assertEqual(str(button.cget('state')), 'normal')
                self.assertTrue(self.ui.target_vars[name].get())
            else:
                self.assertEqual(str(button.cget('state')), 'disabled')
                self.assertFalse(self.ui.target_vars[name].get())
        self.assertEqual(self.ui.ids, [])
        self.assertIsNone(self.ui.loaded_connection)
        self.ui.protocol.set('OpenAI')
        self.ui.protocol_changed()
        self.assertTrue(self.ui.target_vars['WorkBuddy'].get())

    def test_anthropic_fetch_uses_native_catalog(self):
        self.ui.protocol.set('Anthropic')
        self.ui.protocol_changed()
        catalog = [core.GatewayModel('claude-test', 200000, 8192)]
        with patch('modern_ui.core.fetch_anthropic_model_catalog', return_value=catalog) as fetch, \
                patch.object(self.ui, 'run') as run:
            self.ui.fetch()
            result = run.call_args.args[0]()
        self.assertEqual(result, catalog)
        fetch.assert_called_once_with(
            'https://gateway.example/v1', 'fake-ui-key', allow_private_http=False,
        )

    def test_preview_does_not_write(self):
        self.ui.selected.add('code-model')
        self.ui.preview()
        dialogs = [w for w in self.root.winfo_children() if isinstance(w, tk.Toplevel)]
        self.assertEqual(len(dialogs), 1)
        self.assertEqual(self.ui.path.read_text(), '[]')
        self.assertFalse((self.ui.path.parent / 'gateway-model-assistant-backups').exists())
        dialogs[0].destroy()

    def test_preview_scrollbar_hidden_with_wheel_keyboard_and_no_writes(self):
        from tkinter import ttk
        self.root.attributes('-alpha', 0.0)
        self.root.deiconify()
        self.ui.ids = ['preview-model-' + str(i) for i in range(100)]
        self.ui.selected = set(self.ui.ids)
        self.ui.preview()
        dialog = next(w for w in self.root.winfo_children() if isinstance(w, tk.Toplevel))
        dialog.attributes('-alpha', 0.0)
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        try:
            self.root.update()
            widgets = list(descendants(dialog))
            table = next(w for w in widgets if isinstance(w, ttk.Treeview))
            bars = [w for w in widgets if isinstance(w, ttk.Scrollbar)]
            self.assertTrue(bars)
            self.assertTrue(all(not w.winfo_ismapped() and not w.winfo_manager() for w in bars))
            self.assertEqual(len(table.get_children()), 100)
            table.event_generate('<MouseWheel>', delta=-120)
            self.root.update()
            self.assertGreater(table.yview()[0], 0)
            table.yview_moveto(0)
            table.focus_force()
            table.focus(table.get_children()[0])
            table.selection_set(table.get_children()[0])
            self.root.update()
            table.event_generate('<Next>')
            self.root.update()
            self.assertGreater(table.yview()[0], 0)
            self.assertTrue(all(not w.winfo_ismapped() for w in bars))
            self.assertEqual(self.ui.path.read_text(), '[]')
            self.assertFalse((self.ui.path.parent / 'gateway-model-assistant-backups').exists())
        finally:
            dialog.destroy()

    def test_restored_theme_and_user_brand_icon(self):
        self.assertEqual(self.ui.COLORS['bg'], '#F7F9FA')
        self.assertEqual(self.ui.COLORS['accent_soft'], '#EAF3EC')
        self.assertEqual(self.root._desktop_icon.width(), 256)
        self.assertTrue(self.root._desktop_icon.transparency_get(0, 0))
        def luminance(value):
            channels = [int(value[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in channels]
            return sum(v * w for v, w in zip(linear, (.2126, .7152, .0722)))
        for foreground, background in [('text', 'bg'), ('accent', 'accent_soft')]:
            values = sorted([luminance(self.ui.COLORS[foreground]), luminance(self.ui.COLORS[background])])
            self.assertGreaterEqual((values[1] + .05) / (values[0] + .05), 4.5)
        self.assertGreaterEqual(1.05 / (luminance(self.ui.COLORS['accent']) + .05), 4.5)

    def test_no_header_or_reserved_space_and_footer_still_updates(self):
        self.root.attributes('-alpha', 0.0)
        self.root.deiconify()
        self.root.update()
        self.assertFalse(hasattr(self.ui, 'header'))
        self.assertFalse(hasattr(self.ui, 'brand_logo'))
        self.assertFalse(hasattr(self.ui, 'status_badge'))
        self.assertEqual(self.ui.workspace.winfo_y(), 0)
        self.assertEqual(int(self.ui.workspace.grid_info()['row']), 0)
        self.assertEqual(int(self.ui.actions.grid_info()['row']), 3)
        self.assertTrue(self.ui.footer_status.winfo_ismapped())
        self.ui.status.set('正在读取模型列表…')
        self.root.update()
        self.assertEqual(str(self.ui.footer_status.cget('textvariable')), str(self.ui.status))

    def test_darker_green_window_icon_preserves_transparency(self):
        from desktop_runtime import asset_path
        image = self.root._desktop_icon
        baseline = tk.PhotoImage(master=self.root, file=str(asset_path('brand-green.png')))
        red, green, blue = image.get(128, 128)
        self.assertGreater(green, red)
        self.assertGreater(green, blue)
        self.assertTrue(image.transparency_get(0, 0))
        self.assertLess(sum((red, green, blue)), sum(baseline.get(512, 512)) * .8)

    def test_import_target_explanatory_note_is_removed(self):
        def walk(widget):
            for child in widget.winfo_children():
                yield child
                yield from walk(child)
        labels = [str(w.cget('text')) for w in walk(self.ui.sidebar) if 'text' in w.keys()]
        self.assertFalse(any('可多选。Anthropic' in text for text in labels))
        self.assertEqual(len(self.ui.target_buttons), 3)

    def test_image_metadata_flows_from_catalog_to_mixed_import_targets(self):
        catalog = [core.GatewayModel('opaque-vision', supports_images=True),
                   core.GatewayModel('opaque-text', supports_images=False)]
        with patch.object(self.ui, 'run') as run, patch('modern_ui.core.fetch_model_catalog', return_value=catalog):
            self.ui.fetch()
            run.call_args.args[1](run.call_args.args[0]())
        self.assertEqual(self.ui.flags['supportsImages'].get(), '自动识别')
        self.ui.target_vars[trae_solo_adapter.SOLO_NAME].set(True)
        plans = self.ui.build_import_plans('https://gateway.example/v1','fake-ui-key',[m.id for m in catalog],{})
        wb = next(p[3] for p in plans if p[1] == 'workbuddy')
        solo = next(p[3] for p in plans if p[1] == 'trae_solo')
        self.assertEqual([m['supportsImages'] for m in core.model_array(wb.document)], [True, False])
        self.assertEqual(core.model_array(wb.document)[0]['url'], 'https://gateway.example/v1/chat/completions')
        self.assertEqual(solo.endpoint, 'https://gateway.example/v1')
        self.assertTrue(solo.model_capabilities['opaque-vision']['supportsImages'])
        plans = self.ui.build_import_plans('https://gateway.example/v1','fake-ui-key',[m.id for m in catalog],{'supportsImages':False})
        self.assertFalse(core.model_array(plans[0][3].document)[0]['supportsImages'])

    def test_workbuddy_tool_calls_default_on_without_changing_trae(self):
        self.assertEqual(self.ui.flags['supportsToolCall'].get(), '自动（WB 开启）')
        self.ui.target_vars[trae_solo_adapter.SOLO_NAME].set(True)
        plans = self.ui.build_import_plans(
            'https://gateway.example/v1', 'fake-ui-key', ['code-model'], {},
        )
        buddy = next(plan for _, kind, _, plan in plans if kind == 'workbuddy')
        solo = next(plan for _, kind, _, plan in plans if kind == 'trae_solo')
        self.assertTrue(core.model_array(buddy.document)[0]['supportsToolCall'])
        self.assertNotIn('supportsToolCall', solo.model_capabilities.get('code-model', {}))

        plans = self.ui.build_import_plans(
            'https://gateway.example/v1', 'fake-ui-key', ['code-model'],
            {'supportsToolCall': False},
        )
        buddy = next(plan for _, kind, _, plan in plans if kind == 'workbuddy')
        self.assertFalse(core.model_array(buddy.document)[0]['supportsToolCall'])

    def test_workbuddy_tool_calls_preserve_and_overwrite_existing(self):
        model = {
            'id': 'code-model', 'vendor': 'Custom',
            'url': 'https://gateway.example/v1/chat/completions',
            'apiKey': 'fake-ui-key', 'useCustomProtocol': False,
            'supportsToolCall': False,
        }
        self.ui.path.write_text(json.dumps([model]), encoding='utf-8')
        url, key = 'https://gateway.example/v1', 'fake-ui-key'
        kept = self.ui.build_import_plans(url, key, ['code-model'], {})[0][3]
        self.assertEqual(kept.rows, [('code-model', '保留已有')])
        self.assertFalse(core.model_array(kept.document)[0]['supportsToolCall'])
        self.ui.existing_policy.set('覆盖已有')
        replaced = self.ui.build_import_plans(url, key, ['code-model'], {})[0][3]
        self.assertTrue(core.model_array(replaced.document)[0]['supportsToolCall'])

    def test_model_panel_contains_status_left_and_import_right_without_root_footer(self):
        self.root.attributes('-alpha', 0.0)
        self.root.deiconify()
        for geometry in ('940x700', '1280x860'):
            self.root.geometry(geometry)
            self.root.update()
            for message in ('等待连接网关', '正在读取模型列表与规格，稍后可预览并导入。' * 8):
                self.ui.status.set(message)
                self.root.update()
                self.assertEqual(self.ui.actions.master, self.ui.models_panel)
                self.assertEqual(self.ui.import_button.master, self.ui.actions)
                self.assertEqual(self.ui.footer_status.master, self.ui.actions)
                self.assertEqual(self.ui.table_hint.master, self.ui.actions)
                for column, widget in enumerate((self.ui.footer_status, self.ui.table_hint, self.ui.import_button)):
                    self.assertEqual(int(widget.grid_info()['row']), 0)
                    self.assertEqual(int(widget.grid_info()['column']), column)
                self.assertLessEqual(self.ui.footer_status.winfo_rootx() + self.ui.footer_status.winfo_width(), self.ui.table_hint.winfo_rootx())
                self.assertLessEqual(self.ui.table_hint.winfo_rootx() + self.ui.table_hint.winfo_width(), self.ui.import_button.winfo_rootx())
                self.assertEqual(str(self.ui.footer_status.cget('anchor')), 'w')
                self.assertLessEqual(self.ui.footer_status.winfo_rootx() + self.ui.footer_status.winfo_width(),
                                     self.ui.import_button.winfo_rootx())
                self.assertLessEqual(self.ui.import_button.winfo_rootx() + self.ui.import_button.winfo_width(),
                                     self.ui.models_panel.winfo_rootx() + self.ui.models_panel.winfo_width())
                self.assertLessEqual(self.ui.actions.winfo_rooty() + self.ui.actions.winfo_height(),
                                     self.ui.models_panel.winfo_rooty() + self.ui.models_panel.winfo_height())
                self.assertEqual(self.root.grid_slaves(row=1), [])
                self.assertGreater(self.ui.tree.winfo_height(), 100)

    def test_search_right_select_all_replaces_old_toolbar_and_shows_media(self):
        self.root.attributes('-alpha', 0.0)
        self.root.geometry('940x700')
        self.root.deiconify()
        self.root.update()
        button = self.ui.select_all_button
        self.assertEqual(button.master, self.ui.search_entry.master)
        self.assertEqual(button.cget('text'), '全选')
        self.assertGreaterEqual(button.winfo_rootx(), self.ui.search_entry.winfo_rootx() + self.ui.search_entry.winfo_width())
        self.assertFalse(hasattr(self.ui, 'hide_media'))
        self.assertEqual(len(self.ui.tree.get_children()), 3)
        button.invoke()
        self.assertEqual(self.ui.selected, set(self.ui.ids))
        self.ui.selected.clear()
        self.ui.search.set('second')
        button.invoke()
        self.assertEqual(self.ui.selected, {'second-code-model'})
        self.ui.search.set('no-matches')
        button.invoke()
        self.assertEqual(self.ui.selected, {'second-code-model'})
        def walk(widget):
            for child in widget.winfo_children():
                yield child
                yield from walk(child)
        labels = [str(w.cget('text')) for w in walk(self.ui.models_panel) if 'text' in w.keys()]
        for old in ('隐藏媒体 / 嵌入', '选中可见', '取消可见'):
            self.assertNotIn(old, labels)

    def test_preview_requires_an_import_target(self):
        self.ui.selected.add('code-model')
        for var in self.ui.target_vars.values():
            var.set(False)
        with patch.object(app.messagebox, 'showerror') as showerror:
            self.ui.preview()
        self.assertTrue(showerror.called)
        dialogs = [w for w in self.root.winfo_children() if isinstance(w, tk.Toplevel)]
        self.assertEqual(dialogs, [])

    def test_trae_only_plan_does_not_build_workbuddy_plan(self):
        target = self.ui.trae_targets[0]
        self.ui.target_vars['WorkBuddy'].set(False)
        self.ui.target_vars[target.name].set(True)
        trae_plan = SimpleNamespace(rows=[('code-model', '新增')], changed=True, summary=lambda: '新增 1')
        with patch.object(app.App, 'selected_import_targets', return_value=[target.name]), \
                patch('modern_ui.trae_adapter.make_plan', return_value=trae_plan) as make_trae, \
                patch('modern_ui.core.make_plan') as make_workbuddy:
            plans = self.ui.build_import_plans('https://gateway.example/v1', 'fake-ui-key', ['code-model'], {})
        self.assertEqual([item[0] for item in plans], [target.name])
        make_trae.assert_called_once()
        make_workbuddy.assert_not_called()

    def test_reuse_feature_is_not_present(self):
        texts = []
        def walk(widget):
            for child in widget.winfo_children():
                try:
                    texts.append(child.cget('text'))
                except tk.TclError:
                    pass
                walk(child)
        walk(self.root)
        self.assertNotIn('从应用复用', texts)

    def test_existing_model_policy_defaults_to_keep_and_can_overwrite(self):
        self.assertEqual(self.ui.existing_policy.get(), '保留已有')
        self.ui.existing_policy.set('覆盖已有')
        with patch('modern_ui.core.make_plan', return_value=SimpleNamespace(rows=[], changed=False)) as make:
            self.ui.build_import_plans(
                'https://gateway.example/v1', 'fake-ui-key', ['code-model'], {},
                {'code-model': {'maxInputTokens': 128000, 'maxOutputTokens': 8192}},
            )
        self.assertTrue(make.call_args.kwargs['update_existing'])
        self.assertEqual(
            make.call_args.kwargs['token_limits_by_model'],
            {'code-model': {'maxInputTokens': 128000, 'maxOutputTokens': 8192}},
        )

    def test_token_limits_are_automatic_and_visible_per_model(self):
        self.assertFalse(hasattr(self.ui, 'max_input_tokens'))
        self.assertFalse(hasattr(self.ui, 'max_output_tokens'))
        first = self.ui.tree.item('0', 'values')
        self.assertEqual(first[2:4], ('128000', '8192'))
        third = self.ui.tree.item('2', 'values')
        self.assertEqual(third[2:4], ('64000', '未识别'))

    def test_private_http_requires_visible_confirmation(self):
        self.ui.url.set('http://172.16.116.14/v1/chat/completions')
        with patch.object(app.messagebox, 'askyesno', return_value=True) as confirm:
            url = self.ui._connection_url(prompt_private=True)
        self.assertEqual(url, 'http://172.16.116.14/v1')
        self.assertEqual(self.ui.private_http_consent, url)
        confirm.assert_called_once()
        self.assertEqual(self.ui._connection_url(prompt_private=False), url)

    def test_private_http_decline_keeps_connection_unapproved(self):
        self.ui.url.set('http://172.16.116.14/v1')
        with patch.object(app.messagebox, 'askyesno', return_value=False):
            with self.assertRaises(core.UserError):
                self.ui._connection_url(prompt_private=True)
        self.assertIsNone(self.ui.private_http_consent)

    def test_solo_target_builds_persistent_client_plan(self):
        target = next(item for item in self.ui.trae_targets if item.name == trae_solo_adapter.SOLO_NAME)
        for var in self.ui.target_vars.values():
            var.set(False)
        self.ui.target_vars[target.name].set(True)
        solo_plan = SimpleNamespace(rows=[('code-model', '导入时复核')], changed=True,
                                    summary=lambda: '由客户端持久化导入 1 个')
        with patch('modern_ui.trae_solo_adapter.make_plan', return_value=solo_plan) as make_solo, \
                patch('modern_ui.trae_adapter.make_plan') as make_cache:
            plans = self.ui.build_import_plans('https://gateway.example/v1', 'fake-ui-key', ['code-model'], {})
        self.assertEqual([(item[0], item[1]) for item in plans], [(target.name, 'trae_solo')])
        make_solo.assert_called_once()
        self.assertEqual(make_solo.call_args.kwargs['protocol'], 'openai')
        make_cache.assert_not_called()

    def test_anthropic_plan_only_builds_traework_native_import(self):
        self.ui.protocol.set('Anthropic')
        self.ui.protocol_changed()
        solo_plan = SimpleNamespace(rows=[('claude-test', '导入时复核')], changed=True,
                                    summary=lambda: 'Anthropic 原生导入 1 个')
        with patch('modern_ui.trae_solo_adapter.make_plan', return_value=solo_plan) as make_solo, \
                patch('modern_ui.trae_adapter.make_plan') as make_cache, \
                patch('modern_ui.core.make_plan') as make_workbuddy:
            plans = self.ui.build_import_plans(
                'https://gateway.example/v1', 'fake-ui-key', ['claude-test'], {},
                {'claude-test': {'maxInputTokens': 200000, 'maxOutputTokens': 8192}},
            )
        self.assertEqual([(item[0], item[1]) for item in plans],
                         [(trae_solo_adapter.SOLO_NAME, 'trae_solo')])
        self.assertEqual(make_solo.call_args.kwargs['protocol'], 'anthropic')
        make_cache.assert_not_called()
        make_workbuddy.assert_not_called()

    def test_footer_only_offers_preview_import(self):
        from tkinter import ttk
        def walk(widget):
            for child in widget.winfo_children():
                yield child
                yield from walk(child)
        buttons = [w for w in walk(self.ui.actions) if isinstance(w, ttk.Button)]
        self.assertEqual(buttons, [self.ui.import_button])
        self.assertFalse(hasattr(self.ui, 'restore'))
        self.assertFalse(hasattr(self.ui, 'export'))
        self.assertTrue(callable(core.apply_plan))

    def test_layout_fits_minimum_window(self):
        self.root.attributes('-alpha', 0.0)
        self.root.geometry('920x740')
        self.root.deiconify()
        self.root.update()
        width, height = self.root.winfo_width(), self.root.winfo_height()
        def walk(widget):
            for child in widget.winfo_children():
                yield child
                yield from walk(child)
        for w in walk(self.root):
            if not w.winfo_ismapped(): continue
            x = w.winfo_rootx() - self.root.winfo_rootx()
            y = w.winfo_rooty() - self.root.winfo_rooty()
            self.assertLessEqual(x + w.winfo_width(), width + 1, str(w))
            # Sidebar children live in a clipped scrolling viewport, like table rows.
            if str(w).startswith(str(self.ui.sidebar)):
                continue
            self.assertLessEqual(y + w.winfo_height(), height + 1, str(w))

    def test_layout_fits_default_window(self):
        self.root.attributes('-alpha', 0.0)
        self.root.deiconify()
        self.root.update()
        # Check the fixed parts of the UI, excluding content inside list viewports.
        width, height = self.root.winfo_width(), self.root.winfo_height()
        def walk(widget):
            for child in widget.winfo_children():
                yield child
                yield from walk(child)
        for w in walk(self.root):
            if not w.winfo_ismapped(): continue
            x = w.winfo_rootx() - self.root.winfo_rootx()
            y = w.winfo_rooty() - self.root.winfo_rooty()
            self.assertLessEqual(x + w.winfo_width(), width + 1, str(w))
            if str(w).startswith(str(self.ui.sidebar)):
                continue
            self.assertLessEqual(y + w.winfo_height(), height + 1, str(w))
        self.assertGreater(self.ui.tree.winfo_height(), 130)

    def test_html_layout_has_sidebar_table_and_fixed_actions(self):
        self.root.attributes('-alpha', 0.0)
        self.root.deiconify()
        self.root.update()
        self.assertLess(self.ui.sidebar_shell.winfo_rootx(), self.ui.models_panel.winfo_rootx())
        self.assertGreater(self.ui.tree.winfo_height(), 400)
        self.assertGreater(self.ui.actions.winfo_rooty(), self.ui.models_panel.winfo_rooty())
        self.assertEqual(self.ui.actions.master, self.ui.models_panel)
        self.assertEqual(self.ui.COLORS['accent'], '#24633A')

    def test_sidebar_scroll_reaches_policy_without_moving_footer(self):
        self.root.attributes('-alpha', 0.0)
        self.root.geometry('940x700')
        self.root.deiconify()
        self.root.update()
        footer_y = self.ui.actions.winfo_rooty()
        self.ui.sidebar_canvas.yview_moveto(1)
        self.root.update()
        policy_bottom = self.ui.policy_combo.winfo_rooty() + self.ui.policy_combo.winfo_height()
        viewport_bottom = self.ui.sidebar_canvas.winfo_rooty() + self.ui.sidebar_canvas.winfo_height()
        self.assertLessEqual(policy_bottom, viewport_bottom)
        self.assertGreater(self.ui.policy_combo.winfo_rooty(), self.ui.sidebar_canvas.winfo_rooty())
        self.assertEqual(self.ui.actions.winfo_rooty(), footer_y)
        self.ui.sidebar_canvas.yview_moveto(0)
        self.root.update()
        self.ui._reveal_sidebar_control(SimpleNamespace(widget=self.ui.policy_combo))
        self.root.update()
        self.assertLessEqual(self.ui.policy_combo.winfo_rooty() + self.ui.policy_combo.winfo_height(), viewport_bottom)

    def test_sidebar_scrollbar_stays_hidden_while_wheel_and_keyboard_work(self):
        self.root.attributes('-alpha', 0.0)
        self.root.geometry('940x700')
        self.root.deiconify()
        self.root.update()
        self.assertEqual(self.ui.sidebar_scroll.winfo_manager(), '')
        self.assertFalse(self.ui.sidebar_scroll.winfo_ismapped())
        start = self.ui.sidebar_canvas.yview()
        self.ui._scroll_sidebar(SimpleNamespace(delta=-120))
        self.root.update_idletasks()
        self.assertGreater(self.ui.sidebar_canvas.yview()[0], start[0])
        self.ui._reveal_sidebar_control(SimpleNamespace(widget=self.ui.policy_combo))
        self.root.update_idletasks()
        self.assertFalse(self.ui.sidebar_scroll.winfo_ismapped())
        self.assertLessEqual(self.ui.policy_combo.winfo_rooty() + self.ui.policy_combo.winfo_height(),
                             self.ui.sidebar_canvas.winfo_rooty() + self.ui.sidebar_canvas.winfo_height())
        self.assertTrue(self.ui.tree.cget('xscrollcommand'))
        self.assertTrue(self.ui.tree.cget('yscrollcommand'))

    def test_target_icons_and_two_row_layout_preserve_selection(self):
        self.root.attributes('-alpha', 0.0)
        self.root.geometry('940x700')
        self.root.deiconify()
        self.root.update()
        trae = self.ui.target_buttons['Trae']
        work = self.ui.target_buttons[trae_solo_adapter.SOLO_NAME]
        buddy = self.ui.target_buttons['WorkBuddy']
        self.assertEqual(trae.winfo_rooty(), work.winfo_rooty())
        self.assertGreater(work.winfo_rootx(), trae.winfo_rootx())
        self.assertGreater(buddy.winfo_rooty(), trae.winfo_rooty())
        self.assertEqual(int(buddy.grid_info()['columnspan']), 2)
        for name, button in self.ui.target_buttons.items():
            self.assertTrue(button.cget('image'))
            self.assertEqual(self.ui.target_icons[name].width(), 24)
            self.assertGreaterEqual(button.winfo_width(), button.winfo_reqwidth())
            self.assertIn(name, button.cget('text'))
            button.configure(state='normal')
            before = self.ui.target_vars[name].get()
            button.invoke()
            self.assertEqual(self.ui.target_vars[name].get(), not before)
            self.assertEqual(button.instate(['selected']), not before)
            self.assertNotIn('✓', button.cget('text'))
            self.assertNotIn('□', button.cget('text'))
            self.assertEqual(str(button.cget('compound')), 'left')
            self.assertEqual(button.cget('style'), 'Target.Round.TCheckbutton')

    def test_capabilities_use_two_rows_without_clipping_options(self):
        from tkinter import font
        self.root.attributes('-alpha', 0.0)
        self.root.deiconify()
        controls = self.ui.capability_combos + [self.ui.policy_combo]
        for geometry in ('940x700', '1280x860'):
            self.root.geometry(geometry)
            self.root.update()
            for index, combo in enumerate(controls):
                with self.subTest(geometry=geometry, index=index):
                    self.assertEqual(int(combo.master.grid_info()['row']), index // 2)
                    self.assertEqual(int(combo.master.grid_info()['column']), index % 2)
                    self.assertEqual(str(combo.cget('justify')), 'center')
                    text_font = font.Font(root=self.root, font=combo.cget('font'))
                    self.assertGreaterEqual(combo.winfo_width() - 29,
                                            max(text_font.measure(v) for v in combo.cget('values')))
                    self.assertLessEqual(combo.winfo_rootx() + combo.winfo_width(),
                                         self.ui.sidebar.winfo_rootx() + self.ui.sidebar.winfo_width())
            self.assertEqual(controls[0].winfo_rooty(), controls[1].winfo_rooty())
            self.assertEqual(controls[2].winfo_rooty(), controls[3].winfo_rooty())
            self.assertGreater(controls[2].winfo_rooty(), controls[0].winfo_rooty())

    def test_compact_layout_centers_controls_and_pairs_protocol_with_auth(self):
        from tkinter import ttk
        self.root.attributes('-alpha', 0.0)
        self.root.deiconify()
        for geometry in ('940x700', '1280x860'):
            self.root.geometry(geometry)
            self.root.update()
            self.assertEqual(self.ui.protocol_field.winfo_rooty(), self.ui.auth_field.winfo_rooty())
            self.assertGreater(self.ui.auth_field.winfo_rootx(), self.ui.protocol_field.winfo_rootx())
            for control in [self.ui.auth_combo, *self.ui.protocol_buttons.values()]:
                self.assertGreaterEqual(control.winfo_width(), control.winfo_reqwidth())
                self.assertLessEqual(control.winfo_rootx() + control.winfo_width(),
                                     self.ui.sidebar.winfo_rootx() + self.ui.sidebar.winfo_width())
        for combo in [self.ui.auth_combo, self.ui.policy_combo, *self.ui.capability_combos]:
            self.assertEqual(str(combo.cget('justify')), 'center')
        for entry in [self.ui.url_entry, self.ui.key_entry, self.ui.search_entry]:
            self.assertEqual(str(entry.cget('justify')), 'left')
        for column in self.ui.tree['displaycolumns']:
            self.assertEqual(str(self.ui.tree.column(column, 'anchor')), 'center')
        style = ttk.Style(self.root)
        for name in ['Round.TButton', 'Target.Round.TCheckbutton', 'Protocol.Round.TRadiobutton']:
            self.assertEqual(str(style.lookup(name, 'anchor')), 'center')
            self.assertEqual(str(style.lookup(name, 'justify')), 'center')
        self.assertLessEqual(int(style.lookup('Treeview', 'rowheight')), 34)

    def test_key_visibility_button_is_compact_and_keeps_width_when_toggled(self):
        self.root.attributes('-alpha', 0.0)
        self.root.deiconify()
        self.root.update()
        normal = self.ui._button(self.ui.key_toggle.master, '显示', lambda: None)
        try:
            self.root.update_idletasks()
            width = self.ui.key_toggle.winfo_reqwidth()
            self.assertLess(width, normal.winfo_reqwidth())
            entry_width = self.ui.key_entry.winfo_width()
            self.ui.key_toggle.invoke()
            self.root.update_idletasks()
            self.assertEqual(self.ui.key_toggle.cget('text'), '隐藏')
            self.assertEqual(self.ui.key_toggle.winfo_reqwidth(), width)
            self.assertEqual(self.ui.key_entry.winfo_width(), entry_width)
        finally:
            normal.destroy()

    def test_model_list_hides_both_scrollbars_but_native_scrolling_works(self):
        from tkinter import ttk
        self.root.attributes('-alpha', 0.0)
        self.root.geometry('940x700')
        self.root.deiconify()
        self.ui.ids = ['test-model-' + str(i) + '-long-name' * 25 for i in range(100)]
        self.ui.refresh()
        self.root.update()
        bars = [w for w in self.ui.tree.master.winfo_children() if isinstance(w, ttk.Scrollbar)]
        self.assertEqual(len(bars), 2)
        for bar in bars:
            self.assertFalse(bar.winfo_ismapped())
            self.assertEqual(bar.winfo_manager(), '')
        before_sidebar = self.ui.sidebar_canvas.yview()
        self.ui.tree.event_generate('<MouseWheel>', delta=-120)
        self.root.update()
        self.assertGreater(self.ui.tree.yview()[0], 0)
        self.ui.tree.event_generate('<Shift-MouseWheel>', delta=-120)
        self.root.update()
        self.assertGreater(self.ui.tree.xview()[0], 0)
        self.assertEqual(self.ui.sidebar_canvas.yview(), before_sidebar)
        self.ui.tree.focus_force()
        self.ui.tree.focus('0')
        self.ui.tree.selection_set('0')
        self.ui.tree.event_generate('<Next>')
        self.root.update()
        self.assertGreater(self.ui.tree.yview()[0], 0)
        for bar in bars:
            self.assertFalse(bar.winfo_ismapped())

    def test_sidebar_focus_binding_excludes_ancestor_containers(self):
        self.assertFalse(self.ui.sidebar.bind('<FocusIn>'))
        for combo in self.ui.capability_combos + [self.ui.policy_combo]:
            self.assertFalse(combo.master.bind('<FocusIn>'))
            self.assertIn('_on_sidebar_control_focus', combo.bind('<FocusIn>'))

    def test_non_owner_focus_notification_does_not_reveal_control(self):
        self.root.attributes('-alpha', 0.0)
        self.root.deiconify()
        self.root.focus_force()
        self.root.update()
        with patch.object(self.ui, '_reveal_sidebar_control') as reveal:
            self.ui._on_sidebar_control_focus(SimpleNamespace(widget=self.ui.policy_combo))
        reveal.assert_not_called()

    def test_real_keyboard_focus_still_reveals_offscreen_control(self):
        self.root.attributes('-alpha', 0.0)
        self.root.geometry('940x700')
        self.root.deiconify()
        self.root.focus_force()
        self.root.update()
        self.ui.sidebar_canvas.yview_moveto(0)
        self.root.update()
        self.ui.policy_combo.focus_force()
        # Windows may deliver native focus messages after the initial update.
        self.root.after(80, self.root.quit)
        self.root.mainloop()
        self.root.update()
        self.assertEqual(str(self.root.tk.call('focus')), str(self.ui.policy_combo))
        self.assertGreater(self.ui.sidebar_canvas.yview()[0], 0)
        self.assertLessEqual(self.ui.policy_combo.winfo_rooty() + self.ui.policy_combo.winfo_height(),
                             self.ui.sidebar_canvas.winfo_rooty() + self.ui.sidebar_canvas.winfo_height())

    def test_native_dropdown_open_select_and_cancel_do_not_scroll_sidebar(self):
        self.root.attributes('-alpha', 0.0)
        self.root.deiconify()
        for geometry in ('940x700', '1280x860'):
            self.root.geometry(geometry)
            self.root.update()
            for combo in self.ui.capability_combos + [self.ui.policy_combo]:
                for action in ('cancel', 'select'):
                    with self.subTest(geometry=geometry, combo=str(combo), action=action):
                        self.root.focus_force()
                        self.root.update()
                        self.ui.sidebar_canvas.yview_moveto(1)
                        self.root.update()
                        before = self.ui.sidebar_canvas.yview()
                        with patch.object(self.ui.sidebar_canvas, 'yview_moveto',
                                          wraps=self.ui.sidebar_canvas.yview_moveto) as scroll:
                            combo.event_generate('<ButtonPress-1>', x=combo.winfo_width()-10,
                                                 y=combo.winfo_height()//2)
                            combo.event_generate('<ButtonRelease-1>', x=combo.winfo_width()-10,
                                                 y=combo.winfo_height()//2)
                            self.root.update()
                            popup = str(self.root.tk.call('ttk::combobox::PopdownWindow', str(combo)))
                            try:
                                self.assertTrue(int(self.root.tk.call('winfo', 'ismapped', popup)))
                                listbox = popup + '.f.l'
                                if action == 'select':
                                    self.root.tk.call(listbox, 'selection', 'clear', 0, 'end')
                                    self.root.tk.call(listbox, 'selection', 'set', 1)
                                    self.root.tk.call('event', 'generate', listbox, '<Return>')
                                else:
                                    self.root.tk.call('event', 'generate', listbox, '<Escape>')
                                self.root.update()
                                self.assertFalse(int(self.root.tk.call('winfo', 'ismapped', popup)))
                                if action == 'select':
                                    self.assertEqual(combo.current(), 1)
                                scroll.assert_not_called()
                                self.assertEqual(self.ui.sidebar_canvas.yview(), before)
                            finally:
                                self.root.tk.call('ttk::combobox::Unpost', str(combo))

    def test_sidebar_repaint_coalesces_changed_views_and_ignores_duplicates(self):
        self.root.update_idletasks()
        self.ui._sidebar_last_view = None
        with patch.object(self.root, 'after_idle', return_value='test-id') as schedule, \
                patch('modern_ui.finish_viewport_paint') as paint:
            self.ui._sidebar_view_changed('0.1', '0.5')
            self.ui._sidebar_view_changed('0.2', '0.6')
            self.ui._sidebar_view_changed('0.3', '0.7')
            schedule.assert_called_once_with(self.ui._finish_sidebar_paint)
            self.ui._finish_sidebar_paint()
            paint.assert_called_once_with(self.ui.sidebar_canvas)
            self.assertIsNone(self.ui._sidebar_repaint_id)
            self.ui._sidebar_view_changed('0.3000', '0.7000')
            self.assertEqual(schedule.call_count, 1)

    def test_scrollbar_wheel_and_keyboard_reveal_all_schedule_local_repaint(self):
        self.root.attributes('-alpha', 0.0)
        self.root.geometry('940x700')
        self.root.deiconify()
        self.root.update()
        with patch('modern_ui.finish_viewport_paint') as paint:
            command = self.root.tk.splitlist(self.ui.sidebar_scroll.cget('command'))
            self.root.tk.call(*command, 'moveto', .2)
            self.root.update_idletasks()
            paint.assert_called_with(self.ui.sidebar_canvas)
            paint.reset_mock()
            # Scroll upward: the compact sidebar may already be at its bottom.
            self.ui._scroll_sidebar(SimpleNamespace(delta=120))
            self.root.update_idletasks()
            paint.assert_called_with(self.ui.sidebar_canvas)
            self.ui.sidebar_canvas.yview_moveto(0)
            self.root.update_idletasks()
            paint.reset_mock()
            self.ui._reveal_sidebar_control(SimpleNamespace(widget=self.ui.policy_combo))
            self.root.update_idletasks()
            paint.assert_called_with(self.ui.sidebar_canvas)

    def test_close_cancels_pending_sidebar_paint(self):
        self.root.update_idletasks()
        self.ui._sidebar_last_view = None
        self.ui._sidebar_view_changed('0.1', '0.5')
        job = self.ui._sidebar_repaint_id
        with patch.object(self.root, 'destroy'), patch.object(self.root, 'after_cancel', wraps=self.root.after_cancel) as cancel:
            self.ui.close()
        cancel.assert_any_call(job)
        self.assertIsNone(self.ui._sidebar_repaint_id)
        with patch('modern_ui.finish_viewport_paint') as paint:
            self.ui._finish_sidebar_paint()
        paint.assert_not_called()

    def test_sidebar_wheel_does_not_bind_globally_or_to_table(self):
        self.assertIn('_scroll_sidebar', self.ui.url_entry.bind('<MouseWheel>'))
        self.assertNotIn('_scroll_sidebar', self.root.bind_all('<MouseWheel>'))
        self.assertNotIn('_scroll_sidebar', self.ui.tree.bind('<MouseWheel>'))

    def test_key_visibility_toggle_only_changes_mask(self):
        original = self.ui.key.get()
        self.assertTrue(self.ui.key_entry.cget('show'))
        self.ui.key_toggle.invoke()
        self.assertEqual(self.ui.key_entry.cget('show'), '')
        self.assertEqual(self.ui.key_toggle.cget('text'), '隐藏')
        self.ui.key_toggle.invoke()
        self.assertTrue(self.ui.key_entry.cget('show'))
        self.assertEqual(self.ui.key.get(), original)
        self.assertNotIn(original, self.ui.status.get())

    def test_protocol_segments_keep_native_target_rules(self):
        self.ui.protocol_buttons['Anthropic'].invoke()
        self.assertEqual(self.ui.current_protocol(), 'anthropic')
        self.assertEqual(self.ui.selected_import_targets(), [trae_solo_adapter.SOLO_NAME])
        self.assertEqual(str(self.ui.auth_combo.cget('state')), 'readonly')
        self.ui.protocol_buttons['OpenAI'].invoke()
        self.assertEqual(self.ui.current_protocol(), 'openai')
        self.assertEqual(str(self.ui.auth_combo.cget('state')), 'disabled')

    def test_empty_state_and_counts_follow_real_list(self):
        self.assertEqual(self.ui.empty_panel.winfo_manager(), '')
        self.assertIn('共 3 个', self.ui.selected_count.get())
        self.ui.search.set('no-match')
        self.assertEqual(self.ui.empty_title.cget('text'), '没有匹配的模型')
        self.assertEqual(self.ui.empty_panel.winfo_manager(), 'place')
        self.ui.ids = []
        self.ui.refresh()
        self.assertEqual(self.ui.empty_title.cget('text'), '尚未获取模型列表')
        self.assertIn('共 0 个', self.ui.selected_count.get())

    def test_selected_rows_receive_visual_highlight(self):
        self.ui.select_visible(True)
        self.assertIn('checked', self.ui.tree.item('0', 'tags'))
        self.assertIn('已选择 3', self.ui.selected_count.get())
        self.ui.select_visible(False)
        self.assertNotIn('checked', self.ui.tree.item('0', 'tags'))

    def test_status_badge_tracks_busy_success_and_failure(self):
        self.ui.busy = True
        self.ui.status.set('正在读取模型列表…')
        self.assertIn('正在处理', self.ui.status_badge_text.get())
        self.ui.busy = False
        self.ui.status.set('已读取 3 个')
        self.assertEqual(str(self.ui.footer_status.cget('fg')), self.ui.COLORS['accent'])
        with patch.object(app.messagebox, 'showerror'):
            self.ui.report(core.UserError('测试失败'))
        self.assertIn('操作未完成', self.ui.status_badge_text.get())
        self.ui.status.set('协议已切换，请重新获取模型列表')
        self.assertIn('等待连接网关', self.ui.status_badge_text.get())

    def test_busy_disables_protocol_segments_and_poll_restores(self):
        with patch('modern_ui.threading.Thread'):
            self.ui.run(lambda: [], lambda result: None, '测试读取')
        for button in self.ui.protocol_buttons.values():
            self.assertEqual(str(button.cget('state')), 'disabled')
        self.ui.events.put((lambda result: self.ui.status.set('已读取 0 个'), [], None))
        self.root.after_cancel(self.ui.poll_id)
        self.ui.poll()
        for button in self.ui.protocol_buttons.values():
            self.assertEqual(str(button.cget('state')), 'normal')
        self.assertIn('操作完成', self.ui.status_badge_text.get())


if __name__ == '__main__': unittest.main()
