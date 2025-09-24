# Capstone RF-AI Project

This repo contains our pipeline for **LoRa IQ dataset preprocessing and CNN training**. It converts raw `.dat` + `.sigmf-meta` IQ captures (OSU dataset) into spectrograms and trains a baseline CNN that exports to ONNX for deployment.

---

## 📂 Repo Structure

Capstone-RF-AI/
├── data/
│ ├── raw/ # raw IQ datasets (ignored in git)
│ └── spec/ # spectrograms (ignored in git)
├── models/ # trained models (.onnx, .pt, etc.)
├── scripts/
│ ├── sigmf_to_spectrogram.py # IQ → spectrogram conversion
│ ├── train_cnn_onnx.py # Train CNN and export to ONNX
│ ├── osu_5m_urls.txt # wget/aria2 download list (5m setup)
│ ├── osu_10m_urls.txt # wget/aria2 download list (10m setup)
│ └── run_all.sh # helper script (download + convert + train)
└── requirements.txt

---

## ⚙️ Setup

git clone git@github.com:<your-org>/Capstone-RF-AI.git
cd Capstone-RF-AI
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

Download Data:
We are using the OSU LoRa Dataset (Setup 4 – Distances). The .txt files under scripts/ contain direct URLs for 5m and 10m samples.
Install aria2 (for parallel downloads):

brew install aria2     # Mac
sudo apt-get install aria2  # Linux

Download subsets:
aria2c -i scripts/osu_5m_urls.txt -d data/raw/OSU/Setup4/5m
aria2c -i scripts/osu_10m_urls.txt -d data/raw/OSU/Setup4/10m
These files are large (~150 MB each). Don’t commit them to Git.
Convert IQ → Spectrograms
python scripts/sigmf_to_spectrogram.py

This creates:
data/spec/5m/*.png, *.npy
data/spec/10m/*.png, *.npy
Each folder (5m, 10m, etc.) is treated as a class label.

Train CNN + Export ONNX:
python scripts/train_cnn_onnx.py
Trains a small CNN on spectrograms
Prints validation accuracy per epoch
Saves model in models/baseline_cnn.onnx

To Quick Run-All:
bash scripts/run_all.sh
This downloads data, converts IQ to spectrograms, and trains CNN in one go.

Notes:
data/raw, data/spec, and models/ are gitignored.
Use *_urls.txt files to re-download datasets.

If accuracy looks flat, tweak preprocessing (longer clips, augmentation, model depth).

References:
OSU LoRa Dataset: Release Note PDF
IEEE Access Paper: LoRa Device Fingerprinting in the Wild (2021)