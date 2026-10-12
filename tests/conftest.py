"""Shared fixtures: the repository modules are loaded by path, no xiaozhi checkout needed."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for folder in ('tts-service', 'tools'):
    sys.path.insert(0, str(ROOT / folder))


def load_module(name, relative_path):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
