from pathlib import Path

import numpy as np
import xarray as xr

DATA_VARS = [
    "Ws", "Ta", "Ts_horz", "Tac_can_roof", "roofTsrfT",
    "Tmrt", "UTCI", "UTCI_cat", "httc_urb_new", "Tb_rur",
]


def load_results(output_npy: Path) -> xr.Dataset:
    """Load TARGET .npy output into an xarray Dataset.

    Drops uninitialised trailing timesteps (date field is int 0, not datetime).
    Returns Dataset with dims (time, cell).
    """
    results = np.load(output_npy, allow_pickle=True)
    valid_mask = [
        hasattr(results[t]["date"][0][0], "strftime")
        for t in range(results.shape[0])
    ]
    results_valid = results[valid_mask]

    times = [results_valid[t]["date"][0][0] for t in range(results_valid.shape[0])]
    cell_ids = results_valid[0]["ID"][:, 0].astype(int)

    return xr.Dataset(
        {
            var: (["time", "cell"], results_valid[var][:, :, 0].astype(float))
            for var in DATA_VARS
        },
        coords={"time": times, "cell": cell_ids},
    )
