#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate hit

pip install -q -e vendor/target_py/

streamlit run app.py
