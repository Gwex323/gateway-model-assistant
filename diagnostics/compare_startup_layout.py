"""Offline old/new layout parity at two window sizes."""
import json, sys, tkinter as tk
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import modern_ui
from rounded_ui import RoundedTheme
from diagnostics.rounded_ui_before import RoundedTheme as OldTheme

def snapshot(theme, size):
    modern_ui.RoundedTheme = theme
    root = tk.Tk(); root.withdraw()
    app = modern_ui.App(root); root.geometry(size + '+30+30')
    root.deiconify(); root.update()
    def walk(widget):
        return [(str(widget), widget.winfo_x(), widget.winfo_y(), widget.winfo_width(), widget.winfo_height(),
                 widget.winfo_reqwidth(), widget.winfo_reqheight())] + [row for child in widget.winfo_children() for row in walk(child)]
    result = walk(root)
    app.close()
    return result

results = []
for size in ('1280x860', '940x700'):
    before, after = snapshot(OldTheme, size), snapshot(RoundedTheme, size)
    changes = [(a,b) for a,b in zip(before, after) if a != b]
    results.append({'size': size, 'before_count':len(before), 'after_count':len(after), 'changes':changes})
Path('diagnostics/startup-layout-parity.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
print(json.dumps(results))
assert all(not r['changes'] and r['before_count']==r['after_count'] for r in results)
