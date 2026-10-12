"""Standalone application resources and offline self-test contract."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_metadata import VERSION
from desktop_runtime import asset_path
from app_selftest import run_selftest


class DesktopAppTests(unittest.TestCase):
    def test_icon_assets_are_resolved_from_module_not_working_directory(self):
        self.assertTrue(asset_path('app.png').is_absolute())
        self.assertEqual(asset_path('app.png').read_bytes()[:8], b'\x89PNG\r\n\x1a\n')
        self.assertEqual(asset_path('app.ico').read_bytes()[:4], b'\x00\x00\x01\x00')

    def test_offline_selftest_never_calls_network_or_writes_client_config(self):
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / 'selftest.json'
            with patch('core._request_json', side_effect=AssertionError('No network')), \
                    patch('core.apply_plan', side_effect=AssertionError('No client writes')), \
                    patch('trae_adapter.apply_plan', side_effect=AssertionError('No client writes')), \
                    patch('trae_solo_adapter.apply_plan', side_effect=AssertionError('No client writes')):
                self.assertEqual(run_selftest(report), 0)
            result = json.loads(report.read_text(encoding='utf-8'))
            self.assertTrue(result['ok'])
            self.assertEqual(result['version'], VERSION)
            self.assertTrue(result['temporary_workbuddy_plan'])
            self.assertNotIn('selftest-not-a-real-key', report.read_text(encoding='utf-8'))
