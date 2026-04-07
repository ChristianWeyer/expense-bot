"""Shared pytest configuration and fixtures."""

import sys
from pathlib import Path

# Projekt-Root zum Python-Path
sys.path.insert(0, str(Path(__file__).parent.parent))


def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", default=False, help="Run live API tests")
