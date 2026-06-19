"""
Profile TARGET runtime and peak memory for a 1-week Salvador run.
Run from /Users/martynclark/target-UMEP (where target_runs/ lives).

Usage:
    conda run -n target-umep python \
        /Users/martynclark/hit/scripts/profiling/profile_salvador.py
"""
import time
import tracemalloc
from pathlib import Path

CONFIG_PATH = Path("/Users/martynclark/target-UMEP/target_runs/salvador/config.ini")
OUTPUT_DIR = Path("/Users/martynclark/target-UMEP/target_runs/salvador/output")

if not CONFIG_PATH.exists():
    raise FileNotFoundError(f"Config not found: {CONFIG_PATH}")

# Clear previous output so we time a cold run
for f in OUTPUT_DIR.glob("*.npy"):
    f.unlink()
    print(f"Cleared: {f.name}")

print("Starting TARGET profiling run — Salvador, 1 week (heatwave_oct2023)")
print(f"Config: {CONFIG_PATH}\n")

from target_py import Target  # noqa: E402

tracemalloc.start()
t0 = time.perf_counter()

tar = Target(str(CONFIG_PATH), progress=True)
tar.load_config()
tar.run_simulation(save_csv=True)
tar.save_simulation_parameters()

elapsed = time.perf_counter() - t0
_, peak_bytes = tracemalloc.get_traced_memory()
tracemalloc.stop()

print(f"\n{'='*50}")
print(f"Wall time : {elapsed:.1f}s  ({elapsed / 60:.1f} min)")
print(f"Peak mem  : {peak_bytes / 1e9:.2f} GB")
print(f"{'='*50}")

results_dir = Path("/Users/martynclark/hit/scripts/profiling")
results_dir.mkdir(parents=True, exist_ok=True)
with (results_dir / "salvador_1week_profile.txt").open("w") as f:
    f.write(f"City: Salvador, Brazil\n")
    f.write(f"Run: heatwave_oct2023 (1 week)\n")
    f.write(f"Wall time: {elapsed:.1f}s ({elapsed / 60:.1f} min)\n")
    f.write(f"Peak memory: {peak_bytes / 1e9:.2f} GB\n")

print("Results written to scripts/profiling/salvador_1week_profile.txt")
