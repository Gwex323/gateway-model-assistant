"""Viewport-local repaint scheduling, with no native application access."""
import tkinter as tk
import unittest
from unittest.mock import Mock
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from viewport_repaint import finish_viewport_paint


def widget(children=(), mapped=True, y=0, height=100):
    item = Mock()
    item.winfo_exists.return_value = True
    item.winfo_ismapped.return_value = mapped
    item.winfo_children.return_value = list(children)
    item.winfo_rooty.return_value = y
    item.winfo_height.return_value = height
    item.winfo_width.return_value = 300
    item.canvasy.return_value = 0
    item.bbox.return_value = (0, 0, 300, 500)
    item._last_painted_view = None
    return item


class ViewportRepaintTests(unittest.TestCase):
    def test_initial_view_exposes_visible_subtree_without_event_loop_reentry(self):
        field = widget()
        frame = widget([field])
        canvas = widget([frame])
        self.assertEqual(finish_viewport_paint(canvas), 2)
        for item in (canvas, frame, field):
            item.event_generate.assert_called_once_with('<Expose>', when='now')
            item.update.assert_not_called()
            item.update_idletasks.assert_not_called()

    def test_unmapped_subtree_is_not_repainted(self):
        child = widget()
        hidden = widget([child], mapped=False)
        canvas = widget([hidden])
        self.assertEqual(finish_viewport_paint(canvas), 0)
        hidden.event_generate.assert_not_called()
        child.event_generate.assert_not_called()

    def test_destroyed_widget_is_safe(self):
        canvas = widget()
        canvas.winfo_exists.side_effect = tk.TclError('destroyed')
        self.assertEqual(finish_viewport_paint(canvas), 0)

    def test_closed_child_does_not_prevent_sibling_repaint(self):
        closed, live = widget(), widget()
        closed.event_generate.side_effect = tk.TclError('destroyed')
        self.assertEqual(finish_viewport_paint(widget([live, closed])), 1)
        live.event_generate.assert_called_once()

    def test_only_newly_exposed_band_is_repainted(self):
        upper, lower, offscreen = widget(y=10, height=20), widget(y=120, height=20), widget(y=300, height=20)
        content = widget([upper, lower, offscreen], height=500)
        canvas = widget([content])
        finish_viewport_paint(canvas)
        for item in (upper, lower, offscreen):
            item.event_generate.reset_mock()
        canvas.canvasy.return_value = 70
        self.assertEqual(finish_viewport_paint(canvas), 2)  # content background + new control
        upper.event_generate.assert_not_called()
        lower.event_generate.assert_called_once()
        offscreen.event_generate.assert_not_called()

    def test_dirty_band_uses_logical_coordinates_when_native_position_lags(self):
        child = widget(y=1120, height=20)
        content = widget([child], y=1000, height=500)
        canvas = widget([content])
        canvas._last_painted_view = (0.0, 300, 100, (0, 0, 300, 500))
        canvas.canvasy.return_value = 70
        self.assertEqual(finish_viewport_paint(canvas), 2)
        child.event_generate.assert_called_once()

    def test_unchanged_view_does_not_redraw(self):
        child = widget()
        canvas = widget([child])
        finish_viewport_paint(canvas)
        canvas.event_generate.reset_mock()
        child.event_generate.reset_mock()
        self.assertEqual(finish_viewport_paint(canvas), 0)
        canvas.event_generate.assert_not_called()
        child.event_generate.assert_not_called()

    def test_resize_repaints_full_visible_area(self):
        child = widget(y=140, height=20)
        content = widget([child], height=500)
        canvas = widget([content])
        finish_viewport_paint(canvas)
        child.event_generate.assert_not_called()
        canvas.winfo_height.return_value = 200
        finish_viewport_paint(canvas)
        child.event_generate.assert_called_once()


if __name__ == '__main__':
    unittest.main()
