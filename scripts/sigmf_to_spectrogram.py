# scripts/sigmf_to_spectrogram.py
from pathlib import Path
import json, numpy as np, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.signal import stft, get_window
from tqdm import tqdm
from collections import defaultdict

RAW = Path("data/raw/OSU")      # <-- adjust if your layout differs
OUT = Path("data/spec")
OUT.mkdir(parents=True, exist_ok=True)

# STFT params
NPERSEG  = 1024
NOVERLAP = 768
WINDOW   = get_window("hann", NPERSEG)
SECONDS  = 1.0   # keep first 1 s per file for speed

def get_sample_rate(meta: dict, default=1_000_000.0) -> float:
    """
    Try several SigMF locations/keys for sample rate.
    """
    # common: meta["global"]["core:sample_rate"]
    for gkey in ("global", "Global"):
        g = meta.get(gkey, {})
        for k in ("core:sample_rate", "sample_rate", "core:sample_rate_hz"):
            if k in g:
                try: return float(g[k])
                except: pass
    # sometimes under captures[]
    for cap in meta.get("captures", []):
        for k in ("core:sample_rate", "sample_rate", "core:sample_rate_hz"):
            if k in cap:
                try: return float(cap[k])
                except: pass
    # last resort: default
    return float(default)

def load_osu(meta_path: Path):
    meta = json.loads(meta_path.read_text())
    fs = get_sample_rate(meta, default=1_000_000.0)

    # Prefer .dat (float32 interleaved I/Q) next to the meta
    guess = meta_path.with_name(meta_path.stem.replace(".sigmf", "") + ".dat")
    if guess.exists():
        x_f32 = np.fromfile(guess, dtype=np.float32)
        if x_f32.size < 2:
            raise ValueError(f"{guess.name} is empty or truncated")
        I = x_f32[0::2]; Q = x_f32[1::2]
        x = (I + 1j*Q).astype(np.complex64)
        return x, fs, meta

    # Fallback to .sigmf-data (complex64) if present
    alt = meta_path.with_suffix(".sigmf-data")
    if alt.exists():
        x = np.fromfile(alt, dtype=np.complex64)
        return x, fs, meta

    raise FileNotFoundError(f"No .dat or .sigmf-data for {meta_path.name}")

def iq_to_spec(x: np.ndarray, fs: float):
    f, t, Z = stft(x, fs=fs, window=WINDOW, nperseg=NPERSEG,
                   noverlap=NOVERLAP, return_onesided=False)
    S = np.abs(Z)
    S = np.fft.fftshift(S, axes=0)
    S = 20*np.log10(S + 1e-6)
    S = (S - S.min()) / (S.max() - S.min() + 1e-12)
    return S.astype(np.float32)

def main():
    meta_paths = sorted(RAW.rglob("*.sigmf-meta"))
    if not meta_paths:
        print(f"[ERROR] No .sigmf-meta under {RAW.resolve()}")
        return

    print(f"[INFO] Found {len(meta_paths)} meta files. Converting…")
    counts = defaultdict(int)
    wrote_any = False

    for meta_path in tqdm(meta_paths, desc="Converting"):
        try:
            x, fs, meta = load_osu(meta_path)
            N = int(fs * SECONDS)
            if len(x) >= N: x = x[:N]

            S = iq_to_spec(x, fs)

            # label = parent folder name ('5m', '10m', etc.)
            label = meta_path.parent.name
            out_dir = OUT / label
            out_dir.mkdir(parents=True, exist_ok=True)

            stem = meta_path.stem.replace(".sigmf", "")
            np.save(out_dir / f"{stem}.npy", S)
            plt.imsave(out_dir / f"{stem}.png", S, cmap="gray", origin="lower")

            counts[label] += 1
            wrote_any = True

        except Exception as e:
            print(f"[WARN] {meta_path}: {e}")

    print("\n[SUMMARY] Spectrograms written per label:")
    for k, v in sorted(counts.items()):
        print(f"  {k}: {v}")
    if not wrote_any:
        print("[ERROR] No spectrograms were written. Check RAW path/filenames and permissions.")

if __name__ == "__main__":
    main()
