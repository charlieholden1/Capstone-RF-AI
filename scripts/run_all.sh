#!/usr/bin/env bash
set -e
source .venv/bin/activate
python scripts/sigmf_to_spectrogram.py
python scripts/train_cnn.py
