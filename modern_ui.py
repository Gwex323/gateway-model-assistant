"""HTML-reference inspired native desktop UI; real adapters remain unchanged."""
from __future__ import annotations

from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import font as tkfont, messagebox, simpledialog, ttk

import core
from desktop_runtime import asset_path, configure_desktop
from rounded_ui import RoundedTheme
from viewport_repaint import finish_viewport_paint
import trae_adapter
import trae_solo_adapter


class App:
    COLORS = {
        'bg': '#F7F9FA', 'surface': '#FFFFFF', 'border': '#E2E7E9',
        'text': '#202B32', 'muted': '#707C83', 'accent': '#24633A',
        'accent_soft': '#EAF3EC', 'success': '#368447', 'danger': '#AE352B',
        'button': '#F3F5F5', 'accent_hover': '#1C4F2E',
        'warn': '#956521', 'warn_soft': '#FAF2E4', 'danger_soft': '#FBEDEB',
    }

    def __init__(self, root):
        self.root = root
        self.path = Path.home() / '.workbuddy/models.json'
        self.trae_targets = trae_adapter.discover_targets()
        self.ids = []
        self.model_token_limits = {}
        self.model_limit_sources = {}
        self.model_specs = {}
        self.model_image_support = {}
        self.model_display_names = {}
        self._model_column_widths = {}
        self.selected = set()
        self.events = queue.Queue()
        self.busy = False
        self.closed = False
        self.loaded_connection = None
        self.private_http_consent = None
        self.protocol = tk.StringVar(value='OpenAI')
        self.auth = tk.StringVar(value='自动')
        self.protocol_help = tk.StringVar(value='手动填写 OpenAI 兼容网关')
        self.active_protocol = 'openai'
        self.openai_target_selection = None
        self.target_vars = {}
        self.target_buttons = {}
        self.target_exists = {}
        self._sidebar_repaint_id = None
        self._sidebar_last_view = None
        self._configure_window()
        self._configure_styles()
        self._build_ui()
        self.poll_id = root.after(100, self.poll)

    def _configure_window(self):
        configure_desktop(self.root)
        self.root.title('Gateway Model Assistant')
        self.root.geometry('1280x860')
        self.root.minsize(940, 700)
        self.root.configure(bg=self.COLORS['bg'])
        self.root.protocol('WM_DELETE_WINDOW', self.close)

    def _configure_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei UI', 10))
        style.configure('Treeview', background=self.COLORS['surface'], fieldbackground=self.COLORS['surface'],
                        foreground=self.COLORS['text'], borderwidth=0, rowheight=34)
        style.configure('Treeview.Heading', background='#F8FAF9', foreground=self.COLORS['muted'],
                        borderwidth=0, font=('Microsoft YaHei UI', 9))
        style.map('Treeview', background=[('selected', self.COLORS['accent_soft'])],
                  foreground=[('selected', self.COLORS['text'])])
        style.configure('TCombobox', padding=6, arrowsize=13)
        style.configure('TScrollbar', borderwidth=0, arrowsize=12,
                        background='#D9E1DC', troughcolor=self.COLORS['surface'])
        style.configure('TCheckbutton', background=self.COLORS['surface'], foreground=self.COLORS['text'])
        style.map('TCheckbutton', background=[('active', self.COLORS['surface'])])
        self.rounded = RoundedTheme(self.root, self.COLORS)
        style.configure('Compact.Round.TButton', padding=(6, 4), width=4)
        self.rounded.control('Target.Round.TCheckbutton', 'Checkbutton',
                             self.COLORS['button'], self.COLORS['border'], [
                                 ('disabled', '#F3F5F5', self.COLORS['border']),
                                 ('selected', '#DCEFE1', self.COLORS['accent']),
                                 ('focus', self.COLORS['button'], self.COLORS['accent']),
                                 ('active', '#E8EDEA', self.COLORS['border'])], (6, 4),
                             background=self.COLORS['surface'], foreground=self.COLORS['text'],
                             font=('Microsoft YaHei UI', 9))
        style.configure('Protocol.Round.TRadiobutton', padding=(4, 4), font=('Microsoft YaHei UI', 8), width=0)
        style.map('Target.Round.TCheckbutton', foreground=[
            ('disabled', '#A2ABA6'), ('selected', self.COLORS['accent'])])

    def _panel(self, parent, **grid):
        shell = ttk.Frame(parent, style='Card.TFrame', padding=8)
        shell.grid(**grid)
        panel = tk.Frame(shell, bg=self.COLORS['surface'], bd=0)
        panel.pack(fill='both', expand=True)
        return panel

    def _button(self, parent, text, command, primary=False, width=None):
        button = ttk.Button(parent, text=text, command=command, cursor='hand2',
                            style='Primary.Round.TButton' if primary else 'Round.TButton')
        if width:
            button.configure(width=width)
        return button

    def _label(self, parent, text, muted=False, **kwargs):
        kwargs.setdefault('anchor', 'center')
        kwargs.setdefault('justify', 'center')
        return tk.Label(parent, text=text, bg=parent.cget('bg'),
                        fg=self.COLORS['muted'] if muted else self.COLORS['text'], **kwargs)

    def _entry(self, parent, variable, show=None):
        return ttk.Entry(parent, textvariable=variable, show=show, style='Round.TEntry',
                         font=('Microsoft YaHei UI', 10))

    def _build_ui(self):
        self.status = tk.StringVar(value='等待连接网关')
        self.status_badge_text = tk.StringVar(value='●  等待连接网关')
        self._status_error = None
        self.workspace = tk.Frame(self.root, bg=self.COLORS['bg'])
        self.workspace.grid(row=0, column=0, sticky='nsew')
        self.workspace.columnconfigure(1, weight=1)
        self.workspace.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        self.sidebar_shell = ttk.Frame(self.workspace, style='Card.TFrame', width=336, padding=8)
        self.sidebar_shell.grid(row=0, column=0, sticky='ns', padx=(12, 0), pady=10)
        self.sidebar_shell.grid_propagate(False)
        self.sidebar_shell.rowconfigure(0, weight=1)
        self.sidebar_shell.columnconfigure(0, weight=1)
        self.sidebar_canvas = tk.Canvas(self.sidebar_shell, width=318, bg=self.COLORS['surface'],
                                        highlightthickness=0, bd=0)
        self.sidebar_canvas.grid(row=0, column=0, sticky='nsew')
        self.sidebar_scroll = ttk.Scrollbar(self.sidebar_shell, orient='vertical', command=self.sidebar_canvas.yview)
        # Keep the scroll controller but never map its visual thumb/gutter.
        # Mouse wheel, keyboard reveal and viewport repaint remain unchanged.
        self.sidebar_canvas.configure(yscrollcommand=self._sidebar_view_changed)
        self.sidebar = tk.Frame(self.sidebar_canvas, bg=self.COLORS['surface'])
        self.sidebar_window = self.sidebar_canvas.create_window(0, 0, window=self.sidebar, anchor='nw')
        self.sidebar_canvas.bind('<Configure>', self._resize_sidebar)
        self.sidebar.bind('<Configure>', lambda _e: self.sidebar_canvas.configure(
            scrollregion=self.sidebar_canvas.bbox('all')))
        self._build_targets(self._side_group(1, '导入目标'))
        self._build_connection(self._side_group(2, '网关连接'))
        self._build_settings(self._side_group(3, '模型能力'))
        self._bind_sidebar_navigation(self.sidebar)
        self.sidebar_canvas.bind('<MouseWheel>', self._scroll_sidebar)

        self.main = tk.Frame(self.workspace, bg=self.COLORS['bg'])
        self.main.grid(row=0, column=1, sticky='nsew', padx=20, pady=10)
        self.main.columnconfigure(0, weight=1)
        self.main.rowconfigure(0, weight=1)
        self.models_panel = self._panel(self.main, row=0, column=0, sticky='nsew')
        self._build_models(self.models_panel)
        self._build_actions(self.models_panel)
        self.status.trace_add('write', self._sync_status)
        self._sync_status()
        self.refresh()

    def _side_group(self, number, title):
        if number > 1:
            tk.Frame(self.sidebar, bg=self.COLORS['border'], height=1).pack(fill='x', padx=20)
        group = tk.Frame(self.sidebar, bg=self.COLORS['surface'])
        group.pack(fill='x', padx=20, pady=8)
        head = tk.Frame(group, bg=self.COLORS['surface'])
        head.pack(fill='x', pady=(0, 4))
        ttk.Label(head, text=str(number), style=self.rounded.badge_style(self.COLORS['text'], '#FFFFFF'),
                  font=('Consolas', 9, 'bold'), padding=(6, 2), anchor='center').pack(side='left', padx=(0, 8))
        self._label(head, title, font=('Microsoft YaHei UI', 10, 'bold')).pack(side='left')
        if number == 1:
            self.target_summary = tk.StringVar()
            self._label(head, '', textvariable=self.target_summary, muted=True,
                        font=('Microsoft YaHei UI', 9)).pack(side='right')
        return group

    def _resize_sidebar(self, event):
        self.sidebar_canvas.itemconfigure(self.sidebar_window, width=event.width)

    def _sidebar_view_changed(self, first, last):
        self.sidebar_scroll.set(first, last)
        view = (float(first), float(last))
        if self.closed or view == self._sidebar_last_view:
            return
        self._sidebar_last_view = view
        if self._sidebar_repaint_id is None:
            self._sidebar_repaint_id = self.root.after_idle(self._finish_sidebar_paint)

    def _finish_sidebar_paint(self):
        self._sidebar_repaint_id = None
        if not self.closed:
            finish_viewport_paint(self.sidebar_canvas)

    def _scroll_sidebar(self, event):
        if self.sidebar.winfo_height() > self.sidebar_canvas.winfo_height():
            self.sidebar_canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units')
        return 'break'

    def _bind_sidebar_navigation(self, widget):
        # Local bindings: scrolling the table or a dialog never moves the sidebar.
        widget.bind('<MouseWheel>', self._scroll_sidebar)
        # FocusIn is also delivered to ancestor containers (NotifyVirtual).
        # Revealing the entire sidebar on those notifications jumps to the top.
        if isinstance(widget, (ttk.Entry, ttk.Button, ttk.Checkbutton, ttk.Radiobutton,
                               tk.Entry, tk.Button, tk.Checkbutton, tk.Radiobutton)):
            widget.bind('<FocusIn>', self._on_sidebar_control_focus)
        for child in widget.winfo_children():
            self._bind_sidebar_navigation(child)

    def _on_sidebar_control_focus(self, event):
        # Combobox popdowns are separate native windows. Their focus transitions
        # must not move the control that anchors an already-open menu.
        if str(self.root.tk.call('focus')) == str(event.widget):
            self._reveal_sidebar_control(event)

    def _reveal_sidebar_control(self, event):
        # Geometry is already available here; do not flush intermediate paints
        # from inside a focus callback while a combobox menu is being posted.
        height = self.sidebar.winfo_height()
        top = event.widget.winfo_rooty() - self.sidebar.winfo_rooty()
        view_top = self.sidebar_canvas.canvasy(0)
        view_height = self.sidebar_canvas.winfo_height()
        bottom = top + event.widget.winfo_height()
        if top < view_top:
            self.sidebar_canvas.yview_moveto(max(0, top - 12) / max(1, height))
        elif bottom > view_top + view_height:
            self.sidebar_canvas.yview_moveto((bottom - view_height + 12) / max(1, height))

    def _build_targets(self, panel):
        apps = tk.Frame(panel, bg=self.COLORS['surface'])
        apps.pack(fill='x')
        apps.columnconfigure((0, 1), weight=1, uniform='targets')
        self.target_icons = {
            'Trae': tk.PhotoImage(master=self.root, file=str(asset_path('target-trae.png'))),
            trae_solo_adapter.SOLO_NAME: tk.PhotoImage(master=self.root, file=str(asset_path('target-traework.png'))),
            'WorkBuddy': tk.PhotoImage(master=self.root, file=str(asset_path('target-workbuddy.png'))),
        }
        for index, target in enumerate(self.trae_targets):
            self._target_toggle(apps, target.name, target.exists, False, 0, index)
        wb_exists = self.path.parent.is_dir()
        self._target_toggle(apps, 'WorkBuddy', wb_exists, wb_exists, 1, 0, columnspan=2)
        self.update_target_summary()

    def _target_toggle(self, parent, name, exists, selected, row, column, columnspan=1):
        var = tk.BooleanVar(value=selected)
        self.target_vars[name] = var
        self.target_exists[name] = bool(exists)
        button = ttk.Checkbutton(parent, variable=var, command=self.update_target_summary,
                                 style='Target.Round.TCheckbutton', cursor='hand2',
                                 image=self.target_icons[name], compound='left')
        if not exists:
            button.configure(state='disabled')
        button.grid(row=row, column=column, columnspan=columnspan, sticky='nsew',
                    padx=(0, 6) if columnspan == 1 and column == 0 else 0, pady=(0, 4))
        self.target_buttons[name] = button

    def _field(self, parent, label, **grid):
        field = tk.Frame(parent, bg=self.COLORS['surface'])
        if grid:
            field.grid(**grid)
        else:
            field.pack(fill='x', pady=(0, 4))
        self._label(field, label, muted=True, font=('Microsoft YaHei UI', 9)).pack(fill='x', pady=(0, 3))
        return field

    def _build_connection(self, panel):
        self.connection_options = tk.Frame(panel, bg=self.COLORS['surface'])
        self.connection_options.pack(fill='x', pady=(0, 4))
        self.connection_options.columnconfigure(0, weight=3)
        self.connection_options.columnconfigure(1, weight=2)
        self.protocol_field = self._field(self.connection_options, '接口协议', row=0, column=0, sticky='nsew', padx=(0, 6))
        field = self.protocol_field
        segment = ttk.Frame(field, style='Segment.TFrame', padding=2)
        segment.pack(fill='x')
        self.protocol_buttons = {}
        for value in ('OpenAI', 'Anthropic'):
            button = ttk.Radiobutton(segment, text=value, value=value, variable=self.protocol,
                                     command=self.protocol_changed, style='Protocol.Round.TRadiobutton', cursor='hand2')
            button.pack(side='left', fill='x', expand=True)
            self.protocol_buttons[value] = button
        self.auth_field = self._field(self.connection_options, '鉴权方式', row=0, column=1, sticky='nsew')
        field = self.auth_field
        self.auth_combo = ttk.Combobox(field, style='Round.TCombobox', textvariable=self.auth, state='disabled',
                                       values=('自动', 'Bearer', 'x-api-key'), width=7, font=('Microsoft YaHei UI', 8), justify='center')
        self.auth_combo.pack(fill='x')
        self.auth_combo.bind('<<ComboboxSelected>>', self.auth_changed)
        self.url = tk.StringVar()
        self.key = tk.StringVar()
        self.url_entry = self._entry(self._field(panel, 'Base URL'), self.url)
        self.url_entry.configure(font=('Consolas', 10))
        self.url_entry.pack(fill='x', ipady=3)
        field = self._field(panel, 'API Key')
        key_row = tk.Frame(field, bg=self.COLORS['surface'])
        key_row.pack(fill='x')
        self.key_entry = self._entry(key_row, self.key, show='●')
        self.key_entry.configure(font=('Consolas', 10), width=12)
        self.key_entry.pack(side='left', fill='x', expand=True, ipady=3)
        self.key_toggle = self._button(key_row, '显示', self.toggle_key_visibility)
        self.key_toggle.configure(style='Compact.Round.TButton')
        self.key_toggle.pack(side='right', padx=(6, 0))
        self.fetch_button = self._button(panel, '获取模型', self.fetch, primary=True)
        self.fetch_button.pack(fill='x')
        self._label(panel, '', textvariable=self.protocol_help, muted=True,
                    font=('Microsoft YaHei UI', 9), wraplength=265, justify='center').pack(fill='x', pady=(4, 0))
        self._label(panel, '仅读取模型列表与规格，不调用聊天或付费生成接口。', muted=True,
                    font=('Microsoft YaHei UI', 9), wraplength=265, justify='center').pack(fill='x', pady=(3, 0))

    def toggle_key_visibility(self):
        visible = bool(self.key_entry.cget('show'))
        self.key_entry.configure(show='' if visible else '●')
        self.key_toggle.configure(text='隐藏' if visible else '显示')

    def current_protocol(self):
        return 'anthropic' if self.protocol.get() == 'Anthropic' else 'openai'

    def current_auth(self):
        return {'自动': 'auto', 'Bearer': 'bearer', 'x-api-key': 'x-api-key'}[self.auth.get()]

    def auth_changed(self, _event=None):
        self.loaded_connection = None
        self.model_image_support = {}
        self.ids = []
        self.model_token_limits = {}
        self.selected.clear()
        self.refresh()
        self.status.set('鉴权已切换，请重新获取模型列表')

    def protocol_changed(self, _event=None):
        protocol = self.current_protocol()
        if protocol == self.active_protocol:
            return
        if self.active_protocol == 'openai':
            self.openai_target_selection = {
                name: var.get() for name, var in self.target_vars.items()
            }
        if protocol == 'anthropic':
            for name, var in self.target_vars.items():
                is_solo = name == trae_solo_adapter.SOLO_NAME
                available = self.target_exists.get(name, False)
                var.set(bool(is_solo and available))
                self.target_buttons[name].configure(
                    state='normal' if is_solo and available else 'disabled'
                )
            self.protocol_help.set('原生 Anthropic Messages；仅导入 TraeWork CN')
        else:
            saved = self.openai_target_selection or {}
            for name, var in self.target_vars.items():
                available = self.target_exists.get(name, False)
                var.set(bool(available and saved.get(name, var.get())))
                self.target_buttons[name].configure(
                    state='normal' if available else 'disabled'
                )
            self.protocol_help.set('手动填写 OpenAI 兼容网关')
        self.active_protocol = protocol
        self.auth.set('自动')
        self.auth_combo.configure(state='readonly' if protocol == 'anthropic' else 'disabled')
        self.loaded_connection = None
        self.model_image_support = {}
        self.ids = []
        self.model_token_limits = {}
        self.selected.clear()
        self.refresh()
        self.update_target_summary()
        self.status.set('协议已切换，请重新获取模型列表')

    def _build_models(self, panel):
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(2, weight=1)
        head = tk.Frame(panel, bg=self.COLORS['surface'])
        head.grid(row=0, column=0, sticky='ew', padx=18, pady=(8, 6))
        self._label(head, '模型列表', font=('Microsoft YaHei UI', 11, 'bold')).pack(side='left')
        self.selected_count = tk.StringVar(value='已选择 0 · 共 0 个')
        self._label(head, '', textvariable=self.selected_count, muted=True,
                    font=('Microsoft YaHei UI', 9)).pack(side='left', padx=12)
        tools = tk.Frame(panel, bg=self.COLORS['surface'])
        tools.grid(row=1, column=0, sticky='ew', padx=18, pady=(0, 4))
        tools.columnconfigure(0, weight=1)
        search_row = tk.Frame(tools, bg=self.COLORS['surface'])
        search_row.grid(row=0, column=0, sticky='ew', pady=(0, 4))
        self._label(search_row, '搜索模型 ID', muted=True, font=('Microsoft YaHei UI', 9)).pack(side='left', padx=(0, 10))
        self.search = tk.StringVar()
        self.search.trace_add('write', lambda *_: self.refresh())
        self.search_entry = self._entry(search_row, self.search)
        self.search_entry.pack(side='left', fill='x', expand=True, ipady=3)
        self.select_all_button = self._button(search_row, '全选', lambda: self.select_visible(True))
        self.select_all_button.pack(side='right', padx=(8, 0))

        listing = tk.Frame(panel, bg=self.COLORS['surface'])
        listing.grid(row=2, column=0, sticky='nsew', padx=1)
        listing.columnconfigure(0, weight=1)
        listing.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            listing, columns=('check', 'id', 'max_input', 'max_output', 'hint', 'display_name'),
            displaycolumns=('check', 'id', 'display_name', 'max_input', 'max_output'),
            show='headings', selectmode='browse',
        )
        for name, label in [
            ('check', ''), ('id', '模型 ID'), ('max_input', '最大输入'),
            ('max_output', '最大输出'), ('hint', '规格来源 / 类型'), ('display_name', '显示名称'),
        ]:
            self.tree.heading(name, text=label)
            self.tree.column(name, stretch=False, anchor='center')
        self.tree.tag_configure('checked', background=self.COLORS['accent_soft'])
        self.tree.tag_configure('alternate', background='#FAFCFA')
        self.tree.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(listing, orient='vertical', command=self.tree.yview)
        # Keep native scrolling callbacks without displaying bars or gutters.
        self.tree.configure(yscrollcommand=scroll.set)
        horizontal = ttk.Scrollbar(listing, orient='horizontal', command=self.tree.xview)
        self.tree.configure(xscrollcommand=horizontal.set)
        self.empty_panel = tk.Frame(listing, bg=self.COLORS['surface'])
        self._label(self.empty_panel, '▤', muted=True, font=('Segoe UI', 30)).pack(pady=(0, 4))
        self.empty_title = self._label(self.empty_panel, '', font=('Microsoft YaHei UI', 11, 'bold'))
        self.empty_title.pack()
        self.empty_description = self._label(self.empty_panel, '', muted=True, wraplength=350,
                                             font=('Microsoft YaHei UI', 9), justify='center')
        self.empty_description.pack(pady=(4, 0))
        self._column_drag = None
        self.tree.bind('<ButtonPress-1>', self._start_column_drag)
        self.tree.bind('<B1-Motion>', self._resize_column_drag)
        self.tree.bind('<ButtonRelease-1>', self._finish_column_drag)
        self.tree.bind('<space>', self.toggle_keyboard)
        self.tree.bind('<Button-3>', self.show_model_spec)
        self.tree.bind('<Double-1>', self.edit_display_name_event)
        self.tree.bind('<Configure>', lambda event: self._fit_model_columns(event.width))
        self._autosize_model_columns()

    def _start_column_drag(self, event):
        self._column_drag = None
        if self.tree.identify_region(event.x, event.y) != 'separator':
            return
        columns = tuple(self.tree['displaycolumns'])
        column = self.tree.identify_column(event.x)
        if not column.startswith('#') or not column[1:].isdigit():
            return 'break'
        index = int(column[1:]) - 1
        # A handled but locked separator still consumes motion and release.
        self._column_drag = ()
        if 0 <= index < columns.index('max_input'):
            movable = columns[:columns.index('max_input') + 1]
            self._column_drag = (event.x, index, movable,
                                 tuple(self.tree.column(c, 'width') for c in movable))
        return 'break'

    def _resize_column_drag(self, event):
        if self._column_drag is None:
            return
        if self._column_drag:
            start, index, columns, original = self._column_drag
            widths = list(original)
            minima = [self.tree.column(c, 'minwidth') for c in columns]
            requested = event.x - start
            # Borrow from further columns when the immediate neighbor is already
            # at its minimum. In particular, the input's left edge must not get
            # stuck just because display_name starts at its heading width.
            donors = (range(index + 1, len(columns)) if requested >= 0
                      else range(index, -1, -1))
            donors = tuple(donors)
            remaining = min(abs(requested), sum(max(0, widths[i] - minima[i]) for i in donors))
            widths[index if requested >= 0 else index + 1] += remaining
            for i in donors:
                taken = min(remaining, max(0, widths[i] - minima[i]))
                widths[i] -= taken
                remaining -= taken
            # The total through max_input never changes; max_output is untouched.
            for column, width in zip(columns, widths):
                self.tree.column(column, width=width)
        return 'break'

    def _finish_column_drag(self, event):
        if self._column_drag is not None:
            self._column_drag = None
            return 'break'
        self.toggle(event)

    def _build_settings(self, panel):
        self.flags = {}
        self.capability_combos = []
        self.capability_grid = tk.Frame(panel, bg=self.COLORS['surface'])
        self.capability_grid.pack(fill='x')
        self.capability_grid.columnconfigure((0, 1), weight=1, uniform='capabilities')
        ttk.Style(self.root).configure('Capability.Round.TCombobox', padding=(6, 4))
        choices = ['保留 / 未声明', '开启（已确认支持）', '关闭']
        for index, (key, label) in enumerate([
                ('supportsToolCall', '工具调用'), ('supportsImages', '图片输入'),
                ('supportsReasoning', '推理标记')]):
            field_choices = (['自动识别', *choices[1:]] if key == 'supportsImages'
                             else ['自动（WB 开启）', *choices[1:]] if key == 'supportsToolCall'
                             else choices)
            var = tk.StringVar(value=field_choices[0])
            self.flags[key] = var
            field = self._field(self.capability_grid, label, row=index // 2, column=index % 2,
                                sticky='nsew', padx=(0, 6) if index % 2 == 0 else 0, pady=(0, 4))
            combo = ttk.Combobox(field, style='Capability.Round.TCombobox', textvariable=var,
                                 state='readonly', values=field_choices, justify='center', width=9,
                                 font=('Microsoft YaHei UI', 8))
            combo.pack(fill='x')
            self.capability_combos.append(combo)
        self.existing_policy = tk.StringVar(value='保留已有')
        field = self._field(self.capability_grid, '同 URL 已有模型', row=1, column=1,
                            sticky='nsew', pady=(0, 4))
        self.policy_combo = ttk.Combobox(field, style='Capability.Round.TCombobox',
                                         textvariable=self.existing_policy, state='readonly',
                                         values=('保留已有', '覆盖已有'), justify='center', width=9,
                                         font=('Microsoft YaHei UI', 8))
        self.policy_combo.pack(fill='x')

    def _build_actions(self, parent):
        # Actions belong to the model panel, not a separate window-wide footer.
        self.actions = tk.Frame(parent, bg=self.COLORS['surface'])
        self.actions.grid(row=3, column=0, sticky='ew', padx=18, pady=(6, 8))
        self.actions.columnconfigure(0, weight=1, uniform='model_footer')
        self.actions.columnconfigure(1, weight=3, uniform='model_footer')
        self.footer_status = self._label(self.actions, '', textvariable=self.status, muted=True,
                                         font=('Microsoft YaHei UI', 9), justify='left', anchor='w', width=1, height=3)
        self.footer_status.grid(row=0, column=0, sticky='ew', padx=(0, 12))
        self.footer_status.bind('<Configure>', lambda e: self.footer_status.configure(wraplength=max(1, e.width)))
        self.table_hint = self._label(self.actions, 'Token 上限自动识别 · 右键查看规格与导入预算\n双击显示名称可编辑（仅 Trae 系列，WorkBuddy 不应用）',
                                      muted=True, font=('Microsoft YaHei UI', 9), justify='center', anchor='center', width=1)
        self.table_hint.grid(row=0, column=1, sticky='ew', padx=(0, 12))
        self.table_hint.bind('<Configure>', lambda e: self.table_hint.configure(wraplength=max(1, e.width)))
        self.import_button = self._button(self.actions, '预览并导入  →', self.preview, primary=True)
        self.import_button.grid(row=0, column=2, sticky='e')

    def _sync_status(self, *_):
        message = self.status.get()
        if self.busy:
            label, fg, bg = '正在处理', self.COLORS['warn'], self.COLORS['warn_soft']
        elif self._status_error == message:
            label, fg, bg = '操作未完成', self.COLORS['danger'], self.COLORS['danger_soft']
        elif message.startswith(('已读取', '导入完成', '配置无需变更')):
            label, fg, bg = '操作完成', self.COLORS['accent'], self.COLORS['accent_soft']
        else:
            label, fg, bg = '等待连接网关', self.COLORS['muted'], self.COLORS['button']
        self.status_badge_text.set('●  ' + label)
        self.footer_status.configure(fg=fg)

    def update_target_summary(self):
        selected = self.selected_import_targets()
        self.target_summary.set(('已选 ' + str(len(selected))) if selected else '未选择')
        for name, button in self.target_buttons.items():
            available = self.target_exists[name]
            button.configure(text=name + '\n' + ('已发现' if available else '未发现'))

    def selected_import_targets(self):
        return [name for name, var in self.target_vars.items() if var.get()]

    def idle(self):
        if self.busy:
            messagebox.showinfo('请稍候', '当前操作尚未完成。', parent=self.root)
            return False
        return True

    def close(self):
        if self.busy:
            messagebox.showinfo('请稍候', '请等待当前操作结束后关闭。', parent=self.root)
            return
        self.closed = True
        if self._sidebar_repaint_id is not None:
            self.root.after_cancel(self._sidebar_repaint_id)
            self._sidebar_repaint_id = None
        if getattr(self, 'poll_id', None):
            self.root.after_cancel(self.poll_id)
        self.key.set('')
        self.root.destroy()

    def report(self, error):
        msg = str(error) if isinstance(error, core.UserError) else '操作失败；请检查文件权限或使用说明。'
        self._status_error = msg
        self.status.set(msg)
        messagebox.showerror('未完成', msg, parent=self.root)

    def run(self, work, done, message):
        if not self.idle():
            return
        self.busy = True
        self._status_error = None
        self.status.set(message)
        self.fetch_button.configure(state='disabled')
        self.import_button.configure(state='disabled')
        self.select_all_button.configure(state='disabled')
        for button in self.protocol_buttons.values():
            button.configure(state='disabled')
        self.auth_combo.configure(state='disabled')
        def worker():
            try:
                self.events.put((done, work(), None))
            except Exception as error:
                self.events.put((done, None, error))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            done, result, error = self.events.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            self.fetch_button.configure(state='normal')
            self.import_button.configure(state='normal')
            self.select_all_button.configure(state='normal')
            for button in self.protocol_buttons.values():
                button.configure(state='normal')
            self.auth_combo.configure(state='readonly' if self.current_protocol() == 'anthropic' else 'disabled')
            if error:
                self.report(error)
            else:
                try:
                    done(result)
                except Exception as callback_error:
                    self.report(callback_error)
            self._sync_status()
        if not self.closed:
            self.poll_id = self.root.after(100, self.poll)

    def fetch(self):
        if not self.idle():
            return
        try:
            detected = core.resolve_connection(self.url.get(), 'anthropic', allow_private_http=True)
            if detected.provider_name == 'DeepSeek' and self.current_protocol() != 'anthropic':
                self.protocol.set('Anthropic')
                self.protocol_changed()
            protocol = self.current_protocol()
            auth = self.current_auth()
            url, key = self._connection_url(prompt_private=True), core.check_key(self.key.get())
        except Exception as error:
            self.report(error)
            return
        self.url.set(url)
        def done(catalog):
            if (protocol, auth, url, key) != (self.current_protocol(), self.current_auth(), self.url.get(), self.key.get().strip()):
                self.loaded_connection = None
                self.status.set('连接已改变，请重新获取模型列表')
                return
            self.ids = [model.id for model in catalog]
            same_connection = self.loaded_connection == (protocol, auth, url, key)
            self.model_display_names = {mid: name for mid, name in self.model_display_names.items()
                                        if same_connection and mid in self.ids}
            self.model_token_limits = {model.id: model.token_limits for model in catalog}
            self.model_image_support = {model.id: model.supports_images for model in catalog}
            self.model_limit_sources = {model.id: model.limit_source or ('接口元数据' if model.token_limits else '未识别') for model in catalog}
            self.model_specs = {model.id: {
                'max_input': core.API_ONLY_IMPORT_HINT if core.image_video_hint(model.id) else model.input_display or str(model.max_input_tokens or '未识别'),
                'max_output': core.API_ONLY_IMPORT_HINT if core.image_video_hint(model.id) else model.output_display or str(model.max_output_tokens or '未识别'),
                'source_url': model.limit_source_url, 'note': model.limit_note,
                'supports_images': model.supports_images,
            } for model in catalog}
            self.selected.clear()
            self.loaded_connection = (protocol, auth, url, key)
            self.refresh()
            text_models = [model for model in catalog if not core.media_hint(model.id)]
            recognized = sum(model.max_input_tokens is not None and model.max_output_tokens is not None for model in text_models)
            self.status.set(f'已读取 {len(catalog)} 个；完整规格 {recognized}/{len(text_models)}，媒体 {len(catalog)-len(text_models)}')
        fetcher = (core.fetch_anthropic_model_catalog
                   if protocol == 'anthropic' else core.fetch_model_catalog)
        options = {'auth': auth} if auth != 'auto' else {}
        self.run(lambda: fetcher(
            url, key, allow_private_http=core.is_private_http_url(url), **options
        ), done, '正在读取模型列表…')

    def _autosize_model_columns(self):
        """Measure visible cells with the actual Tk fonts (including CJK/DPI)."""
        style = ttk.Style(self.root)
        body = tkfont.Font(root=self.root, font=style.lookup('Treeview', 'font') or 'TkDefaultFont')
        heading = tkfont.Font(root=self.root, font=style.lookup('Treeview.Heading', 'font') or 'TkHeadingFont')
        columns = self.tree['displaycolumns']
        padding = 24
        widths = {column: heading.measure(self.tree.heading(column, 'text')) + padding for column in columns}
        widths['check'] = max(widths['check'], body.measure('✓') + padding)
        measured = {}
        for row in self.tree.get_children():
            for column in columns:
                text = str(self.tree.set(row, column))
                if text not in measured:
                    measured[text] = max((body.measure(line) for line in text.splitlines()), default=0)
                widths[column] = max(widths[column], measured[text] + padding)
        self._model_column_minwidths = {column: heading.measure(self.tree.heading(column, 'text')) + padding
                                        for column in columns}
        self._model_column_widths = widths
        self._fit_model_columns(self.tree.winfo_width())

    def _fit_model_columns(self, available_width):
        if not self._model_column_widths:
            return
        self._column_drag = None
        # Never squeeze content to fit. Overflow stays accessible by scrolling;
        # spare viewport space goes to the model-ID column.
        spare = max(0, int(available_width) - 4 - sum(self._model_column_widths.values()))
        for column, natural in self._model_column_widths.items():
            self.tree.column(column, minwidth=self._model_column_minwidths[column],
                             width=natural + (spare if column == 'id' else 0), stretch=False)

    def token_display_values(self, model_id):
        if core.image_video_hint(model_id):
            return (core.API_ONLY_IMPORT_HINT, core.API_ONLY_IMPORT_HINT)
        spec = self.model_specs.get(model_id, {})
        limits = self.model_token_limits.get(model_id, {})
        return (spec.get('max_input', str(limits.get('maxInputTokens', '未识别'))),
                spec.get('max_output', str(limits.get('maxOutputTokens', '未识别'))))

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        query = self.search.get().casefold()
        for index, model_id in enumerate(self.ids):
            media = core.media_hint(model_id)
            if query not in model_id.casefold():
                continue
            self.tree.insert('', 'end', iid=str(index), values=(
                '✓' if model_id in self.selected else '', model_id,
                *self.token_display_values(model_id),
                self.model_limit_sources.get(model_id, '媒体/嵌入，需核实' if media else '未验证'),
                self.model_display_names.get(model_id, '默认 / 保留原名')),
                tags=('checked',) if model_id in self.selected else ('alternate',) if index % 2 else ())
        visible = len(self.tree.get_children())
        self.selected_count.set(f'已选择 {len(self.selected)} · 共 {len(self.ids)} 个 · 可见 {visible}')
        if visible:
            self.empty_panel.place_forget()
        else:
            title = '没有匹配的模型' if self.ids else '尚未获取模型列表'
            description = '调整搜索词后再试。' if self.ids else '在左侧填写 Base URL 与 API Key，\n选择导入目标后点击「获取模型」。'
            self.empty_title.configure(text=title)
            self.empty_description.configure(text=description)
            self.empty_panel.place(relx=0.5, rely=0.48, anchor='center')
        self._autosize_model_columns()

    def show_model_spec(self, event):
        row = self.tree.identify_row(event.y)
        if not row:
            return
        model_id = self.ids[int(row)]
        spec = self.model_specs.get(model_id, {})
        limits = self.model_token_limits.get(model_id, {})
        input_text, output_text = self.token_display_values(model_id)
        text = (f'{model_id}\n\n官方/接口最大输入：{input_text}\n'
                f'官方/接口最大输出：{output_text}\n\n'
                f'实际导入输入预算：{limits.get("maxInputTokens", "不写入，保留原值")}\n'
                f'实际导入输出预算：{limits.get("maxOutputTokens", "不写入，保留原值")}\n\n'
                f'{self.model_limit_sources.get(model_id, "未识别")}\n'
                f'{spec.get("note", "")}\n\n{spec.get("source_url", "")}')
        messagebox.showinfo('模型规格与导入预算', text, parent=self.root)

    def edit_display_name(self, model_id):
        if not self.idle() or model_id not in self.ids:
            return
        if not any(name != 'WorkBuddy' for name in self.selected_import_targets()):
            self.report(core.UserError('显示名称仅用于 Trae 系列；请先选择 Trae 或 TraeWork CN。WorkBuddy 不支持此项。'))
            return
        value = simpledialog.askstring(
            '编辑 Trae 显示名称',
            f'模型 ID：{model_id}（不会改变）\n仅用于 Trae 系列。\n留空：新增使用模型 ID，覆盖时保留原显示名称。',
            initialvalue=self.model_display_names.get(model_id, ''), parent=self.root,
        )
        if value is None:
            return
        try:
            names = trae_adapter.validate_display_names({model_id: value}, [model_id])
        except core.UserError as error:
            self.report(error)
            return
        if model_id in names:
            self.model_display_names[model_id] = names[model_id]
        else:
            self.model_display_names.pop(model_id, None)
        self.refresh()

    def edit_display_name_event(self, event):
        row = self.tree.identify_row(event.y)
        if row and self.tree.identify_column(event.x) == '#3':
            self.edit_display_name(self.ids[int(row)])
        return 'break'

    def toggle_row(self, row):
        if self.busy or not row:
            return
        model_id = self.ids[int(row)]
        if model_id in self.selected:
            self.selected.remove(model_id)
        else:
            self.selected.add(model_id)
        self._update_selection_rows((row,))

    def toggle(self, event):
        if self.tree.identify_column(event.x) == '#3':
            return
        self.toggle_row(self.tree.identify_row(event.y))

    def toggle_keyboard(self, _event):
        self.toggle_row(self.tree.focus())
        return 'break'

    def select_visible(self, yes):
        if not self.idle():
            return
        rows = self.tree.get_children()
        for row in rows:
            model_id = self.ids[int(row)]
            if yes:
                self.selected.add(model_id)
            else:
                self.selected.discard(model_id)
        self._update_selection_rows(rows)

    def _update_selection_rows(self, rows):
        # Selection cannot change natural column widths; avoid deleting the
        # entire table, measuring every cell, and losing focus/scroll position.
        for row in rows:
            index = int(row)
            checked = self.ids[index] in self.selected
            self.tree.set(row, 'check', '✓' if checked else '')
            self.tree.item(row, tags=('checked',) if checked else ('alternate',) if index % 2 else ())
        self.selected_count.set(
            f'已选择 {len(self.selected)} · 共 {len(self.ids)} 个 · 可见 {len(self.tree.get_children())}')

    def connection(self):
        protocol = self.current_protocol()
        url, key = self._connection_url(prompt_private=False), core.check_key(self.key.get())
        if (protocol, self.current_auth(), url, key) != self.loaded_connection:
            raise core.UserError('连接发生变化，请重新获取模型列表。')
        return url, key

    def _connection_url(self, prompt_private):
        raw = self.url.get()
        normalize = (core.normalize_anthropic_url
                     if self.current_protocol() == 'anthropic' else core.normalize_url)
        try:
            url = normalize(raw)
            self.private_http_consent = None
            return url
        except core.UserError as strict_error:
            try:
                url = normalize(raw, allow_private_http=True)
            except core.UserError:
                raise strict_error
            if not core.is_private_http_url(url):
                raise strict_error
            if self.private_http_consent == url:
                return url
            if not prompt_private:
                raise core.UserError('私有网络 HTTP 连接尚未确认，请重新获取模型列表。')
            accepted = messagebox.askyesno(
                '确认明文 HTTP 风险',
                '此地址位于私有网络，但使用未加密 HTTP。API Key 和请求内容可能被同一网络中的设备读取。\n\n仍要使用此连接吗？',
                parent=self.root,
            )
            if not accepted:
                raise core.UserError('已取消使用明文 HTTP 连接。')
            self.private_http_consent = url
            return url

    def build_import_plans(self, url, key, selected, flags, token_limits_by_model=None):
        names = self.selected_import_targets()
        if not names:
            raise core.UserError('请至少选择一个导入应用。')
        protocol = self.current_protocol()
        if protocol == 'anthropic' and names != [trae_solo_adapter.SOLO_NAME]:
            raise core.UserError('Anthropic 原生协议仅支持导入 TraeWork CN。')
        allow_private_http = core.is_private_http_url(url) and self.private_http_consent == url
        overwrite_existing = self.existing_policy.get() == '覆盖已有'
        display_names = {mid: self.model_display_names[mid] for mid in selected if mid in self.model_display_names}
        image_capabilities = {mid: {'supportsImages': value} for mid in selected
                              if (value := self.model_image_support.get(mid)) is not None}
        plans = []
        if protocol == 'anthropic':
            target = next(
                (item for item in self.trae_targets
                 if item.name == trae_solo_adapter.SOLO_NAME and item.name in names),
                None,
            )
            if target is None or not target.exists:
                raise core.UserError('未发现 TraeWork CN，无法使用 Anthropic 原生导入。')
            plan = trae_solo_adapter.make_plan(
                target, url, key, selected, capabilities=flags, capabilities_by_model=image_capabilities,
                update_existing=overwrite_existing,
                allow_private_http=allow_private_http,
                token_limits_by_model=token_limits_by_model,
                protocol='anthropic',
                auth=self.current_auth(),
                display_names_by_model=display_names,
            )
            return [(target.name, 'trae_solo', target, plan)]
        if 'WorkBuddy' in names:
            plan = core.make_plan(
                core.read_snapshot(self.path), url, key, selected,
                capabilities={'supportsToolCall': True, **flags},
                capabilities_by_model=image_capabilities, update_existing=overwrite_existing,
                allow_private_http=allow_private_http,
                token_limits_by_model=token_limits_by_model,
            )
            plans.append(('WorkBuddy', 'workbuddy', self.path, plan))
        for target in self.trae_targets:
            if target.name in names:
                if target.name == trae_solo_adapter.SOLO_NAME:
                    plan = trae_solo_adapter.make_plan(
                        target, url, key, selected, capabilities=flags, capabilities_by_model=image_capabilities,
                        update_existing=overwrite_existing,
                        allow_private_http=allow_private_http,
                        token_limits_by_model=token_limits_by_model,
                        protocol='openai',
                        display_names_by_model=display_names,
                    )
                    plans.append((target.name, 'trae_solo', target, plan))
                else:
                    plan = trae_adapter.make_plan(
                        target, url, key, selected, capabilities=flags, capabilities_by_model=image_capabilities,
                        update_existing=overwrite_existing,
                        allow_private_http=allow_private_http,
                        token_limits_by_model=token_limits_by_model,
                        display_names_by_model=display_names,
                    )
                    plans.append((target.name, 'trae', target, plan))
        return plans

    def preview(self):
        if not self.idle():
            return
        try:
            url, key = self.connection()
            flags = {name: var.get() == '开启（已确认支持）' for name, var in self.flags.items()
                     if var.get() not in ('保留 / 未声明', '自动识别', '自动（WB 开启）')}
            selected = sorted(self.selected)
            token_limits_by_model = {
                model_id: self.model_token_limits.get(model_id, {}) for model_id in selected
            }
            plans = self.build_import_plans(
                url, key, selected, flags, token_limits_by_model,
            )
        except Exception as error:
            self.report(error)
            return
        dialog = tk.Toplevel(self.root)
        dialog.title('确认导入')
        dialog.geometry('850x600')
        dialog.configure(bg=self.COLORS['surface'])
        dialog.transient(self.root)
        dialog.grab_set()
        box = tk.Frame(dialog, bg=self.COLORS['surface'])
        box.pack(fill='both', expand=True, padx=22, pady=20)
        self._label(box, '确认导入', font=('Microsoft YaHei UI', 14, 'bold')).pack(anchor='w')
        summary = '   '.join(name + '：' + plan.summary() for name, _, _, plan in plans)
        self._label(box, summary, muted=True, wraplength=790, justify='left').pack(anchor='w', pady=(5, 14))
        warnings = [plan.compatibility_warning for _, kind, _, plan in plans
                    if kind == 'trae_solo' and getattr(plan, 'compatibility_warning', '')]
        for warning in dict.fromkeys(warnings):
            self._label(box, warning, wraplength=790, justify='left').pack(anchor='w', pady=(0, 8))
        table_box = tk.Frame(box, bg=self.COLORS['surface'])
        table_box.pack(fill='both', expand=True)
        table = ttk.Treeview(table_box, columns=('target', 'id', 'display_name', 'action'), show='headings')
        for column, label, width in [('target', '应用', 120), ('id', '模型 ID', 310), ('display_name', 'Trae 显示名称', 240), ('action', '操作', 120)]:
            table.heading(column, text=label)
            table.column(column, width=width, stretch=column == 'id')
        table.pack(side='left', fill='both', expand=True)
        scroll = ttk.Scrollbar(table_box, orient='vertical', command=table.yview)
        # Keep scrolling functional without displaying a scrollbar or gutter.
        table.configure(yscrollcommand=scroll.set)
        for name, kind, _, plan in plans:
            for model_id, action in plan.rows:
                label = ('不支持（不应用）' if kind == 'workbuddy' else
                         '保留原名' if action.startswith('保留') else
                         getattr(plan, 'display_names', {}).get(model_id, '默认 / 保留原名'))
                table.insert('', 'end', values=(name, model_id, label, action))
        self._label(box, '写入前请完全退出已选择的应用。TraeWork CN 会由工具临时启动并安全重启；本地配置会单独备份。',
                    muted=True).pack(anchor='w', pady=(12, 8))
        footer = tk.Frame(box, bg=self.COLORS['surface'])
        footer.pack(fill='x')
        self._button(footer, '取消', dialog.destroy).pack(side='right')
        changed = any(plan.changed for _, _, _, plan in plans)
        def commit():
            if warnings and not messagebox.askyesno(
                '调用兼容性尚未验证', '\n'.join(warnings) + '\n\n仍要导入配置吗？', parent=dialog,
            ):
                return
            dialog.destroy()
            def work():
                backups = []
                changed_apps = []
                for name, kind, target, plan in plans:
                    if kind == 'workbuddy':
                        backup = core.apply_plan(target, plan)
                        changed = bool(backup)
                    elif kind == 'trae_solo':
                        outcome = trae_solo_adapter.apply_plan(target, plan)
                        backup = None
                        changed = outcome.changed
                    else:
                        backup = trae_adapter.apply_plan(target, plan)
                        changed = bool(backup)
                    if changed:
                        changed_apps.append(name)
                    if backup:
                        backups.append((name, backup))
                return changed_apps, backups
            def done(result):
                changed_apps, backups = result
                self.status.set('导入完成' if changed_apps else '配置无需变更')
                details = '\n'.join(name + '：' + str(path) for name, path in backups)
                if changed_apps:
                    message = '已完成：' + '、'.join(changed_apps)
                    if warnings:
                        message += '\n\n仅完成配置导入，上游 Bearer 鉴权调用仍待验证。'
                    if details:
                        message += '\n\n本地加密备份：\n' + details
                else:
                    message = '没有需要写入的变更。'
                messagebox.showinfo('完成', message, parent=self.root)
            self.run(work, done, '正在备份并写入…')
        confirm = self._button(footer, '确认导入', commit, primary=True)
        confirm.pack(side='right', padx=8)
        if not changed:
            confirm.configure(state='disabled')



def main():
    root = tk.Tk()
    App(root)
    root.mainloop()
