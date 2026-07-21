"""Charge les polices Inter dans GDI avant l'initialisation tkinter."""
import ctypes
from pathlib import Path

_FONT_DIR = Path(__file__).parent / "fonts"
_FR_PRIVATE = 0x10  # ne pas exposer aux autres apps


def load_inter() -> None:
    for ttf in _FONT_DIR.glob("Inter-*.ttf"):
        ctypes.windll.gdi32.AddFontResourceExW(str(ttf), _FR_PRIVATE, 0)
