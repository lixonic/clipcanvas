"""Licensed Malayalam fonts shared by preview, font pickers and export."""
import ctypes
from functools import lru_cache
from pathlib import Path
import sys


MALAYALAM_FONTS = (
    ('Noto Sans Malayalam', 'Noto Sans Malayalam · everyday captions'),
    ('Manjari', 'Manjari · soft, rounded text'),
    ('Anek Malayalam', 'Anek Malayalam · bold captions and titles'),
)
MALAYALAM_SAMPLE = 'മലയാളം അടിക്കുറിപ്പുകൾ'


def has_malayalam(text):
    return any('\u0d00' <= char <= '\u0d7f' for char in text)


@lru_cache(maxsize=1)
def register():
    """Add app fonts to Fontconfig without installing into a user's folders."""
    if not sys.platform.startswith('linux'):
        return False
    library = ctypes.CDLL('libfontconfig.so.1')
    library.FcConfigGetCurrent.restype = ctypes.c_void_p
    library.FcConfigAppFontAddFile.argtypes = (ctypes.c_void_p, ctypes.c_char_p)
    library.FcConfigAppFontAddFile.restype = ctypes.c_int
    library.FcConfigBuildFonts.argtypes = (ctypes.c_void_p,)
    library.FcConfigBuildFonts.restype = ctypes.c_int
    config = library.FcConfigGetCurrent()
    files = sorted((Path(__file__).parent/'fonts').glob('*/*.ttf'))
    if not config or not files:
        raise RuntimeError('Bundled Malayalam fonts are missing. Reinstall ClipCanvas.')
    for path in files:
        if not library.FcConfigAppFontAddFile(config, bytes(path)):
            raise RuntimeError(f'Could not load bundled font {path.name}.')
    if not library.FcConfigBuildFonts(config):
        raise RuntimeError('Could not prepare bundled Malayalam fonts.')
    from gi.repository import PangoCairo
    PangoCairo.FontMap.get_default().changed()
    return True
