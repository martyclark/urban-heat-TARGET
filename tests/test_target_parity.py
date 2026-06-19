"""
Parity test: hit/target/ wrapper must produce numerically identical
outputs to the existing Salvador notebook run.

Reference outputs: target-UMEP/data/BRA_salvador/heatwave_oct2023/target_results.nc
"""
import shutil
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from hit.config.city import SALVADOR
from hit.target.run import prepare_inputs, run_target
from hit.target.results import load_results

UMEP_ROOT = Path("/Users/martynclark/target-UMEP")
REFERENCE_NC = UMEP_ROOT / "data/BRA_salvador/heatwave_oct2023/target_results.nc"
DATA_DIR = UMEP_ROOT / "data/BRA_salvador"
RUN_DIR = UMEP_ROOT / "data/BRA_salvador/heatwave_oct2023"
TARGET_WORK_DIR = UMEP_ROOT / "target_runs"


@pytest.fixture(scope="module")
def result_ds(tmp_path_factory):
    """Run the TARGET wrapper for Salvador and return the result Dataset."""
    work_dir = tmp_path_factory.mktemp("target_runs")
    config_ini = prepare_inputs(
        config=SALVADOR,
        data_dir=DATA_DIR,
        run_dir=RUN_DIR,
        target_work_dir=work_dir,
    )
    output_npy = run_target(config_ini, progress=False)
    return load_results(output_npy)


@pytest.mark.skipif(
    not REFERENCE_NC.exists(),
    reason="Reference NetCDF not found — run the Salvador notebooks first",
)
def test_tac_parity(result_ds):
    reference = xr.open_dataset(REFERENCE_NC)
    xr.testing.assert_allclose(
        result_ds["Tac_can_roof"],
        reference["Tac_can_roof"],
        atol=1e-4,
    )


@pytest.mark.skipif(
    not REFERENCE_NC.exists(),
    reason="Reference NetCDF not found — run the Salvador notebooks first",
)
def test_utci_parity(result_ds):
    reference = xr.open_dataset(REFERENCE_NC)
    xr.testing.assert_allclose(
        result_ds["UTCI"],
        reference["UTCI"],
        atol=1e-4,
    )


def test_result_shape(result_ds):
    """Basic sanity: correct number of cells and non-trivial timesteps."""
    assert result_ds.sizes["cell"] > 1000, "Expected >1000 cells for Salvador"
    assert result_ds.sizes["time"] > 100, "Expected >100 timesteps for a 1-week run"


def test_no_all_nan(result_ds):
    """No variable should be entirely NaN."""
    for var in ["Tac_can_roof", "UTCI", "Tmrt", "Tb_rur"]:
        assert not np.all(np.isnan(result_ds[var].values)), f"{var} is all NaN"
