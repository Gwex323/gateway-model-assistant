import tkinter as tk,time,statistics,json,runpy,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import modern_ui

def partial_paint(viewport):
 if not viewport.winfo_ismapped(): return 0
 top=viewport.winfo_rooty(); height=viewport.winfo_height(); width=viewport.winfo_width()
 state=(float(viewport.canvasy(0)),width,height,viewport.bbox('all'))
 old=getattr(viewport,'_last_painted_view',None); viewport._last_painted_view=state
 if old and old[1:]==state[1:] and abs(state[0]-old[0])<height:
  delta=round(state[0]-old[0])
  if not delta:return 0
  bands=(top+height-delta,top+height) if delta>0 else (top,top-delta)
 else: bands=(top,top+height)
 count=0; stack=list(viewport.winfo_children()); content_y=stack[0].winfo_rooty()
 viewport.event_generate('<Expose>',when='now')
 while stack:
  w=stack.pop()
  if not w.winfo_ismapped():continue
  y=top+w.winfo_rooty()-content_y-state[0]; h=w.winfo_height()
  if y>=bands[1] or y+h<=bands[0]:continue
  stack.extend(w.winfo_children())
  w.event_generate('<Expose>',when='now'); count+=1
 return count
modern_ui.finish_viewport_paint=partial_paint
root=tk.Tk(); app=modern_ui.App(root); root.geometry('1280x860+30+30')
try:
 root.update(); durations=[]; counts=[]
 for i in range(60):
  start=time.perf_counter(); app.sidebar_canvas.yview_moveto(i*.007); root.update_idletasks(); durations.append((time.perf_counter()-start)*1000)
 print('partial-repaint',json.dumps({'median_ms':statistics.median(durations),'p95_ms':sorted(durations)[56],'max_ms':max(durations)}),flush=True)
finally:app.close()
runpy.run_path('diagnostics/check_scroll_repaint.py',run_name='__main__')
