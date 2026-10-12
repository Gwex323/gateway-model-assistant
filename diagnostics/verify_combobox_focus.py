"""Record real combobox posting and sidebar focus notifications (blank UI)."""
import json,sys,tkinter as tk
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import modern_ui
trace=[]
original=modern_ui.App._reveal_sidebar_control
def traced(self,event):
 before=self.sidebar_canvas.yview()
 item={'widget':str(event.widget),'class':event.widget.winfo_class(),'focus':str(self.root.tk.call('focus')),'before':before,'height':event.widget.winfo_height()}
 original(self,event)
 item['after']=self.sidebar_canvas.yview()
 trace.append(item)
modern_ui.App._reveal_sidebar_control=traced
root=tk.Tk(); app=modern_ui.App(root); root.geometry('1280x860+30+30'); root.attributes('-topmost',True)
try:
 root.update(); app.sidebar_canvas.yview_moveto(1); root.update()
 cases=[]
 for index,combo in enumerate(app.capability_combos+[app.policy_combo]):
  root.focus_force(); root.update(); app.sidebar_canvas.yview_moveto(1); root.update()
  trace.clear(); before=app.sidebar_canvas.yview()
  combo.event_generate('<ButtonPress-1>',x=combo.winfo_width()-10,y=combo.winfo_height()//2)
  combo.event_generate('<ButtonRelease-1>',x=combo.winfo_width()-10,y=combo.winfo_height()//2)
  root.update()
  popdown=str(root.tk.call('ttk::combobox::PopdownWindow',str(combo)))
  mapped=bool(int(root.tk.call('winfo','ismapped',popdown)))
  posted_events=list(trace)
  root.tk.call('ttk::combobox::Unpost',str(combo)); root.update()
  cases.append({'index':index,'before':before,'after':app.sidebar_canvas.yview(),'popup_mapped':mapped,'events':posted_events})
 print(json.dumps(cases,ensure_ascii=False))
 Path('diagnostics/combobox-jitter-fixed.json').write_text(json.dumps(cases,ensure_ascii=False,indent=2),encoding='utf-8')
finally:app.close()
