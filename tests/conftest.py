import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def load_settings():
    """Read the package settings (JSON with // comments)."""
    with open(os.path.join(ROOT, "Margin.sublime-settings"), encoding="utf-8") as f:
        text = f.read()
    text = re.sub(r"^\s*//.*$", "", text, flags=re.M)
    return json.loads(text)


def load_example(name):
    with open(os.path.join(ROOT, "examples", name), encoding="utf-8") as f:
        text = f.read()
    text = re.sub(r"^\s*//.*$", "", text, flags=re.M)
    return json.loads(text)


def fixture(name):
    with open(os.path.join(ROOT, "tests", "fixtures", name), encoding="utf-8") as f:
        return f.read()
