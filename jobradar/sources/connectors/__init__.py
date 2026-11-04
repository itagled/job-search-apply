"""Connectors for job posting sources. Each module registers its own with ``@register``."""

from __future__ import annotations

import importlib
import pkgutil


def load_all() -> None:
    """Import all modules in the package so they get registered."""
    for module in pkgutil.iter_modules(__path__):
        importlib.import_module(f"{__name__}.{module.name}")
