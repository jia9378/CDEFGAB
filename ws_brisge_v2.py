"""
=============================================================================
Invisible Piano — WebSocket Bridge (with calibration)
=============================================================================
Same onset detection as semg_to_sound_dual.py, with proper rest
calibration before going live.

USAGE:
  python ws_bridge.py
  python ws_bridge.py --model finger_model_dual.pkl
=============================================================================
"""

import asyncio
import serial
import json
import sys
import os
import pickle
import time
import numpy as np
from scipy import signal as scipy_signal
from scipy.stats import kurtosis, skew
from collections import deque
import websockets

# ======================== CONFIGURATION ========================
SERIAL_PORT = "/dev/cu.usbserial-A5069RR4"
BAUD_RATE = 115200
SAMPLE_RATE = 500
WS_HOST = "localhost"
WS_PORT = 8765

MODEL_FILE = "finger_model_dual.pkl"

WINDOW_SIZE_MS = 200
WINDOW_SAMPLES = int(WINDOW_SIZE_MS / 1000 * SAMPLE_RATE)
COOLDOWN_MS = 250
CALIBRATION_SECONDS = 3
# ===============================================================


# ======================== FEATURES ========================

def extract_single_channel(window_raw, window_filtered, prefix):
    f = {}
    N = len(window_filtered)
    abs_filt = np.abs(window_filtered)

    f[f"{prefix}_mav"] = np.mean(abs_filt)
    f[f"{prefix}_rms"] = np.sqrt(np.mean(window_filtered ** 2))
    f[f"{prefix}_waveform_length"] = np.sum(np.abs(np.diff(window_filtered)))
    zc = np.sum(np.abs(np.diff(np.sign(window_filtered))) > 0)
    f[f"{prefix}_zcr"] = zc / N
    d1 = np.diff(window_filtered)
    ssc = np.sum(np.abs(np.diff(np.sign(d1))) > 0)
    f[f"{prefix}_ssc"] = ssc / N
    f[f"{prefix}_variance"] = np.var(window_filtered)
    f[f"{prefix}_peak_to_peak"] = np.max(window_filtered) - np.min(window_filtered)
    f[f"{prefix}_skewness"] = skew(window_filtered) if N > 2 else 0
    f[f"{prefix}_kurtosis"] = kurtosis(window_filtered) if N > 2 else 0
    f[f"{prefix}_raw_deviation"] = np.std(window_raw)
    f[f"{prefix}_raw_peak"] = np.max(np.abs(window_raw - np.mean(window_raw)))

    if N >= 8:
        freqs, psd = scipy_signal.welch(
            window_filtered, fs=SAMPLE_RATE,
            nperseg=min(N, 64), noverlap=min(N // 2, 32))
        tp = np.sum(psd) + 1e-10
        f[f"{prefix}_mean_freq"] = np.sum(freqs * psd) / tp
        cum = np.cumsum(psd)
        mi = np.searchsorted(cum, tp / 2)
        f[f"{prefix}_median_freq"] = freqs[min(mi, len(freqs) - 1)]
        for band, (lo, hi) in [("low", (20, 80)), ("mid", (80, 150)), ("high", (150, 250))]:
            mask = (freqs >= lo) & (freqs < hi)
            f[f"{prefix}_power_{band}"] = np.sum(psd[mask]) / tp
        pn = psd / tp; pn = pn[pn > 0]
        f[f"{prefix}_spectral_entropy"] = -np.sum(pn * np.log2(pn))
    else:
        for name in ["mean_freq", "median_freq", "power_low", "power_mid",
                      "power_high", "spectral_entropy"]:
            f[f"{prefix}_{name}"] = 0
    return f


def extract_dual_features(rawA, filtA, rawB, filtB):
    features = {}
    features.update(extract_single_channel(rawA, filtA, "A"))
    features.update(extract_single_channel(rawB, filtB, "B"))
    features["cross_rms_ratio"] = features["A_rms"] / (features["B_rms"] + 1e-10)
    if len(filtA) > 2:
        corr = np.corrcoef(filtA, filtB)[0, 1]
        features["cross_correlation"] = corr if not np.isnan(corr) else 0
    else:
        features["cross_correlation"] = 0
    features["cross_mav_diff"] = features["A_mav"] - features["B_mav"]
    return features


# ======================== SERIAL ========================

def read_sample_sync(ser):
    try:
        line = ser.readline().decode("utf-8", errors="ignore").strip()
        if not line: return None
        if "->" in line: line = line.split("->")[1].strip()
        parts = line.split(",")
        if len(parts) == 9:
            return {
                "rawA": int(parts[1]), "filteredA": float(parts[2]),
                "envelopeA": int(parts[3]),
                "rawB": int(parts[5]), "filteredB": float(parts[6]),
                "envelopeB": int(parts[7]),
            }
        elif len(parts) == 8:
            return {
                "rawA": int(parts[0]), "filteredA": float(parts[1]),
                "envelopeA": int(parts[2]),
                "rawB": int(parts[4]), "filteredB": float(parts[5]),
                "envelopeB": int(parts[6]),
            }
    except (ValueError, IndexError):
        pass
    return None


# ======================== CALIBRATION (blocking, before async) ========================

def calibrate(ser):
    """Same calibration as semg_to_sound_dual.py — runs before async loop."""

    print(f"\n  REST CALIBRATION — keep hand completely relaxed ({CALIBRATION_SECONDS}s)")
    input("  Press ENTER when ready...")
    print("  Reading baseline...", end=" ", flush=True)

    rest_envsA = []
    rest_envsB = []
    start = time.time()
    while time.time() - start < CALIBRATION_SECONDS:
        s = read_sample_sync(ser)
        if s:
            rest_envsA.append(abs(s["envelopeA"]))
            rest_envsB.append(abs(s["envelopeB"]))

    if len(rest_envsA) > 100:
        restA_p95 = np.percentile(rest_envsA, 95)
        restB_p95 = np.percentile(rest_envsB, 95)
        threshA = max(restA_p95 * 1.5, np.mean(rest_envsA) + 5, 3)
        threshB = max(restB_p95 * 1.5, np.mean(rest_envsB) + 5, 3)
        baseA = np.mean(rest_envsA)
        baseB = np.mean(rest_envsB)
        print(f"done ({len(rest_envsA)} samples)")
        print(f"    Ch A — rest mean: {baseA:.1f}, 95th: {restA_p95:.1f}, threshold: {threshA:.0f}")
        print(f"    Ch B — rest mean: {baseB:.1f}, 95th: {restB_p95:.1f}, threshold: {threshB:.0f}")
    else:
        threshA = threshB = 10
        baseA = baseB = 10
        print(f"few samples, defaults: thresh=10, base=10")

    # Velocity calibration
    print(f"\n  VELOCITY CALIBRATION — press your STRONGEST finger HARD ({CALIBRATION_SECONDS}s)")
    input("  Press ENTER, then press hard repeatedly...")

    max_envs = []
    start = time.time()
    while time.time() - start < CALIBRATION_SECONDS:
        s = read_sample_sync(ser)
        if s:
            combined = max(abs(s["envelopeA"]), abs(s["envelopeB"]))
            if combined > max(threshA, threshB):
                max_envs.append(combined)

    if len(max_envs) > 10:
        env_max = np.percentile(max_envs, 95)
    else:
        env_max = max(threshA, threshB) * 5
    print(f"  Max envelope: {env_max:.0f}")

    return {
        "threshA": threshA, "threshB": threshB,
        "baseA": baseA, "baseB": baseB,
        "env_max": env_max,
        "env_floor": max(threshA, threshB),
    }


# ======================== WEBSOCKET ========================

clients = set()

async def broadcast(msg):
    dead = set()
    for c in clients:
        try:
            await c.send(msg)
        except websockets.exceptions.ConnectionClosed:
            dead.add(c)
    clients.difference_update(dead)

async def ws_handler(websocket, path=None):
    clients.add(websocket)
    print(f"  Browser connected: {websocket.remote_address}")
    try:
        async for _ in websocket:
            pass
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        clients.discard(websocket)
        print(f"  Browser disconnected")


# ======================== SERIAL READER ========================

async def serial_reader(ser, model, feature_cols, classes, cal):
    rawA_buf = deque(maxlen=WINDOW_SAMPLES)
    filtA_buf = deque(maxlen=WINDOW_SAMPLES)
    rawB_buf = deque(maxlen=WINDOW_SAMPLES)
    filtB_buf = deque(maxlen=WINDOW_SAMPLES)

    baseA = cal["baseA"]
    baseB = cal["baseB"]
    threshA = cal["threshA"]
    threshB = cal["threshB"]
    env_floor = cal["env_floor"]
    env_range = cal["env_max"] - env_floor
    if env_range < 1: env_range = 1

    was_active = False
    last_note_time = 0
    note_count = 0
    sample_count = 0

    print(f"\n  ✓ READY — play on any surface!")
    print(f"  Open browser to invisible_piano_demo.html")
    print(f"  Ctrl+C to stop\n")

    while True:
        if ser.in_waiting > 0:
            try:
                raw_line = ser.readline().decode("utf-8", errors="ignore")
            except Exception:
                await asyncio.sleep(0.001)
                continue

            s = read_sample_sync_from_line(raw_line)
            if s is None:
                continue

            rawA_buf.append(s["rawA"])
            filtA_buf.append(s["filteredA"])
            rawB_buf.append(s["rawB"])
            filtB_buf.append(s["filteredB"])
            sample_count += 1

            # Send envelope to browser (~10Hz)
            envA = abs(s["envelopeA"])
            envB = abs(s["envelopeB"])

            if sample_count % 50 == 0 and clients:
                await broadcast(json.dumps({"envA": envA, "envB": envB}))

            if len(rawA_buf) < WINDOW_SAMPLES:
                continue

            # Delta-based onset (same as semg_to_sound_dual.py)
            baseA = baseA * 0.995 + envA * 0.005
            baseB = baseB * 0.995 + envB * 0.005

            deltaA = envA - baseA
            deltaB = envB - baseB
            is_active = deltaA > threshA * 0.5 or deltaB > threshB * 0.5

            # Debug: print periodically so you can see the signal
            if sample_count % 500 == 0:
                print(f"  [debug] envA:{envA:3.0f} baseA:{baseA:.0f} dA:{deltaA:.1f}>{threshA*0.5:.0f}?  envB:{envB:3.0f} baseB:{baseB:.0f} dB:{deltaB:.1f}>{threshB*0.5:.0f}?  active:{is_active}")

            if is_active and not was_active:
                now = time.time() * 1000
                if now - last_note_time >= COOLDOWN_MS:
                    rA = np.array(rawA_buf, dtype=float)
                    fA = np.array(filtA_buf, dtype=float)
                    rB = np.array(rawB_buf, dtype=float)
                    fB = np.array(filtB_buf, dtype=float)

                    feats = extract_dual_features(rA, fA, rB, fB)
                    X = np.array([[feats.get(col, 0) for col in feature_cols]])
                    X = np.nan_to_num(X, nan=0, posinf=0, neginf=0)

                    proba = model.predict_proba(X)[0]
                    pred_idx = np.argmax(proba)
                    predicted = classes[pred_idx]
                    confidence = proba[pred_idx]

                    # Velocity from envelope
                    combined = max(envA, envB)
                    velocity = (combined - env_floor) / env_range
                    velocity = max(0.1, min(1.0, velocity))

                    note_count += 1

                    msg = json.dumps({
                        "finger": predicted,
                        "velocity": round(velocity, 2),
                        "confidence": round(confidence, 2),
                        "envA": envA,
                        "envB": envB,
                    })
                    await broadcast(msg)

                    prob_str = " ".join(f"{c[0].upper()}:{p:.0%}" for c, p in zip(classes, proba))
                    print(f"  #{note_count:3d}  {predicted:7s} → vel:{velocity:.0%}  A:{envA:3.0f} B:{envB:3.0f}  conf:{confidence:.0%}  ({prob_str})")

                    last_note_time = now

            was_active = is_active
        else:
            await asyncio.sleep(0.001)


def read_sample_sync_from_line(raw_line):
    """Parse a line that's already been read from serial."""
    try:
        line = raw_line.strip()
        if not line: return None
        if "->" in line: line = line.split("->")[1].strip()
        parts = line.split(",")
        if len(parts) == 9:
            return {
                "rawA": int(parts[1]), "filteredA": float(parts[2]),
                "envelopeA": int(parts[3]),
                "rawB": int(parts[5]), "filteredB": float(parts[6]),
                "envelopeB": int(parts[7]),
            }
        elif len(parts) == 8:
            return {
                "rawA": int(parts[0]), "filteredA": float(parts[1]),
                "envelopeA": int(parts[2]),
                "rawB": int(parts[4]), "filteredB": float(parts[5]),
                "envelopeB": int(parts[6]),
            }
    except (ValueError, IndexError):
        pass
    return None


# ======================== MAIN ========================

async def main():
    model_file = MODEL_FILE
    serial_port = SERIAL_PORT

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--model" and i + 1 < len(args):
            model_file = args[i + 1]; i += 2
        elif args[i] == "--port" and i + 1 < len(args):
            serial_port = args[i + 1]; i += 2
        else:
            i += 1

    print()
    print("=" * 55)
    print("  INVISIBLE PIANO — WebSocket Bridge")
    print("=" * 55)

    # Load model
    if not os.path.exists(model_file):
        print(f"\n  ERROR: No model at {model_file}")
        sys.exit(1)

    print(f"\n  Model: {model_file}")
    with open(model_file, "rb") as fp:
        model_data = pickle.load(fp)

    model = model_data["model"]
    feature_cols = model_data["feature_cols"]
    classes = model_data["classes"]
    print(f"  Classifier: {model_data.get('classifier_name', '?')}")
    print(f"  Accuracy: {model_data.get('accuracy', 0):.1%}")
    print(f"  Classes: {classes}")

    # Connect serial
    print(f"\n  Serial: {serial_port}")
    try:
        ser = serial.Serial(serial_port, BAUD_RATE, timeout=0.05)
    except serial.SerialException as e:
        print(f"  ERROR: {e}")
        sys.exit(1)

    time.sleep(2)
    ser.flushInput()
    print("  Serial connected!")

    # Calibrate (blocking — same as semg_to_sound_dual.py)
    cal = calibrate(ser)

    # Start WebSocket server
    print(f"\n  WebSocket: ws://{WS_HOST}:{WS_PORT}")
    ws_server = await websockets.serve(ws_handler, WS_HOST, WS_PORT)

    try:
        await serial_reader(ser, model, feature_cols, classes, cal)
    except KeyboardInterrupt:
        print("\n\n  Stopping...")
    finally:
        ser.close()
        ws_server.close()
        print("  Done.")


if __name__ == "__main__":
    asyncio.run(main())