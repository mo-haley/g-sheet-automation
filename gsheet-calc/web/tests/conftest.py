"""Shared pytest fixtures."""

import sys
from pathlib import Path

# Ensure gsheet-calc root is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
