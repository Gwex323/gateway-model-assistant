"""Repeatable scroll/selection benchmark using only synthetic model data."""
import cProfile,json,pstats,statistics,sys,time
from pathlib import Path
import tkinter as tk
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import modern_ui
out=Path(sys.argv[1] if len(sys.argv)>1 else 'diagnostics/performance-baseline.json')
if '--before' in sys.argv:
 from diagnostics.rounded_ui_before import RoundedTheme
 modern_ui.RoundedTheme=RoundedTheme
root=tk.Tk(); app=modern_ui.App(root)
root.minsize(940,400)
root.geometry('940x500+30+30')
counts=[]; paint_times=[]
paint=modern_ui.finish_viewport_paint
def measured(widget):
 start=time.perf_counter(); count=paint(widget)
 counts.append(count); paint_times.append((time.perf_counter()-start)*1000)
 return count
modern_ui.finish_viewport_paint=measured
try:
 root.update(); root.after(150,root.quit); root.mainloop()
 assert app.sidebar_canvas.yview()[1] - app.sidebar_canvas.yview()[0] < 1, 'Benchmark must actually scroll'
 durations=[]; profile=cProfile.Profile(); profile.enable()
 for i in range(120):
  fraction=(i%60)/60*.55 if (i//60)%2==0 else (1-(i%60)/60)*.55
  start=time.perf_counter(); app.sidebar_canvas.yview_moveto(fraction); root.update_idletasks()
  durations.append((time.perf_counter()-start)*1000)
 profile.disable()
 def stats(values):
  values=sorted(values)
  return {'median_ms':round(statistics.median(values),3),'p95_ms':round(values[int((len(values)-1)*.95)],3),'max_ms':round(max(values),3)}
 app.ids=[f'model-{i:04d}' for i in range(300)]; app.refresh(); root.update_idletasks()
 selects=[]
 for i in range(10):
  start=time.perf_counter(); app.toggle_row(str(i)); root.update_idletasks(); selects.append((time.perf_counter()-start)*1000)
 result={'scroll':stats(durations),'paint_callback':stats(paint_times),'painted_controls_median':statistics.median(counts),'select_300_models':stats(selects)}
 out.write_text(json.dumps(result,indent=2),encoding='utf-8')
 with out.with_suffix('.profile.txt').open('w',encoding='utf-8') as f: pstats.Stats(profile,stream=f).sort_stats('cumtime').print_stats(18)
 print(json.dumps(result)); print(out.with_suffix('.profile.txt').read_text(encoding='utf-8'))
finally: app.close()
