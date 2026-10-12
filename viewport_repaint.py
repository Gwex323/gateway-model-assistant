"""Refresh embedded Tk controls after their Canvas viewport moves."""
from __future__ import annotations

import tkinter as tk


def finish_viewport_paint(widget):
    """Repaint only newly exposed bands of a single-window Canvas viewport.

    Canvas geometry is processed by Tk idle handlers, whereas newly exposed
    Windows child controls may still be waiting for their Tk Expose event.
    Use content-relative geometry: native screen positions can lag scrolling.
    Redrawing the entire mapped subtree also redraws offscreen controls and is
    expensive. A resize/initial display gets a full visible-viewport repaint.
    Deliver Expose now so the required paint handlers join the current idle pass.
    No update()/update_idletasks(), nested event loop, timer loop or OS API.
    """
    try:
        if not widget.winfo_exists() or not widget.winfo_ismapped():
            return 0
        top, height = widget.winfo_rooty(), widget.winfo_height()
        state = (float(widget.canvasy(0)), widget.winfo_width(), height, widget.bbox('all'))
        previous = getattr(widget, '_last_painted_view', None)
        widget._last_painted_view = state
        if previous and previous[1:] == state[1:] and abs(state[0] - previous[0]) < height:
            delta = round(state[0] - previous[0])
            if not delta:
                return 0
            band = (top + height - delta, top + height) if delta > 0 else (top, top - delta)
        else:
            band = (top, top + height)
        pending = list(widget.winfo_children())
        if not pending:
            return 0
        content_top = pending[0].winfo_rooty()
        widget.event_generate('<Expose>', when='now')
    except tk.TclError:
        return 0
    refreshed = 0
    while pending:
        current = pending.pop()
        try:
            if not current.winfo_exists() or not current.winfo_ismapped():
                continue
            # Subtract the content window's root position to remove stale native
            # placement; its descendants' local positions do not change on scroll.
            y = top + current.winfo_rooty() - content_top - state[0]
            if y >= band[1] or y + current.winfo_height() <= band[0]:
                continue
            current.event_generate('<Expose>', when='now')
            refreshed += 1
            pending.extend(current.winfo_children())
        except tk.TclError:
            # Closing a window during a notification is harmless.
            continue
    return refreshed
