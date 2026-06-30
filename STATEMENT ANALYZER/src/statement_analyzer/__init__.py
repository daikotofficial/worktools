"""Daikot Worktools business operations package."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCAL_PACKAGES = PROJECT_ROOT / ".python_packages"

if LOCAL_PACKAGES.exists():
    sys.path.insert(0, str(LOCAL_PACKAGES))

__all__ = ["models", "pipeline"]
