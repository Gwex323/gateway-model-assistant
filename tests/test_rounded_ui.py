"""Rounded rendering assets and native control behavior; no client or network I/O."""
import struct
import tkinter as tk
from tkinter import ttk
import unittest
import zlib
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modern_ui import App
from rounded_ui import RoundedTheme, rounded_png


class RoundedAssetTests(unittest.TestCase):
    def pixels(self, png):
        offset, compressed = 8, bytearray()
        while offset < len(png):
            size = struct.unpack('!I', png[offset:offset + 4])[0]
            kind = png[offset + 4:offset + 8]
            body = png[offset + 8:offset + 8 + size]
            if kind == b'IHDR':
                width, height = struct.unpack('!II', body[:8])
            if kind == b'IDAT':
                compressed.extend(body)
            offset += size + 12
        data = zlib.decompress(compressed)
        return [[tuple(data[y * (width * 4 + 1) + 1 + x * 4:y * (width * 4 + 1) + 5 + x * 4])
                 for x in range(width)] for y in range(height)]

    def test_all_four_corners_transparent_center_opaque(self):
        pixels = self.pixels(rounded_png(26, 26, 10, '#24633A', '#E2E7E9'))
        for x, y in ((0, 0), (25, 0), (0, 25), (25, 25)):
            self.assertEqual(pixels[y][x][3], 0)
        self.assertEqual(pixels[13][13], (36, 99, 58, 255))
        self.assertEqual(pixels[0][13], (226, 231, 233, 255))

    def test_rounded_edges_have_partial_alpha_and_are_symmetric(self):
        pixels = self.pixels(rounded_png(26, 26, 10, '#24633A'))
        self.assertTrue(any(0 < p[3] < 255 for row in pixels for p in row))
        self.assertEqual(pixels, pixels[::-1])
        for row in pixels:
            self.assertEqual(row, row[::-1])

    def test_render_assets_are_cached(self):
        a = rounded_png(26, 26, 10, '#24633A')
        self.assertIs(a, rounded_png(26, 26, 10, '#24633A'))

    def test_fast_interior_matches_original_supersampling(self):
        from diagnostics.rounded_ui_before import rounded_png as reference
        for width, height, radius, stroke in ((22, 22, 8, 1), (80, 80, 8, 1),
                (88, 88, 12, 1), (19, 13, 4.5, 2), (9, 7, 0, 1),
                (11, 13, 20, 3), (15, 15, 2, 4)):
            for border in (None, '#E2E7E9'):
                with self.subTest(size=(width, height), radius=radius, border=border):
                    args = width, height, radius, '#24633A', border, stroke
                    self.assertEqual(rounded_png(*args), reference(*args))

    def test_larger_tile_preserves_nine_slice_pixels(self):
        for radius in (8, 12, 16):
            old_size, new_size = radius * 2 + 6, radius * 2 + 64
            old = self.pixels(rounded_png(old_size, old_size, radius, '#24633A', '#E2E7E9'))
            new = self.pixels(rounded_png(new_size, new_size, radius, '#24633A', '#E2E7E9'))
            def coord(value, target, source):
                if value < radius:
                    return value
                if value >= target - radius:
                    return source - (target - value)
                return radius + (value - radius) % (source - 2 * radius)
            for width, height in ((39, 39), (101, 85), (720, 60)):
                for y in range(height):
                    for x in range(width):
                        self.assertEqual(old[coord(y, height, old_size)][coord(x, width, old_size)],
                                         new[coord(y, height, new_size)][coord(x, width, new_size)])


class RoundedNativeTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        ttk.Style(self.root).theme_use('clam')
        self.theme = RoundedTheme(self.root, App.COLORS)

    def tearDown(self):
        self.root.destroy()

    def test_button_disabled_and_keyboard_invocation_remain_native(self):
        calls = []
        button = ttk.Button(self.root, text='Test', style='Primary.Round.TButton', command=lambda: calls.append(1))
        button.pack()
        button.configure(state='disabled')
        button.invoke()
        self.assertEqual(calls, [])
        button.configure(state='normal')
        button.invoke()
        self.assertEqual(calls, [1])
        self.root.attributes('-alpha', 0.0)
        self.root.deiconify()
        self.root.update()
        button.focus_force()
        button.event_generate('<KeyPress-space>')
        button.event_generate('<KeyRelease-space>')
        self.root.update()
        self.assertEqual(calls, [1, 1])

    def test_entry_retains_mask_selection_and_unicode_editing(self):
        value = tk.StringVar(value='test-only')
        entry = ttk.Entry(self.root, textvariable=value, show='●', style='Round.TEntry')
        entry.delete(0, 'end')
        entry.insert(0, '中文-test')
        entry.selection_range(0, 2)
        self.assertEqual(entry.get(), '中文-test')
        self.assertTrue(entry.selection_present())
        self.assertEqual(entry.cget('show'), '●')

    def test_checkbox_and_radio_keep_native_variable_semantics(self):
        selected = tk.BooleanVar(value=False)
        checkbox = ttk.Checkbutton(self.root, variable=selected, style='Round.TCheckbutton')
        checkbox.invoke()
        self.assertTrue(selected.get())
        self.assertTrue(checkbox.instate(['selected']))
        checkbox.configure(state='disabled')
        checkbox.invoke()
        self.assertTrue(selected.get())
        protocol = tk.StringVar(value='OpenAI')
        radio = ttk.Radiobutton(self.root, variable=protocol, value='Anthropic', style='Round.TRadiobutton')
        radio.invoke()
        self.assertEqual(protocol.get(), 'Anthropic')

    def test_combobox_arrow_is_inside_padded_round_border(self):
        combo = ttk.Combobox(self.root, style='Round.TCombobox', values=('保留已有', '覆盖已有'), state='readonly')
        combo.current(1)
        self.assertEqual(combo.get(), '覆盖已有')
        layout = self.theme.style.layout('Round.TCombobox')
        padding = layout[0][1]['children'][0]
        self.assertEqual(padding[0], 'Combobox.padding')
        self.assertIn('Combobox.downarrow', [item[0] for item in padding[1]['children']])

    def test_larger_tiles_do_not_enlarge_native_minimum_size(self):
        from diagnostics.rounded_ui_before import RoundedTheme as OriginalTheme
        reference_root = tk.Tk()
        reference_root.withdraw()
        try:
            ttk.Style(reference_root).theme_use('clam')
            original_theme = OriginalTheme(reference_root, App.COLORS)
            for widget_class, style in ((ttk.Button, 'Round.TButton'),
                    (ttk.Checkbutton, 'Round.TCheckbutton'), (ttk.Entry, 'Round.TEntry'),
                    (ttk.Combobox, 'Round.TCombobox'), (ttk.Frame, 'Card.TFrame')):
                current = widget_class(self.root, style=style)
                original = widget_class(reference_root, style=style)
                current.pack(); original.pack()
                self.root.update_idletasks(); reference_root.update_idletasks()
                self.assertEqual((current.winfo_reqwidth(), current.winfo_reqheight()),
                                 (original.winfo_reqwidth(), original.winfo_reqheight()))
        finally:
            reference_root.destroy()

    def test_images_and_dynamic_badge_styles_are_reused(self):
        image = self.theme.image('#24633A')
        self.assertIs(image, self.theme.image('#24633A'))
        self.assertTrue(image.transparency_get(0, 0))
        self.assertFalse(image.transparency_get(image.width() // 2, image.height() // 2))
        style = self.theme.badge_style('#EAF3EC', '#24633A')
        self.assertEqual(style, self.theme.badge_style('#EAF3EC', '#24633A'))
        self.assertGreaterEqual(self.theme.radius, 8)


if __name__ == '__main__':
    unittest.main()
