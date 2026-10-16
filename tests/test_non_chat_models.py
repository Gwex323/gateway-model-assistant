"""Non-chat import restrictions; synthetic data only."""
import tkinter as tk
import unittest
from pathlib import Path

import core
import trae_adapter
import trae_solo_adapter
from modern_ui import App


BLOCKED = ('Seedream-4', 'Seedance-2', 'MiniMax-H3', 'gpt-image-1',
           'gemini-2.5-flash-image', 'vendor/flux.1-dev', 'text-embedding-3-small',
           'bge-m3-embedding', 'rerank-3', 'whisper-1', 'tts-1',
           'kling', 'Kling-v2.1-master', 'kwai-kling-video-o1', 'klingai-2.0',
           'kling/video-o1', 'vendor/kling/2.1', 'hy-3d', 'hy-3d-3.1',
           'hy_3d_v2', 'HY3D-2.1', 'tencent/hunyuan3d-2.1', 'hunyuan-3d-2',
           'opaque-text-to-3d', 'opaque-image-to-3d',
           'tripo', 'Tripo-v3', 'tripo3d-v2', 'tripo-3d', 'tripo-text-to-model', 'tripo/model')
ALLOWED = ('qwen-vl', 'deepseek-vision-exp', 'gemini-2.5-pro',
           'video-understanding-model', 'image-reader', 'unknown-model',
           'klingon-chat', 'hybrid-chat', 'hunyuan-vision', '3d-scene-understanding', 'mesh-reader',
           'tripod-chat', 'tripoli-vision')


class NonChatTests(unittest.TestCase):
    def test_classification_preserves_visual_understanding_and_unknowns(self):
        for mid in BLOCKED:
            with self.subTest(mid=mid):
                self.assertTrue(core.unsupported_chat_model(mid))
        for mid in ALLOWED:
            with self.subTest(mid=mid):
                self.assertFalse(core.unsupported_chat_model(mid))

    def test_all_adapters_reject_before_accessing_client_data(self):
        target = trae_adapter.Target(trae_solo_adapter.SOLO_NAME, Path('missing-state.vscdb'), 'test')
        for mid in BLOCKED:
            for make in (
                lambda: core.make_plan(None, 'https://gateway.example/v1', 'fake-key', [mid]),
                lambda: trae_adapter.make_plan(target, 'https://gateway.example/v1', 'fake-key', [mid]),
                lambda: trae_solo_adapter.make_plan(target, 'https://gateway.example/v1', 'fake-key', [mid]),
            ):
                with self.subTest(mid=mid), self.assertRaisesRegex(core.UserError, core.UNSUPPORTED_IMPORT_HINT):
                    make()

    def test_ui_mouse_keyboard_select_all_and_stale_selection(self):
        root = tk.Tk()
        root.withdraw()
        app = App(root)
        try:
            app.ids = list(BLOCKED + ALLOWED)
            app.refresh()
            self.assertEqual(len(app.tree.get_children()), len(app.ids))
            for i, mid in enumerate(BLOCKED):
                row = str(i)
                app.toggle_row(row)
                app.tree.focus(row)
                app.toggle_keyboard(None)
                self.assertNotIn(mid, app.selected)
                self.assertIn('unsupported', app.tree.item(row, 'tags'))
                self.assertEqual(app.token_display_values(mid), (core.UNSUPPORTED_IMPORT_HINT,) * 2)
            app.select_visible(True)
            self.assertEqual(app.selected, set(ALLOWED))
            self.assertIn('unsupported', app.tree.item('0', 'tags'))
            app.search.set('Seedance')
            self.assertEqual(len(app.tree.get_children()), 1)
            app.select_visible(True)
            self.assertEqual(app.selected, set(ALLOWED))
            app.selected.add(BLOCKED[0])
            app.refresh()
            self.assertNotIn(BLOCKED[0], app.selected)
            with self.assertRaisesRegex(core.UserError, core.UNSUPPORTED_IMPORT_HINT):
                app.build_import_plans('https://gateway.example/v1', 'fake-key', [BLOCKED[0]], {})
        finally:
            app.close()
