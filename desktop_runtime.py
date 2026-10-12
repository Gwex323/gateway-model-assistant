"""Small desktop integrations, with resource paths independent of the CWD."""
from pathlib import Path
import sys
import tkinter as tk

from app_metadata import APP_ID


def asset_path(name):
    return Path(__file__).resolve().parent / 'assets' / name


def configure_desktop(root):
    if sys.platform == 'win32':
        import ctypes
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except (AttributeError, OSError):
            pass
    icon = asset_path('app.png')
    if icon.is_file():
        root._desktop_icon = tk.PhotoImage(master=root, file=str(icon))
        root.iconphoto(True, root._desktop_icon)
