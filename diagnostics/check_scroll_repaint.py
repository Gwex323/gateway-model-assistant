"""Manual Windows pixel QA. Blank test window only; requires local Pillow."""
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import tkinter as tk
from PIL import ImageGrab, ImageChops
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modern_ui import App
u = ctypes.WinDLL('user32')
u.LogicalToPhysicalPointForPerMonitorDPI.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
u.RedrawWindow.argtypes = [wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
root = tk.Tk()
app = App(root)
root.attributes('-topmost', True)
records = []
try:
    def capture():
        view = app.sidebar_canvas
        p = wintypes.POINT(view.winfo_rootx(), view.winfo_rooty())
        q = wintypes.POINT(p.x + view.winfo_width(), p.y + view.winfo_height())
        for point in (p, q):
            u.LogicalToPhysicalPointForPerMonitorDPI(root.winfo_id(), ctypes.byref(point))
        return ImageGrab.grab(bbox=(p.x, p.y, q.x, q.y), include_layered_windows=True)

    root.minsize(940, 400)
    for size in ('1280x860', '940x700', '940x500'):
        root.geometry(size + '+30+30')
        root.update()
        root.after(200, root.quit)
        root.mainloop()
        command = root.tk.splitlist(app.sidebar_scroll.cget('command'))
        steps = [('scrollbar', value) for value in (.03, .09, .18, .27, .45, .62, .39, .14, 0)]
        steps += [('wheel', value) for value in (-120, -120, 120, 120)]
        steps += [('keyboard', 'bottom'), ('keyboard', 'top')]
        for kind, value in steps:
            if kind == 'scrollbar':
                root.tk.call(*command, 'moveto', value)
            elif kind == 'wheel':
                app._scroll_sidebar(SimpleNamespace(delta=value))
            else:
                target = app.policy_combo if value == 'bottom' else app.protocol_buttons['OpenAI']
                app._reveal_sidebar_control(SimpleNamespace(widget=target))
            root.update_idletasks()
            ctypes.windll.dwmapi.DwmFlush()
            moving = capture()
            u.RedrawWindow(app.sidebar_canvas.winfo_id(), None, None, 0x185)
            root.update()
            ctypes.windll.dwmapi.DwmFlush()
            settled = capture()
            diff = ImageChops.difference(moving, settled)
            count = sum(pixel != (0, 0, 0) for pixel in diff.get_flattened_data())
            records.append({'size': size, 'path': kind, 'value': value,
                            'changed_pixels': count, 'bbox': diff.getbbox()})
    output = Path(__file__).with_name('scroll-production-verification.json')
    output.write_text(json.dumps(records, indent=2), encoding='utf-8')
    print(json.dumps(records))
    print('Zero-difference cases:', sum(row['changed_pixels'] == 0 for row in records), '/', len(records))
finally:
    app.close()
