"""Offline packaged-runtime check; uses only temporary test model data."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import ssl
import sys
import tempfile
import time
import tkinter as tk
from tkinter import ttk

from app_metadata import VERSION
from desktop_runtime import asset_path
from modern_ui import App
import core
import trae_adapter
import trae_solo_adapter


def run_selftest(report_path):
    started = time.perf_counter()
    root = tk.Tk()
    root.withdraw()
    app = None
    result = {'version': VERSION, 'frozen': bool(getattr(sys, 'frozen', False)), 'ok': False}
    exit_code = 1
    try:
        app = App(root)
        with tempfile.TemporaryDirectory(prefix='gateway-app-selftest-') as directory:
            config = Path(directory) / 'models.json'
            config.write_text('[]', encoding='utf-8')
            plan = core.make_plan(config.read_bytes(), 'https://gateway.example/v1', 'selftest-not-a-real-key',
                                  ['selftest-model'], capabilities={}, update_existing=False,
                                  token_limits_by_model={'selftest-model': {'maxInputTokens': 128000, 'maxOutputTokens': 8192}})
            assert plan.changed
            assert core.model_array(plan.document)[0]['id'] == 'selftest-model'
            assert core.model_array(plan.document)[0]['url'] == 'https://gateway.example/v1/chat/completions'
            vision_plan = core.make_plan(None, 'https://gateway.example/v1', 'selftest-fake-key', ['gpt-4o', 'text-unknown'])
            assert [m['supportsImages'] for m in vision_plan.document] == [True, False]
            assert app.flags['supportsImages'].get() == '自动识别'
            assert trae_adapter._endpoint('https://gateway.example/v1/chat/completions') == 'https://gateway.example/v1'
            app.ids = ['selftest-model', 'Seedance-2.0']
            app.model_token_limits = {'selftest-model': {'maxInputTokens': 128000, 'maxOutputTokens': 8192}}
            app.refresh()
            app.toggle_row('0')
            assert app.selected == {'selftest-model'}
            assert app.tree.item('1', 'values')[2:4] == ('不支持导入到目标应用', '不支持导入到目标应用')
            app.toggle_row('1')
            app.select_visible(True)
            assert app.selected == {'selftest-model'}
            assert 'unsupported' in app.tree.item('1', 'tags')
            result['non_chat_models_disabled'] = True
            assert app.tree.set('0', 'import_status') == '类型待确认'
            declared = core._gateway_model({'id': 'Seedance-2.0', 'model_type': 'chat'})
            app.model_chat_support[declared.id] = declared.supports_chat
            app.refresh()
            assert app.tree.set('1', 'import_status') == '可导入'
            app.toggle_row('1')
            assert 'Seedance-2.0' in app.selected
            app.model_chat_support['Seedance-2.0'] = False
            app.refresh()
            assert 'Seedance-2.0' not in app.selected
            result['metadata_first_model_classification'] = True
            for mid in ('kling-v2.1', 'hy-3d', 'tencent/hunyuan3d-2.1'):
                assert core.chat_model_support(mid) is False
            assert core._gateway_model({'id': 'opaque-3d', 'output_modalities': ['mesh']}).supports_chat is False
            app.ids.extend(['kling-v2.1', 'hy-3d'])
            app.refresh()
            app.select_visible(True)
            assert not {'kling-v2.1', 'hy-3d'} & app.selected
            assert app.tree.set('2', 'import_status') == core.UNSUPPORTED_IMPORT_HINT
            assert app.tree.set('3', 'import_status') == core.UNSUPPORTED_IMPORT_HINT
            result['video_3d_models_disabled'] = True
            from sgt_cn_specs import parse_mimo
            mimo_spec = parse_mimo(
                '<table><tr><td>`mimo-v2.6-pro`<br/>`mimo-v2.6-flash`</td>'
                '<td>Context Window: 1M<br/>Maximum Output: 128K</td></tr></table>',
                'mimo-v2.6-flash')
            assert mimo_spec is not None and mimo_spec.output_limit == 128000
            app.ids.extend(['tripo', 'mimo-v2.6-flash'])
            app.model_token_limits['mimo-v2.6-flash'] = {
                'maxInputTokens': mimo_spec.input_limit - mimo_spec.output_limit,
                'maxOutputTokens': mimo_spec.output_limit,
            }
            app.refresh()
            app.select_visible(True)
            assert 'tripo' not in app.selected and 'mimo-v2.6-flash' in app.selected
            assert app.tree.set('4', 'import_status') == core.UNSUPPORTED_IMPORT_HINT
            assert app.token_display_values('mimo-v2.6-flash') == ('872000', '128000')
            result['mimo_specs_tripo_verified'] = True
            assert 'hint' not in app.tree['displaycolumns']
            assert asset_path('app.png').is_file()
            assert root._desktop_icon.width() == 256 and root._desktop_icon.transparency_get(0, 0)
            assert app.COLORS['accent'] == '#24633A'
            red, green, blue = root._desktop_icon.get(128, 128)
            assert green > red and green > blue
            assert app._model_column_widths
            assert ssl.create_default_context().verify_mode == ssl.CERT_REQUIRED
            with closing(sqlite3.connect(':memory:')) as database:
                assert database.execute('SELECT 1').fetchone() == (1,)
            assert callable(trae_adapter.make_plan) and callable(trae_solo_adapter.make_plan)
            root.attributes('-alpha', 0.0)
            root.deiconify()
            root.update()
            assert all(str(b.cget('compound')) == 'left' and '✓' not in b.cget('text') and '□' not in b.cget('text') for b in app.target_buttons.values())
            assert app.key_toggle.cget('style') == 'Compact.Round.TButton'
            assert app.protocol_field.winfo_rooty() == app.auth_field.winfo_rooty()
            assert str(app.auth_combo.cget('justify')) == 'center'
            assert app.capability_combos[0].winfo_rooty() == app.capability_combos[1].winfo_rooty()
            assert app.capability_combos[2].winfo_rooty() == app.policy_combo.winfo_rooty()
            assert len(app.target_icons) == 3
            assert all(button.cget('image') for button in app.target_buttons.values())
            assert app.target_buttons['Trae'].winfo_rooty() == app.target_buttons['TraeWork CN'].winfo_rooty()
            assert app.target_buttons['WorkBuddy'].winfo_rooty() > app.target_buttons['Trae'].winfo_rooty()
            assert not hasattr(app, 'header') and not hasattr(app, 'brand_logo')
            assert app.workspace.winfo_y() == 0
            assert app.footer_status.winfo_ismapped()
            assert not hasattr(app, 'restore') and not hasattr(app, 'export')
            assert app.import_button.winfo_ismapped()
            assert app.select_all_button.master is app.search_entry.master
            assert app.select_all_button.winfo_ismapped()
            assert not hasattr(app, 'hide_media')
            assert app.actions.master is app.models_panel
            assert app.import_button.master is app.actions
            assert app.table_hint.master is app.actions
            assert all(int(w.grid_info()['row']) == 0 for w in (app.footer_status, app.table_hint, app.import_button))
            assert not root.grid_slaves(row=1)
            assert not app.sidebar_scroll.winfo_ismapped()
            bars = [w for w in app.tree.master.winfo_children() if isinstance(w, ttk.Scrollbar)]
            assert len(bars) == 2 and all(not w.winfo_ismapped() for w in bars)
            for fraction in (.15, .4, .1, 0):
                app.sidebar_canvas.yview_moveto(fraction)
                root.update_idletasks()
            result.update(ok=True, ui_loaded=True, icon_loaded=True, tls_verified=True,
                          sidebar_scrollbar_hidden=True, model_scrollbars_hidden=True, target_icons_loaded=True, compact_layout=True, capabilities_two_rows=True, green_theme_green_brand=True, header_removed=True, per_app_urls=True, auto_image_input=True, simplified_actions=True, actions_inside_model_panel=True, unified_model_footer=True, search_select_all=True,
                          temporary_workbuddy_plan=True, adapter_modules_loaded=True,
                          python_runtime=sys.version.split()[0], tk_runtime=root.tk.call('info', 'patchlevel'),
                          elapsed_seconds=round(time.perf_counter() - started, 3))
            exit_code = 0
    except Exception as error:
        # Never include request payloads or exception bodies in a diagnostic report.
        result['error_type'] = type(error).__name__
    finally:
        if app is not None:
            app.close()
        else:
            root.destroy()
    Path(report_path).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return exit_code
