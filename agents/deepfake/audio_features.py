"""Audio feature extractor for deepfake detection.

Runs inside agents/.venv (Python 3.11 + librosa) where numba wheels exist.
Called as a subprocess from detector.py when the API venv (Python 3.14) cannot
import librosa directly.

Usage:
    python agents/deepfake/audio_features.py <audio_path>

Prints a single JSON line to stdout.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def extract(path: str) -> dict:
    import librosa  # type: ignore
    import numpy as np

    y, sr = librosa.load(path, sr=22050, mono=True)
    if y.size == 0:
        raise ValueError("Decoded audio is empty")

    duration = float(len(y) / sr)

    # Spectral flatness — ratio of geometric to arithmetic mean of the spectrum.
    # Real voice: 0.20–0.60  |  Suspiciously uniform / synthetic: >0.80
    flatness = float(librosa.feature.spectral_flatness(y=y).mean())

    # Silence ratio — fraction of RMS frames below absolute threshold.
    # Real speech: 0.10–0.30  |  No natural pauses (< 0.05 on clips > 10 s): suspicious
    rms = librosa.feature.rms(y=y)[0]
    silence_ratio = float((rms < 0.01).mean())

    # Noise consistency — raw std of RMS energy.
    # Genuine audio varies; std < 0.01 = unnaturally consistent background (synthetic).
    noise_consistency_std = float(rms.std())

    # MFCC variance — aggregate variance across all 13 MFCC coefficients.
    # Real speech: high (> 5.0)  |  Too uniform (< 0.5): possibly synthetic.
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
    mfcc_variance = float(np.var(mfcc))

    # Pitch variance — spread of fundamental frequency estimates.
    # Real voices vary naturally in pitch; very low variance = robotic / cloned.
    try:
        f0 = librosa.yin(y, fmin=65, fmax=400, sr=sr)
        finite_f0 = f0[np.isfinite(f0)]
        pitch_variance = float(np.var(finite_f0)) if len(finite_f0) > 0 else 0.0
    except Exception:  # noqa: BLE001
        pitch_variance = 0.0

    return {
        "sample_rate": int(sr),
        "duration_seconds": round(duration, 3),
        "spectral_flatness": round(flatness, 4),
        "silence_ratio": round(silence_ratio, 3),
        "noise_consistency_std": round(noise_consistency_std, 6),
        "mfcc_variance": round(mfcc_variance, 4),
        "pitch_variance": round(pitch_variance, 4),
        "extractor": "librosa_subprocess",
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"error": "usage: audio_features.py <path>"}))
        sys.exit(1)
    try:
        print(json.dumps(extract(sys.argv[1])))
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)
