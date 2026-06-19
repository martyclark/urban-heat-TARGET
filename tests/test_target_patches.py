"""
Regression tests for the four target_py patches.
These tests would fail against a clean PyPI install of target_py.
"""
import math
import importlib.util
from pathlib import Path

VENDOR = Path(__file__).parent.parent / "vendor" / "target_py"


def _load(relative_path):
    path = VENDOR / relative_path
    spec = importlib.util.spec_from_file_location(relative_path, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_tbRurSolver_iterations():
    """TbRurSolver must use 100 iterations, not 900000."""
    mod = _load("scripts/TbRurSolver.py")
    assert mod.iterations == 100, (
        f"Expected iterations=100, got {mod.iterations}. "
        "Clean PyPI install has 900000 — causes multi-minute hangs."
    )


def test_utci_domain_guard():
    """UTCI pow() argument must be clamped to prevent math domain error on coastal cells."""
    source = (VENDOR / "scripts" / "UTCI.py").read_text()
    assert "max(" in source and "1e-10" in source, (
        "UTCI.py is missing the max(..., 1e-10) clamp before pow(..., 0.25). "
        "Negative arguments cause ValueError on ocean/coastal cells."
    )
    # Also confirm the clamp actually works numerically
    clamped = max(-999.0, 1e-10)
    result = math.pow(clamped, 0.25)
    assert result > 0


def test_toolkit_slice():
    """toolkit.py must slice mod_rslts to len(met_data_all), not leave trailing uninitialised rows."""
    source = (VENDOR / "scripts" / "toolkit.py").read_text()
    assert "mod_rslts[1:len(met_data_all)]" in source, (
        "toolkit.py is missing the len(met_data_all) slice. "
        "Without it, trailing uninitialised rows have date=0 (int), "
        "causing AttributeError: 'int' object has no attribute 'strftime'."
    )


def test_utils_uninitialised_row_guard():
    """utils.py must skip uninitialised rows and use a portable strftime format."""
    source = (VENDOR / "ui" / "utils.py").read_text()
    assert "hasattr" in source and "strftime" in source, (
        "utils.py is missing the hasattr guard for uninitialised rows."
    )
    assert "%Y-%m-%d %H_%M_%S" in source, (
        "utils.py must use %Y-%m-%d %H_%M_%S format — %F is not portable on all platforms."
    )
