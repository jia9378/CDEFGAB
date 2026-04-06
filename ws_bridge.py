"""
=============================================================================
Invisible Piano — WebSocket Bridge
=============================================================================
Reads dual sEMG from Arduino serial, classifies fingers, sends note events
to the browser demo via WebSocket.

  Arduino (USB serial) → this script → WebSocket → browser demo

PREREQUISITES:
  pip install websockets pyserial numpy scipy scikit-learn

USAGE:
  1. Open invisible_piano_demo.html in your browser
  2. Run:  python ws_bridge.py
  3. Status changes from KEYBOARD to sEMG LIVE

  Options:
    --model finger_model_dual.pkl   (specify model)
    --port /dev/cu.usbserial-XXXXX  (specify serial port)
    --keyboard                      (test mode, no Arduino needed)
=============================================================================
"""

import asyncio
import json
import sys
import os
import time
import pickle
import numpy as np
from scipy import signal as scipy_signal
from scipy.stats import kurtosis, skew
from collections import deque

SERIAL_PORT = "/dev/cu.usbserial-A5069RR4"
BAUD_RATE = 115200
SAMPLE_RATE = 500
WS_HOST = "localhost"
WS_PORT = 8765

MODEL_FILE = "finger_model_dual.pkl"

WINDOW_SIZE_MS = 200
WINDOW_SAMPLES = int(WINDOW_SIZE_MS / 1000 * SAMPLE_RATE)
WINDOW_STEP_MS = 50
WINDOW_STEP_SAMPLES = int(WINDOW_STEP_MS / 1000 * SAMPLE_RATE)

COOLDOWN_MS = 300
CONFIDENCE_THRESHOLD = 0.5


# ======================== FEATURES ========================

