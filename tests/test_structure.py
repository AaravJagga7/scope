"""Sanity and structure tests for SCOPE-X."""

import importlib
import types


def test_package_import() -> None:
    """Ensure the top-level scopex package imports successfully."""
    import scopex

    assert scopex.__version__ == "0.1.0"


def test_submodules_exist() -> None:
    """Ensure all required core research submodules are present and importable."""
    expected_submodules = [
        "scopex.discovery",
        "scopex.verification",
        "scopex.optimisation",
        "scopex.decision",
        "scopex.storage",
    ]

    for module_name in expected_submodules:
        mod = importlib.import_module(module_name)
        assert isinstance(mod, types.ModuleType)
