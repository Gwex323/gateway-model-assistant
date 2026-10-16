"""Metadata-first model classification, with offline client fixtures only."""
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

import core
from model_capabilities import chat_metadata_support, chat_model_support
from modern_ui import App
import trae_adapter
import trae_solo_adapter
from test_trae_adapter import make_db


class ClassificationTests(unittest.TestCase):
    def test_metadata_declarations_and_nested_containers(self):
        for metadata in (
            {'model_type': 'chat_model'}, {'task': 'conversational'},
            {'metadata': {'mode': 'chat'}}, {'capabilities': {'chat': {'supported': True}}},
            {'supported_endpoints': ['/v1/chat/completions']}, {'endpoints': ['/v1/messages']},
            {'architecture': {'input_modalities': ['text', 'image'], 'output_modalities': ['text']}},
            {'architecture': {'modality': 'text+video->text'}},
            {'modalities': {'input': ['text', 'image'], 'output': ['text']}},
        ):
            with self.subTest(metadata=metadata):
                self.assertIs(chat_metadata_support(metadata), True)
        for metadata in (
            {'model_type': 'embedding'}, {'pipeline_tag': 'text-to-image'},
            {'task': 'automatic-speech-recognition'}, {'mode': 'rerank'},
            {'supportsChat': False}, {'supported_endpoints': ['/v1/embeddings']},
            {'capabilities': {'outputModalities': ['image']}},
            {'architecture': {'modality': 'text->video'}},
            {'modalities': {'output': ['audio']}},
        ):
            with self.subTest(metadata=metadata):
                self.assertIs(chat_metadata_support(metadata), False)

    def test_metadata_overrides_both_positive_and_negative_name_guesses(self):
        positive = core._gateway_model({'id': 'gpt-image-1', 'model_type': 'chat'})
        negative = core._gateway_model({'id': 'gpt-4o', 'model_type': 'embedding'})
        self.assertIs(chat_model_support(positive.id, positive.supports_chat), True)
        self.assertIs(chat_model_support(negative.id, negative.supports_chat), False)
        self.assertIs(chat_model_support('gpt-image-1'), False)
        self.assertIs(chat_model_support('gpt-4o'), True)

    def test_partial_malformed_and_generic_fields_do_not_invent_chat_support(self):
        for metadata in (None, [], {'type': 'model'}, {'object': 'model'},
                         {'supports_chat': 'false'}, {'supports_chat': 1},
                         {'input_modalities': ['image']}, {'output_modalities': ['text']},
                         {'output_modalities': ['text', 'audio']},
                         {'output_modalities': 'image'}, {'output_modalities': [1]},
                         {'endpoints': ['https://untrusted.example/chat/completions']},
                         {'endpoints': ['/v1/responses']}, {'task': 'unknown'}):
            with self.subTest(metadata=metadata):
                self.assertIsNone(chat_metadata_support(metadata))
        self.assertIsNone(chat_model_support('opaque-name'))
        self.assertIsNone(chat_model_support('video-understanding-alias'))
        self.assertIs(chat_model_support('qwen-vl'), True)
        self.assertIs(chat_model_support('Seedance-2'), False)

    def test_conflicts_are_conservative_and_cycles_are_bounded(self):
        for metadata in (
            {'supports_chat': True, 'task': 'embedding'},
            {'supported_endpoints': ['/v1/chat/completions'], 'output_modalities': ['image']},
            {'input_modalities': ['text'], 'output_modalities': ['text'],
             'metadata': {'output_modalities': ['audio']}},
        ):
            self.assertIs(chat_metadata_support(metadata), False)
        cyclic = {'supports_chat': True}
        cyclic['metadata'] = cyclic
        self.assertIs(chat_metadata_support(cyclic), True)
        nested = {'metadata': {'metadata': {'metadata': {'metadata': {'metadata': {'supports_chat': False}}}}}}
        self.assertIsNone(chat_metadata_support(nested))

    def test_duplicates_keep_metadata_above_names_and_negative_wins(self):
        catalog = core._parse_openai_catalog({'data': [
            {'id': 'gpt-image-1', 'task': 'conversational'}, {'id': 'gpt-image-1'},
            {'id': 'opaque', 'supports_chat': True}, {'id': 'opaque', 'mode': 'embedding'},
        ]})
        self.assertIs(catalog[0].supports_chat, True)
        self.assertIs(catalog[1].supports_chat, False)
        self.assertIs(chat_model_support(catalog[0].id, catalog[0].supports_chat), True)
        native = core._anthropic_model({'id': 'opaque', 'supports_chat': False})
        self.assertIs(native.supports_chat, False)

    def test_adapters_reject_metadata_negative_for_unknown_ids(self):
        target = trae_adapter.Target(trae_solo_adapter.SOLO_NAME, Path('missing.vscdb'), 'test')
        for make in (
            lambda **kwargs: core.make_plan(None, 'https://gateway.example/v1', 'fake-key', ['opaque'], **kwargs),
            lambda **kwargs: trae_adapter.make_plan(target, 'https://gateway.example/v1', 'fake-key', ['opaque'], **kwargs),
            lambda **kwargs: trae_solo_adapter.make_plan(target, 'https://gateway.example/v1', 'fake-key', ['opaque'], **kwargs),
        ):
            with self.assertRaisesRegex(core.UserError, core.UNSUPPORTED_IMPORT_HINT):
                make(chat_support_by_model={'opaque': False})
            for value in ([], {'opaque': 'false'}, {'opaque': 1}):
                with self.assertRaisesRegex(core.UserError, '类型设置不合法'):
                    make(chat_support_by_model=value)

    def test_native_anthropic_uses_metadata_before_name(self):
        target = trae_adapter.Target(trae_solo_adapter.SOLO_NAME, Path('missing.vscdb'), 'test')
        plan = trae_solo_adapter.make_plan(
            target, 'https://gateway.example/anthropic', 'fake-key', ['Seedance-2'],
            protocol='anthropic', chat_support_by_model={'Seedance-2': True},
        )
        self.assertEqual(plan.ids, ['Seedance-2'])
        with self.assertRaisesRegex(core.UserError, core.UNSUPPORTED_IMPORT_HINT):
            trae_solo_adapter.make_plan(
                target, 'https://gateway.example/anthropic', 'fake-key', ['opaque'],
                protocol='anthropic', chat_support_by_model={'opaque': False},
            )

    def test_fetch_selection_and_all_three_plans_use_the_same_metadata(self):
        with tempfile.TemporaryDirectory(prefix='gateway-classification-test-') as directory:
            root = tk.Tk()
            root.withdraw()
            app = App(root)
            try:
                app.path = Path(directory) / 'models.json'
                app.path.write_text('[]', encoding='utf-8')
                db = Path(directory) / 'state.vscdb'
                make_db(db)
                app.trae_targets = [trae_adapter.Target(name, db, 'test')
                                    for name in ('Trae', trae_solo_adapter.SOLO_NAME)]
                for name, var in app.target_vars.items():
                    var.set(True)
                app.url.set('https://gateway.example/v1')
                app.key.set('fake-key')
                catalog = core._parse_openai_catalog({'data': [
                    {'id': 'opaque-generator', 'architecture': {'output_modalities': ['image']}},
                    {'id': 'gpt-image-1', 'task': 'conversational', 'input_modalities': ['text', 'image']},
                    {'id': 'unknown-alias'},
                ]})
                with patch.object(app, 'run') as run, patch('modern_ui.core.fetch_model_catalog', return_value=catalog):
                    app.fetch()
                    run.call_args.args[1](run.call_args.args[0]())
                rows = {mid: str(i) for i, mid in enumerate(app.ids)}
                self.assertEqual(app.tree.set(rows['opaque-generator'], 'import_status'), core.UNSUPPORTED_IMPORT_HINT)
                self.assertEqual(app.tree.set(rows['gpt-image-1'], 'import_status'), '可导入')
                self.assertEqual(app.tree.set(rows['unknown-alias'], 'import_status'), '类型待确认')
                app.toggle_row(rows['opaque-generator'])
                self.assertFalse(app.selected)
                app.select_visible(True)
                self.assertEqual(app.selected, {'gpt-image-1', 'unknown-alias'})
                plans = app.build_import_plans('https://gateway.example/v1', 'fake-key', sorted(app.selected), {})
                self.assertEqual({kind for _, kind, _, _ in plans}, {'workbuddy', 'trae', 'trae_solo'})
                buddy = next(plan for _, kind, _, plan in plans if kind == 'workbuddy')
                self.assertTrue(core.model_array(buddy.document)[0]['supportsImages'])
                solo = next(plan for _, kind, _, plan in plans if kind == 'trae_solo')
                self.assertIn('gpt-image-1', solo.ids)
                app.existing_policy.set('覆盖已有')
                with self.assertRaisesRegex(core.UserError, core.UNSUPPORTED_IMPORT_HINT):
                    app.build_import_plans('https://gateway.example/v1', 'fake-key', ['opaque-generator'], {})
                app.auth_changed()
                self.assertEqual(app.model_chat_support, {})
                app.model_chat_support = {'stale-id': True}
                app.protocol.set('Anthropic')
                app.protocol_changed()
                self.assertEqual(app.model_chat_support, {})
            finally:
                app.close()
