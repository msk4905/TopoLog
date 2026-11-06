"""Locations of bundled resources for the source tree and the frozen Windows build.

Source layout:  source/VERSION, source/tools/*.ps1, source/tools/topolog/*.py
Frozen layout:  <bundle>/VERSION, <bundle>/tools/*.ps1, <bundle>/topolog/assets
The bundle is read-only program data; results are never written inside it.
"""
from pathlib import Path
import sys

FROZEN = bool(getattr(sys, 'frozen', False))
PACKAGE = Path(__file__).resolve().parent


def bundle_root():
    if FROZEN:
        return Path(getattr(sys, '_MEIPASS', Path(sys.executable).resolve().parent))
    return PACKAGE.parents[1]


def tool_directory():
    return bundle_root() / 'tools' if FROZEN else PACKAGE.parent


def version_file():
    return bundle_root() / 'VERSION'


def version(default='개발판'):
    try:
        return version_file().read_text(encoding='utf-8').strip()
    except OSError:
        return default


def asset(name):
    return PACKAGE / 'assets' / name


def is_inside_bundle(path):
    """True when a path points into the frozen program folder (never a result location)."""
    if not FROZEN:
        return False
    try:
        return Path(path).resolve().is_relative_to(Path(sys.executable).resolve().parent)
    except (OSError, ValueError):
        return False