def extract_ch(raw, filt, p):
    f = {}
    N = len(filt)
    af = np.abs(filt)
    f[f"{p}_mav"] = np.mean(af)
    f[f"{p}_rms"] = np.sqrt(np.mean(filt ** 2))
    f[f"{p}_waveform_length"] = np.sum(np.abs(np.diff(filt)))
    zc = np.sum(np.abs(np.diff(np.sign(filt))) > 0)
    f[f"{p}_zcr"] = zc / N
    d1 = np.diff(filt)
    ssc = np.sum(np.abs(np.diff(np.sign(d1))) > 0)
    f[f"{p}_ssc"] = ssc / N
    f[f"{p}_variance"] = np.var(filt)
    f[f"{p}_peak_to_peak"] = np.max(filt) - np.min(filt)
    f[f"{p}_skewness"] = skew(filt) if N > 2 else 0
    f[f"{p}_kurtosis"] = kurtosis(filt) if N > 2 else 0
    f[f"{p}_raw_deviation"] = np.std(raw)
    f[f"{p}_raw_peak"] = np.max(np.abs(raw - np.mean(raw)))
    if N >= 8:
        freqs, psd = scipy_signal.welch(filt, fs=SAMPLE_RATE,
            nperseg=min(N, 64), noverlap=min(N // 2, 32))
        tp = np.sum(psd) + 1e-10
        f[f"{p}_mean_freq"] = np.sum(freqs * psd) / tp
        cum = np.cumsum(psd)
        mi = np.searchsorted(cum, tp / 2)
        f[f"{p}_median_freq"] = freqs[min(mi, len(freqs) - 1)]
        for bn, (lo, hi) in [("low", (20, 80)), ("mid", (80, 150)), ("high", (150, 250))]:
            mask = (freqs >= lo) & (freqs < hi)
            f[f"{p}_power_{bn}"] = np.sum(psd[mask]) / tp
        pn = psd / tp; pn = pn[pn > 0]
        f[f"{p}_spectral_entropy"] = -np.sum(pn * np.log2(pn))
    else:
        for n in ["mean_freq", "median_freq", "power_low", "power_mid",
                   "power_high", "spectral_entropy"]:
            f[f"{p}_{n}"] = 0
    return f


def extract_dual(rA, fA, rB, fB):
    feat = {}
    feat.update(extract_ch(rA, fA, "A"))
    feat.update(extract_ch(rB, fB, "B"))
    feat["cross_rms_ratio"] = feat["A_rms"] / (feat["B_rms"] + 1e-10)
    if len(fA) > 2:
        c = np.corrcoef(fA, fB)[0, 1]
        feat["cross_correlation"] = c if not np.isnan(c) else 0
    else:
        feat["cross_correlation"] = 0
    feat["cross_mav_diff"] = feat["A_mav"] - feat["B_mav"]
    return feat


# ======================== SERIAL ========================

def parse_line(line):
    try:
        if "->" in line: line = line.split("->")[1].strip()
        parts = line.split(",")
        if len(parts) == 9:
            return {"rawA": int(parts[1]), "filteredA": float(parts[2]),
                    "envelopeA": int(parts[3]),
                    "rawB": int(parts[5]), "filteredB": float(parts[6]),
                    "envelopeB": int(parts[7])}
        elif len(parts) == 8:
            return {"rawA": int(parts[0]), "filteredA": float(parts[1]),
                    "envelopeA": int(parts[2]),
                    "rawB": int(parts[4]), "filteredB": float(parts[5]),
                    "envelopeB": int(parts[6])}
    except (ValueError, IndexError):
        pass
    return None


# ======================== WEBSOCKET ========================

clients = set()

async def ws_handler(websocket):
    clients.add(websocket)
    print(f"  Browser connected ({len(clients)} total)")
    try:
        async for _ in websocket:
            pass
    finally:
        clients.discard(websocket)
        print(f"  Browser disconnected ({len(clients)} total)")


async def broadcast(data):
    if not clients: return
    msg = json.dumps(data)
    gone = set()
    for ws in clients:
        try: await ws.send(msg)
        except: gone.add(ws)
    clients -= gone


# ======================== SERIAL LOOP ========================

async def serial_loop(model, feature_cols, classes):
    import serial as pyserial

    print(f"\n  Connecting to {SERIAL_PORT}...")
    ser = pyserial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.02)
    await asyncio.sleep(2)
    ser.flushInput()
    print("  Serial connected!\n")

    rA_buf = deque(maxlen=WINDOW_SAMPLES)
    fA_buf = deque(maxlen=WINDOW_SAMPLES)
    rB_buf = deque(maxlen=WINDOW_SAMPLES)
    fB_buf = deque(maxlen=WINDOW_SAMPLES)

    n = 0
    prev = "rest"
    last_t = 0
    notes = 0

    try:
        while True:
            if ser.in_waiting > 0:
                try:
                    line = ser.readline().decode("utf-8", errors="ignore").strip()
                except:
                    await asyncio.sleep(0.001); continue

                s = parse_line(line)
                if not s: continue

                rA_buf.append(s["rawA"]); fA_buf.append(s["filteredA"])
                rB_buf.append(s["rawB"]); fB_buf.append(s["filteredB"])
                n += 1

                # Stream envelope to browser (every 10 samples = 50Hz)
                if n % 10 == 0 and clients:
                    await broadcast({"envA": abs(s["envelopeA"]),
                                     "envB": abs(s["envelopeB"])})

                # Classify every step
                if len(rA_buf) >= WINDOW_SAMPLES and n % WINDOW_STEP_SAMPLES == 0:
                    feat = extract_dual(
                        np.array(rA_buf, dtype=float), np.array(fA_buf, dtype=float),
                        np.array(rB_buf, dtype=float), np.array(fB_buf, dtype=float))
                    X = np.array([[feat.get(c, 0) for c in feature_cols]])
                    X = np.nan_to_num(X, nan=0, posinf=0, neginf=0)

                    proba = model.predict_proba(X)[0]
                    idx = np.argmax(proba)
                    pred = classes[idx]
                    conf = proba[idx]

                    if pred != "rest" and conf >= CONFIDENCE_THRESHOLD and prev == "rest":
                        now = time.time() * 1000
                        if now - last_t >= COOLDOWN_MS:
                            notes += 1
                            await broadcast({
                                "finger": pred,
                                "velocity": round(min(1.0, conf), 2),
                                "envA": abs(s["envelopeA"]),
                                "envB": abs(s["envelopeB"]),
                            })
                            prob_str = " ".join(f"{c[0]}:{p:.0%}" for c, p in zip(classes, proba))
                            print(f"  #{notes:3d}  {pred:7s}  conf:{conf:.0%}  ({prob_str})")
                            last_t = now

                    prev = pred if conf >= 0.4 else prev
            else:
                await asyncio.sleep(0.001)
    except KeyboardInterrupt:
        pass
    finally:
        ser.close()
        print(f"\n  Closed. {notes} notes played.")


# ======================== KEYBOARD TEST ========================

async def keyboard_loop():
    import tty, termios, select

    fmap = {"1":"thumb","2":"index","3":"middle","4":"ring","5":"pinky",
            "a":"thumb","s":"index","d":"middle","f":"ring","g":"pinky"}
    print("  Keyboard test — press 1-5 in terminal, Ctrl+C to stop\n")

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        while True:
            if select.select([sys.stdin], [], [], 0.05)[0]:
                ch = sys.stdin.read(1)
                if ch == "\x03": break
                if ch in fmap:
                    v = 0.5 + 0.3 * (hash(ch) % 5) / 5
                    await broadcast({"finger": fmap[ch], "velocity": round(v, 2),
                                     "envA": int(20 + v * 40), "envB": int(10 + v * 25)})
                    print(f"\r  → {fmap[ch]}     ", end="")
            await asyncio.sleep(0.01)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        print("\n  Done.")


# ======================== MAIN ========================

async def main():
    import websockets

    args = sys.argv[1:]
    model_file = MODEL_FILE
    port = "/dev/cu.usbserial-A5069RR4"
    kb = False

    i = 0
    while i < len(args):
        if args[i] == "--model" and i + 1 < len(args):
            model_file = args[i + 1]; i += 2
        elif args[i] == "--port" and i + 1 < len(args):
            port = args[i + 1]; i += 2
        elif args[i] == "--keyboard":
            kb = True; i += 1
        else:
            i += 1

    # global SERIAL_PORT
    SERIAL_PORT = port

    print("\n" + "=" * 50)
    print("  INVISIBLE PIANO — WebSocket Bridge")
    print("=" * 50)

    model = feature_cols = classes = None

    if not kb:
        # Find a model
        candidates = [model_file, "finger_model_onset.pkl",
                      "finger_model_dual.pkl", "finger_model_dual_with_rest.pkl"]
        found = None
        for c in candidates:
            if os.path.exists(c): found = c; break

        if not found:
            print(f"\n  No model found. Use --keyboard for test mode,")
            print(f"  or train: python semg_to_sound_v2.py --train <csv>")
            sys.exit(1)

        with open(found, "rb") as fp:
            md = pickle.load(fp)
        model = md["model"]
        feature_cols = md["feature_cols"]
        classes = md["classes"]
        print(f"  Model: {found}")
        print(f"  Classifier: {md.get('classifier_name', '?')}")
        print(f"  Classes: {classes}")
    else:
        print(f"  Mode: keyboard test")

    print(f"  WebSocket: ws://{WS_HOST}:{WS_PORT}")
    print(f"\n  → Open invisible_piano_demo.html in your browser\n")

    async with websockets.serve(ws_handler, WS_HOST, WS_PORT):
        print(f"  Server running. Ctrl+C to stop.\n")
        if kb:
            await keyboard_loop()
        else:
            await serial_loop(model, feature_cols, classes)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n  Stopped.")