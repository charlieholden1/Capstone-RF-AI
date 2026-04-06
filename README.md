# Capstone: RF+AI Jamming Detection & Mitigation

**Goal:** Build a Raspberry Pi 5 + SDR system that identifies RF interference/jamming in real time and triggers mitigation (policy/control).

---

## Table of Contents
1. [System Overview](#system-overview)
2. [Pipeline Architecture](#pipeline-architecture)
3. [Pipeline Stages](#pipeline-stages)
   - [Stage 1 — RF Signal Capture (IO)](#stage-1--rf-signal-capture-io)
   - [Stage 2 — Digital Signal Processing (DSP)](#stage-2--digital-signal-processing-dsp)
   - [Stage 3 — ML Inference (ML)](#stage-3--ml-inference-ml)
   - [Stage 4 — Mitigation Control (Control)](#stage-4--mitigation-control-control)
4. [Hardware](#hardware)
5. [Firmware](#firmware)
6. [Repo Map](#repo-map)
7. [Quickstart](#quickstart)
8. [Testing](#testing)

---

## System Overview

This project implements a real-time RF jamming detection and mitigation system running on a Raspberry Pi 5 paired with a Software Defined Radio (SDR). The system continuously monitors the RF spectrum, extracts signal features, classifies interference using a trained ML model, and applies a mitigation policy when jamming is detected.

```
┌─────────────┐    IQ samples    ┌─────────────┐    features    ┌─────────────┐
│  SDR / RF   │ ──────────────► │     DSP     │ ─────────────► │     ML      │
│  Hardware   │                  │  (io + dsp) │                │  Inference  │
└─────────────┘                  └─────────────┘                └──────┬──────┘
                                                                        │
                                                               detection result
                                                                        │
                                                                        ▼
                                                               ┌─────────────────┐
                                                               │  Control/Policy │
                                                               │  (mitigation)   │
                                                               └────────┬────────┘
                                                                        │
                                                                 action / alert
                                                                        │
                                                                        ▼
                                                               ┌─────────────────┐
                                                               │    Outputs /    │
                                                               │     Logging     │
                                                               └─────────────────┘
```

---

## Pipeline Architecture

The full pipeline is composed of four sequential software stages running on the Raspberry Pi 5, fed by SDR hardware:

| # | Stage | Module | Input | Output |
|---|-------|--------|-------|--------|
| 1 | RF Signal Capture | `software/io/` | SDR hardware (USB) | Raw IQ sample buffers |
| 2 | Digital Signal Processing | `software/dsp/` | Raw IQ buffers | Feature vectors (STFT/FFT/power) |
| 3 | ML Inference | `software/ml/` | Feature vectors | Jamming classification + confidence |
| 4 | Mitigation Control | `software/control/` | Classification result | Mitigation action / alert |

---

## Pipeline Stages

### Stage 1 — RF Signal Capture (IO)

**Module:** `software/io/`

The IO layer interfaces directly with the SDR hardware (e.g. RTL-SDR, HackRF) over USB. It is responsible for:

- Configuring the SDR center frequency, sample rate, and gain
- Reading continuous streams of complex IQ (In-phase/Quadrature) samples from the device
- Buffering samples and passing them downstream to the DSP stage

Raw IQ data can also be saved to `data/` for offline analysis and model training.

---

### Stage 2 — Digital Signal Processing (DSP)

**Module:** `software/dsp/`

The DSP stage converts raw IQ sample buffers into structured feature vectors suitable for ML classification. Key operations include:

- **FFT / Power Spectral Density** — transform time-domain IQ samples into the frequency domain to observe which frequencies carry energy
- **STFT (Short-Time Fourier Transform)** — produce a spectrogram (time × frequency) that captures how the spectrum evolves over time
- **IQ feature extraction** — compute statistical and signal-level features (e.g. signal-to-noise ratio, spectral flatness, peak power, bandwidth estimates)

The output is a compact feature vector (or spectrogram slice) passed to the ML stage.

---

### Stage 3 — ML Inference (ML)

**Module:** `software/ml/`

The ML stage loads a pre-trained model and classifies each incoming feature vector in real time. Responsibilities include:

- **Inference** — run the trained model (PyTorch / scikit-learn) against the feature vector to produce a class label and confidence score
- **Classes** — e.g. `normal`, `tone_jammer`, `sweep_jammer`, `barrage_jammer`, etc.
- **Training pipeline** — offline scripts to train and validate models against labeled captures stored in `data/`

Dependencies: `torch`, `torchaudio`, `scikit-learn` (see `requirements.txt`).

---

### Stage 4 — Mitigation Control (Control)

**Module:** `software/control/`

The control stage receives the classification result and decides on a mitigation response. Possible approaches include:

- **Rule-based policy** — trigger pre-defined actions (e.g. switch frequency, reduce transmit power, alert operator) when a jammer is detected above a confidence threshold
- **Reinforcement Learning (RL)** — an RL agent learns optimal mitigation actions over time by observing the outcome of its decisions on subsequent signal quality measurements

Mitigation actions and detection events are logged to `outputs/` for analysis and model improvement.

---

## Hardware

Located in `hardware/`.

| Component | Role |
|-----------|------|
| Raspberry Pi 5 | Main compute platform; runs all pipeline software |
| SDR (e.g. RTL-SDR v3 / HackRF One) | RF front-end; samples the spectrum and streams IQ data over USB |
| Antenna | Tuned to the frequency band of interest |
| Optional: ESP32 / STM32 | Auxiliary embedded controller for low-level RF switching or sensor interfacing (see `firmware/`) |

Wiring diagrams and schematics are stored in `hardware/wiring/`.

---

## Firmware

Located in `firmware/src/` and `firmware/include/`.

Optional embedded firmware for auxiliary microcontrollers (ESP32, STM32) that may handle tasks such as:

- Hardware frequency-hopping triggers
- GPIO-based alert indicators
- Low-latency sensor interfacing

---

## Repo Map

```
Capstone-RF-AI/
├── data/               # Sample IQ captures and labeled logs (large files via Git LFS)
├── firmware/
│   ├── include/        # Firmware header files
│   └── src/            # Firmware source files (ESP32/STM32)
├── hardware/
│   └── wiring/         # Wiring diagrams and schematics
├── outputs/            # Detection logs and mitigation action records
├── software/
│   ├── io/             # SDR interface — IQ sample capture
│   ├── dsp/            # Signal processing — FFT/STFT/feature extraction
│   ├── ml/             # ML training and inference pipelines
│   └── control/        # Mitigation policy and RL agent
├── tests/              # Unit and integration tests
├── requirements.txt    # Python dependencies
└── README.md
```

---

## Quickstart

```bash
# 1. Create and activate a Python virtual environment
python -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. (Optional) Verify the install by running the test suite
pytest -q
```

---

## Testing

```bash
# Run all tests
pytest -q

# Run tests with verbose output
pytest -v
```

Test cases and fixtures live in `tests/`.
