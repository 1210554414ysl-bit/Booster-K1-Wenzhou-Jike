"""Locate the release bundle's private Python dependencies."""

import sys
from pathlib import Path

private_python = Path("resources/python").resolve()
if private_python.is_dir():
    sys.path.insert(0, str(private_python))
