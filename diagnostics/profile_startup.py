"""Offline startup timing; fresh Python process per sample, no client writes."""
import sys,time,json,cProfile,pstats
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
start=time.perf_counter()
import tkinter as tk
import modern_ui
import rounded_ui
imports=time.perf_counter()
profile=cProfile.Profile() if '--profile' in sys.argv else None
if profile:profile.enable()
r=tk.Tk();r.withdraw();tk_ready=time.perf_counter()
app=modern_ui.App(r)
if '--native-frames' in sys.argv:
 for name in ('Card.TFrame','Segment.TFrame'):
  app.rounded.style.layout(name, [('Frame.border', {'sticky':'nsew'})])
if '--no-repaint' in sys.argv:
 modern_ui.finish_viewport_paint=lambda *args: None
built=time.perf_counter()

if '--hidden' in sys.argv:r.attributes('-alpha',0)
r.deiconify();r.update();ready=time.perf_counter()
if profile:
 profile.disable()
 with Path(sys.argv[1]).with_suffix('.profile.txt').open('w',encoding='utf-8') as f:pstats.Stats(profile,stream=f).sort_stats('cumtime').print_stats(30)
result={'imports_ms':(imports-start)*1000,'tk_ms':(tk_ready-imports)*1000,'build_ms':(built-tk_ready)*1000,'first_paint_ms':(ready-built)*1000,'total_ms':(ready-start)*1000,'radius':app.rounded.radius,'images':len(app.rounded.images)}
app.close()
Path(sys.argv[1]).write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))
