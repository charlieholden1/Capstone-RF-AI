# Capstone: RF+AI Jamming Detection & Mitigation

**Goal:** Build a Raspberry Pi 5 + SDR system that identifies RF interference/jamming in real time and triggers mitigation (policy/control).

## Repo Map
- `docs/` — proposals, reports, diagrams, notes
- `hardware/` — BOM, wiring diagrams, schematics
- `firmware/` — embedded code (ESP32/STM32 if added)
- `software/` — Pi 5 code
  - `dsp/` — STFT/FFT/IQ feature extraction
  - `ml/` — training/inference pipelines
  - `control/` — mitigation policy / RL
- `tests/` — test cases, fixtures
- `data/` — small sample captures/logs (bigger files tracked with Git LFS)
## Quickstart
- Python env: `python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
- Run unit tests: `pytest -q`
