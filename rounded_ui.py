"""Antialiased, stretchable rounded skins for native ttk widgets (stdlib only).

Only the border element is painted here. Tk retains focus, keyboard activation,
entry editing, password masking, combobox popups and disabled/selected semantics.
"""
from __future__ import annotations

import base64
from functools import lru_cache
import struct
import tkinter as tk
from tkinter import ttk
import zlib


@lru_cache(maxsize=128)
def rounded_png(width, height, radius, fill, border=None, stroke=1):
    """Small RGBA nine-slice tile, with 4x coverage antialiasing at its edges."""
    radius = max(0, min(radius, width / 2, height / 2))
    fill_rgb = tuple(int(fill[i:i + 2], 16) for i in (1, 3, 5))
    edge_rgb = tuple(int((border or fill)[i:i + 2], 16) for i in (1, 3, 5))

    def inside(x, y, inset):
        r = max(0, radius - inset)
        if not (inset <= x <= width - inset and inset <= y <= height - inset):
            return False
        cx = min(max(x, inset + r), width - inset - r)
        cy = min(max(y, inset + r), height - inset - r)
        return (x - cx) ** 2 + (y - cy) ** 2 <= r * r

    rows = bytearray()
    for y in range(height):
        rows.append(0)  # PNG filter: none
        for x in range(width):
            inset = stroke if border else 0
            # Straight interior spans have full coverage; only edges need sampling.
            if (0 <= inset <= radius and (
                (radius <= x and x + 1 <= width - radius and inset <= y and y + 1 <= height - inset)
                or (radius <= y and y + 1 <= height - radius and inset <= x and x + 1 <= width - inset)
            )):
                rows.extend((*fill_rgb, 255))
                continue
            outer = inner = 0
            for sy in range(4):
                for sx in range(4):
                    px, py = x + (sx + .5) / 4, y + (sy + .5) / 4
                    if inside(px, py, 0):
                        outer += 1
                        inner += inside(px, py, stroke if border else 0)
            rgb = tuple(round((fill_rgb[i] * inner + edge_rgb[i] * (outer - inner)) / outer)
                        if outer else 0 for i in range(3))
            rows.extend((*rgb, round(255 * outer / 16)))

    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))

    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', width, height, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(bytes(rows))) + chunk(b'IEND', b''))


class RoundedTheme:
    def __init__(self, root, colors):
        self.root, self.colors = root, colors
        self.style = ttk.Style(root)
        self.images = {}  # Tk images must stay alive for the lifetime of the UI.
        self.badges = {}
        self.radius = max(8, round(8 * float(root.tk.call('tk', 'scaling')) / (96 / 72)))
        self.install()

    def image(self, fill, border=None, radius=None, size=None):
        radius = self.radius if radius is None else radius
        # Tk tiles (rather than stretches) the center. A 6px center causes
        # thousands of alpha blits on large cards; 64px keeps corners identical.
        size = size or radius * 2 + 64
        key = (fill, border, radius, size)
        if key not in self.images:
            self.images[key] = tk.PhotoImage(master=self.root, data=base64.b64encode(
                rounded_png(size, size, radius, fill, border)).decode('ascii'), format='png')
        return self.images[key]

    def element(self, name, fill, border, states=(), radius=None):
        radius = self.radius if radius is None else radius
        self.style.element_create(name, 'image', self.image(fill, border, radius),
                                  *[(state, self.image(bg, edge, radius)) for state, bg, edge in states],
                                  border=radius, sticky='nsew', width=radius * 2 + 6, height=radius * 2 + 6)

    def control(self, name, kind, fill, border, states, padding, **options):
        element = name + '.border'
        self.element(element, fill, border, states)
        self.style.layout(name, [(element, {'sticky': 'nsew', 'children': [
            (kind + '.padding', {'sticky': 'nsew', 'children': [
                (kind + '.label', {'sticky': 'nsew'})]})]})])
        self.style.configure(name, padding=padding, borderwidth=0, anchor='center', justify='center', **options)

    def install(self):
        c, style = self.colors, self.style
        focus = c['accent']
        for name, fill, hover, fg in (
            ('Round.TButton', c['button'], '#E8EDEA', c['text']),
            ('Primary.Round.TButton', c['accent'], c['accent_hover'], '#FFFFFF'),
        ):
            self.control(name, 'Button', fill, fill, [
                ('disabled', '#EEF1EF', '#EEF1EF'), ('pressed', hover, focus),
                ('focus', fill, '#75AC85'), ('active', hover, hover)], (14, 4),
                background=c['surface'], foreground=fg, font=('Microsoft YaHei UI', 9, 'bold'))
            style.map(name, foreground=[('disabled', '#A2ABA6')])
        for name, kind, fill, selected in (
            ('Round.TCheckbutton', 'Checkbutton', c['button'], c['accent_soft']),
            ('Round.TRadiobutton', 'Radiobutton', c['button'], c['surface']),
        ):
            self.control(name, kind, fill, c['border'], [
                ('disabled', '#F3F5F5', c['border']), ('selected focus', selected, focus),
                ('selected', selected, '#B1CFB9'), ('focus', fill, focus),
                ('active', c['accent_soft'], '#B1CFB9')], (10, 4),
                background=c['surface'], foreground=c['text'], font=('Microsoft YaHei UI', 9))
            style.map(name, foreground=[('disabled', '#A2ABA6'), ('selected', focus)])
        for name, kind in (('Round.TEntry', 'Entry'), ('Round.TCombobox', 'Combobox')):
            element = name + '.field'
            self.element(element, c['surface'], c['border'], [
                ('disabled', '#F3F5F5', c['border']), ('focus', c['surface'], focus)])
            children = []
            if kind == 'Combobox':
                children.append(('Combobox.downarrow', {'side': 'right', 'sticky': 'ns'}))
            children.append((kind + '.textarea', {'sticky': 'nsew'}))
            # Inset the arrow too: its native background must not cover our corners.
            style.layout(name, [(element, {'sticky': 'nsew', 'children': [
                (kind + '.padding', {'sticky': 'nsew', 'children': children})]})])
            style.configure(name, padding=(10, 3 if kind == 'Entry' else 4),
                            background=c['surface'], fieldbackground=c['surface'], foreground=c['text'],
                            borderwidth=0, arrowsize=13)
            style.map(name, foreground=[('disabled', '#A2ABA6')],
                      fieldbackground=[('disabled', '#F3F5F5'), ('readonly', c['surface'])])
        for name, fill, outer, radius in (
            ('Card.TFrame', c['surface'], c['bg'], self.radius + 4),
            ('Segment.TFrame', c['button'], c['surface'], self.radius),
        ):
            self.element(name + '.border', fill, c['border'], radius=radius)
            style.layout(name, [(name + '.border', {'sticky': 'nsew'})])
            style.configure(name, background=outer)

    def badge_style(self, fill, foreground, border=None):
        key = fill, foreground, border
        if key not in self.badges:
            name = f'Badge{len(self.badges)}.Round.TLabel'
            self.element(name + '.border', fill, border)
            self.style.layout(name, [(name + '.border', {'sticky': 'nsew', 'children': [
                ('Label.padding', {'sticky': 'nsew', 'children': [('Label.label', {'sticky': 'nsew'})]})]})])
            self.style.configure(name, padding=(10, 3), anchor='center', justify='center', background=self.colors['surface'], foreground=foreground)
            self.badges[key] = name
        return self.badges[key]
