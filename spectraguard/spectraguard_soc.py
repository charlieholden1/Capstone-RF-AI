import os
import time
import queue
import threading
import subprocess
import numpy as np
import scipy.signal
try:
    import paho.mqtt.client as mqtt
except Exception:
    mqtt = None

from datetime import datetime

try:
    from PIL import Image
except Exception:
    Image = None

try:
    from flask import Flask, jsonify, request, render_template, make_response
    FLASK_AVAILABLE = True
except Exception:
    Flask = None
    jsonify = lambda *a, **k: None
    request = None
    render_template = lambda *a, **k: ""
    make_response = lambda *a, **k: None
    FLASK_AVAILABLE = False

import logging
import json
from collections import deque

# NOTE: Duplicate sections were consolidated on 2025-12-17 (removed a second copy of workers/main).
# If you see strange behavior or missing endpoints, check for accidental duplication.


# =========================================================================================
# === CONFIGURATION =======================================================================
# =========================================================================================
START_FREQ = 902e6
STOP_FREQ = 928e6
STEP_FREQ = 0.5e6
SAMPLE_RATE = 4e6
NUM_SAMPLES = 2**17
TEMP_FILE = "capture.iq"
MODEL_FILE = "spectraguard_model.hef"


# *** ADJUSTED THRESHOLD ***
# If AI/Simulation confidence drops below 51%, trigger a hop.
AI_CONFIDENCE_TRIGGER = 0.51


# Frequency Hopping Config
AVAILABLE_CHANNELS = [903.5, 905.3, 915.0, 923.3, 925.7, 927.5]


# =========================================================================================
# === MQTT CONFIGURATION ==================================================================
# =========================================================================================
# CHANGE THIS to your Computer's IP (e.g., 172.20.10.2 or 192.168.1.X)
MQTT_BROKER = "172.20.10.2"
MQTT_PORT = 1883
MQTT_TOPIC_COMMAND = "lora/frequency/command"
MQTT_TOPIC_STATUS = "lora/frequency/status"


# Globals
data_queue = queue.Queue(maxsize=5)
latest_state = {
    "status": "CONNECTING...",
    "confidence": 1.0,
    "freq": 0.0,
    "spectrum": [0]*128,
    "nodes": {},
    "logs": ["System Booting..."]
}


lock = threading.RLock()


running = True
current_network_freq = 915.0
last_hop_time = 0.0


# Jam detection tuning
JAM_DETECTION_COUNT = 1            # number of consecutive detections required (1 -> notify on first detection for active channel)
JAM_DETECTION_WINDOW = 2.0         # seconds to accumulate consecutive detections
HOP_DEBOUNCE = 5.0                 # seconds between hops to avoid thrash
jam_counters = {}
pending_hop_freq = None  # track requested hop until nodes confirm via FREQ_CHANGED


def status_manager():
    """Periodically request node status so nodes that started earlier will report in.
    - At startup: aggressively ping nodes every 5s for 30s
    - Afterwards: ping every 60s as a health check
    """
    # aggressive warm-up pings
    start = time.time()
    while running and time.time() - start < 30:
        try:
            mqtt_c.publish(MQTT_TOPIC_COMMAND, "STATUS:ALL", qos=1)
        except Exception:
            pass
        time.sleep(5)

    # regular heartbeats
    while running:
        try:
            mqtt_c.publish(MQTT_TOPIC_COMMAND, "STATUS:ALL", qos=1)
        except Exception:
            pass
        time.sleep(60)


def add_log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    with lock:
        latest_state["logs"].insert(0, f"[{ts}] {msg}")
        if len(latest_state["logs"]) > 20: latest_state["logs"].pop()


def execute_smart_hop(reason="Threat Detected"):
    global last_hop_time
    # Debounce check
    if time.time() - last_hop_time < HOP_DEBOUNCE: return


    target = hopper.get_hop_suggestion()
    if not target:
        return


    # 1) Broadcast to ALL
    success_broadcast = publish_hop(target)


    # 2) Also try per-node publish for known nodes (works if nodes expect node-specific commands)
    node_success = False
    with lock:
        nodes = list(latest_state.get('nodes', {}).keys())
    for nid in nodes:
        try:
            if publish_hop(target, node_id=nid, per_node=False):
                node_success = True
        except Exception as e:
            logging.debug(f"Per-node publish failed for {nid}: {e}")


    if success_broadcast or node_success:
        add_log(f"🚀 {reason} -> HOPPING TO {target} MHz")
    else:
        add_log(f"❌ Failed to send HOP Command to {target} MHz")


def publish_hop(freq, node_id=None, per_node=True):
    """Publish a hop command. Sends a broadcast `HOP:ALL:<freq>` and optionally a per-node `HOP:<NODE>:<freq>`.
    Returns True if at least one publish succeeded.
    """
    global last_hop_time, current_network_freq
    try:
        if not getattr(mqtt_c, 'is_connected', lambda: False)():
            logging.warning("MQTT client not connected; cannot publish hop")
            return False


        any_success = False
        payload_all = f"HOP:ALL:{freq}"
        info_all = mqtt_c.publish(MQTT_TOPIC_COMMAND, payload_all, qos=1)
        try:
            info_all.wait_for_publish(timeout=2.0)
        except Exception:
            pass
        ok_all = getattr(info_all, 'rc', 0) == mqtt.MQTT_ERR_SUCCESS or getattr(info_all, 'is_published', lambda: False)()
        if ok_all:
            logging.info(f"Published HOP (ALL) -> {freq} MHz (payload: {payload_all})")
            any_success = True


        # Optionally send per-node command (safer if nodes expect node-specific payload)
        if per_node and node_id:
            payload_node = f"HOP:{node_id}:{freq}"
            info_node = mqtt_c.publish(MQTT_TOPIC_COMMAND, payload_node, qos=1)
            try:
                info_node.wait_for_publish(timeout=2.0)
            except Exception:
                pass
            ok_node = getattr(info_node, 'rc', 0) == mqtt.MQTT_ERR_SUCCESS or getattr(info_node, 'is_published', lambda: False)()
            if ok_node:
                logging.info(f"Published HOP (NODE {node_id}) -> {freq} MHz (payload: {payload_node})")
                any_success = True


        # Update state only if we successfully published at least one message
        if any_success:
            last_hop_time = time.time()
            # Mark as pending: wait for nodes to confirm via FREQ_CHANGED to update current_network_freq
            global pending_hop_freq
            pending_hop_freq = freq
            return True
        else:
            logging.warning("All hop publish attempts failed")
            return False
    except Exception as e:
        logging.error(f"Publish exception: {e}")
        return False


if FLASK_AVAILABLE:
    app = Flask(__name__)
else:
    class _DummyApp:
        def route(self, *a, **k):
            def _decor(fn): return fn
            return _decor
        def after_request(self, *a, **k):
            def _decor(fn): return fn
            return _decor
        def run(self, *a, **k):
            logging.warning("Flask not available; app.run skipped")
    app = _DummyApp()


@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response


@app.route('/')
def index(): return render_template('index.html')


@app.route('/data')
def get_data():
    with lock: return jsonify(latest_state)


@app.route('/hop', methods=['POST'])
def manual_hop():
    try:
        freq = float(request.json.get('freq', 915.0))
        node = request.json.get('node', None)
        success = publish_hop(freq, node_id=node) if node else publish_hop(freq)
        if node:
            add_log(f"👨‍💻 COMMAND: MANUAL HOP TO {freq} MHz (node: {node})")
        else:
            add_log(f"👨‍💻 COMMAND: MANUAL HOP TO {freq} MHz")
        return jsonify({"status": "sent" if success else "failed"})
    except Exception as e:
        logging.error(f"manual_hop error: {e}")
        return jsonify({"status": "error"})


# main moved to bottom of file to ensure MQTT client and workers are defined

from collections import deque


# =========================================================================================
# === CONFIGURATION =======================================================================
# =========================================================================================
START_FREQ = 902e6
STOP_FREQ = 928e6
STEP_FREQ = 0.5e6
SAMPLE_RATE = 4e6
NUM_SAMPLES = 2**17
TEMP_FILE = "capture.iq"
MODEL_FILE = "spectraguard_model.hef"


# *** ADJUSTED THRESHOLD ***
# If AI/Simulation confidence drops below 51%, trigger a hop.
AI_CONFIDENCE_TRIGGER = 0.51


# Frequency Hopping Config
AVAILABLE_CHANNELS = [903.5, 905.3, 915.0, 923.3, 925.7, 927.5]


# =========================================================================================
# === MQTT CONFIGURATION ==================================================================
# =========================================================================================
# CHANGE THIS to your Computer's IP (e.g., 172.20.10.2 or 192.168.1.X)
MQTT_BROKER = "172.20.10.2"
MQTT_PORT = 1883
MQTT_TOPIC_COMMAND = "lora/frequency/command"
MQTT_TOPIC_STATUS = "lora/frequency/status"


# Globals
data_queue = queue.Queue(maxsize=5)
latest_state = {
    "status": "CONNECTING...",
    "confidence": 1.0,
    "freq": 0.0,
    "spectrum": [0]*128,
    "nodes": {},
    "logs": ["System Booting..."]
}


lock = threading.RLock()


running = True
current_network_freq = 915.0


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


# =========================================================================================
# === FREQUENCY HOPPER LOGIC ==============================================================
# =========================================================================================
class FrequencyHopper:
    def __init__(self, available_channels):
        self.hop_channels = sorted(available_channels)
        self.sweep_freqs = np.arange(902.0, 928.0, 0.5)
        self.connected_nodes = {}
        self.jammed_memory = {}
        self._sweep_index = 0
        self.jam_memory_duration = 10.0
        self.priority_check_interval = 0.2


    def report_jamming(self, frequency):
        self.jammed_memory[frequency] = time.time() + self.jam_memory_duration


    def report_connection(self, node_id, frequency):
        if node_id not in self.connected_nodes:
            self.connected_nodes[node_id] = {"freq": frequency, "last_check": 0}
        else:
            self.connected_nodes[node_id]["freq"] = frequency


    def _is_channel_jammed(self, frequency):
        for jammed_freq, expiry in list(self.jammed_memory.items()):
            if time.time() > expiry:
                del self.jammed_memory[jammed_freq]
                continue
            if abs(frequency - jammed_freq) < 0.2:
                return True
        return False


    def get_scan_target(self):
        now = time.time()
        for node_id, data in self.connected_nodes.items():
            if now - data["last_check"] > self.priority_check_interval:
                data["last_check"] = now
                return data["freq"]


        freq = self.sweep_freqs[self._sweep_index]
        self._sweep_index = (self._sweep_index + 1) % len(self.sweep_freqs)
        return freq


    def get_hop_suggestion(self):
        import random
        candidates = list(self.hop_channels)
        random.shuffle(candidates)
        for ch in candidates:
            if not self._is_channel_jammed(ch):
                return ch
        return candidates[0] if candidates else 915.0


hopper = FrequencyHopper(AVAILABLE_CHANNELS)


# =========================================================================================
# === HAILO AI SETUP ======================================================================
# =========================================================================================
HAILO_AVAILABLE = False
try:
    from hailo_platform import HEF, VDevice, InferVStreams, ConfigureParams, InputVStreamParams, OutputVStreamParams, FormatType, HailoStreamInterface
    HAILO_AVAILABLE = True
    logging.info("✅ SOC SYSTEM: Hailo AI Coprocessor Online.")
except ImportError:
    logging.warning("⚠️ SOC SYSTEM: Running in Simulation Mode (No NPU).")


# =========================================================================================
# === MQTT NODE LISTENER ==================================================================
# =========================================================================================
def on_mqtt_connect(client, userdata, flags, rc):
    logging.info(f"MQTT Connected (RC: {rc})")
    client.subscribe(MQTT_TOPIC_STATUS)


def on_mqtt_message(client, userdata, msg):
    global current_network_freq # *** FIX: Allow updating global freq
    try:
        payload = msg.payload.decode()
        parts = payload.split(':')
        if len(parts) >= 2:
            node_id = parts[0]
            status = parts[1]
            freq_val = 0.0

            for p in parts:
                if p.startswith('F='):
                    try: freq_val = float(p.split('=')[1])
                    except: pass

            if freq_val > 0:
                hopper.report_connection(node_id, freq_val)
                # *** FIX: SYNC NETWORK FREQ WITH NODE REPORTS ***
                # If we recently requested a hop, confirm and clear pending state only when a node
                # reports the expected frequency (prevents premature UI updates)
                global pending_hop_freq
                if pending_hop_freq is not None and abs(pending_hop_freq - freq_val) < 0.2:
                    current_network_freq = freq_val
                    add_log(f"✅ Network hop confirmed to {freq_val} MHz (node: {node_id})")
                    pending_hop_freq = None
                else:
                    current_network_freq = freq_val


            with lock:
                latest_state["nodes"][node_id] = {
                    "status": status,
                    "freq": freq_val if freq_val > 0 else "Unknown",
                    "last_seen": time.time()
                }
                if status in ["ONLINE", "EVADING", "FREQ_CHANGED"]:
                    add_log(f"📡 NODE {node_id}: {status} on {freq_val} MHz")
    except: pass


if mqtt is None:
    class _DummyMqttClient:
        def connect(self, *a, **k):
            raise Exception("MQTT not available")
        def loop_start(self):
            pass
        def publish(self, *a, **k):
            class Info:
                rc = 0
                def wait_for_publish(self, timeout=None): pass
                def is_published(self): return True
            return Info()
    mqtt_c = _DummyMqttClient()
else:
    mqtt_c = mqtt.Client()
    mqtt_c.on_connect = on_mqtt_connect
    mqtt_c.on_message = on_mqtt_message


# =========================================================================================
# === WORKERS =============================================================================
# =========================================================================================
def scanner_worker():
    while running:
        target_freq_mhz = hopper.get_scan_target()
        target_freq_hz = target_freq_mhz * 1e6

        if os.path.exists(TEMP_FILE): os.remove(TEMP_FILE)

        cmd = ["hackrf_transfer", "-r", TEMP_FILE, "-f", str(int(target_freq_hz)),
               "-s", str(int(SAMPLE_RATE)), "-n", str(NUM_SAMPLES),
               "-l", "40", "-g", "32", "-a", "1"]
        try:
            subprocess.run(cmd, capture_output=True, timeout=2.0)
            if os.path.exists(TEMP_FILE) and os.path.getsize(TEMP_FILE) > 0:
                raw = np.fromfile(TEMP_FILE, dtype=np.int8)
                iq = raw.astype(np.float32).view(np.complex64)
                try:
                    data_queue.put({"freq": target_freq_hz, "iq": iq}, block=False)
                except: pass
        except: pass
        time.sleep(0.01)


def processor_worker():
    global latest_state, current_network_freq
    class DummyCtx:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def infer(self, d): return None

    pipeline_ctx = DummyCtx()
    activation = DummyCtx()
    network_group = None


    if HAILO_AVAILABLE:
        try:
            target = VDevice()
            hef = HEF(MODEL_FILE)
            cfg_params = ConfigureParams.create_from_hef(hef, interface=HailoStreamInterface.PCIe)
            net_groups = target.configure(hef, cfg_params)
            network_group = net_groups[0]
            iparams = InputVStreamParams.make(network_group, format_type=FormatType.UINT8)
            oparams = OutputVStreamParams.make(network_group, format_type=FormatType.FLOAT32)
            activation = network_group.activate()
            pipeline_ctx = InferVStreams(network_group, iparams, oparams)
        except Exception as e: logging.error(f"AI Init Failed: {e}")


    with activation:
        with pipeline_ctx as pipeline:
            while running:
                try:
                    item = data_queue.get(timeout=2)
                    freq_hz = item['freq']
                    iq = item['iq']
                    freq_mhz = freq_hz / 1e6

                    f, t, Sxx = scipy.signal.spectrogram(iq, fs=SAMPLE_RATE, nperseg=1024, noverlap=512, return_onesided=False)
                    Sxx = np.fft.fftshift(Sxx, axes=0)
                    Sxx_db = 10 * np.log10(Sxx + 1e-9)

                    avg_power = np.mean(Sxx_db)


                    # --- CLEAN CONFIDENCE LOGIC ---
                    conf = 1.0

                    if HAILO_AVAILABLE and network_group:
                        try:
                            min_v, max_v = Sxx_db.min(), Sxx_db.max()
                            Sxx_norm = (Sxx_db - min_v) / (max_v - min_v + 1e-6)

                            img = Image.fromarray((Sxx_norm * 255).astype(np.uint8)).resize((64, 64))
                            input_tensor = np.expand_dims(np.expand_dims(np.array(img), 0), -1)
                            res = pipeline.infer({network_group.get_input_vstream_infos()[0].name: input_tensor})
                            raw = list(res.values())[0].flatten()[0]
                            conf = 1 / (1 + np.exp(-raw))
                        except: pass
                    else:
                        norm_val = (avg_power + 90) / 60.0
                        conf = 1.0 - norm_val
                        conf = max(0.0, min(1.0, conf))


                    # 3. Detection Logic
                    is_jammed = conf < AI_CONFIDENCE_TRIGGER


                    if is_jammed:
                        # Only consider jamming if it affects the active network frequency
                        if abs(current_network_freq - freq_mhz) < 0.2:
                            now = time.time()
                            key = round(freq_mhz, 2)
                            cnt, expiry = jam_counters.get(key, (0, now + JAM_DETECTION_WINDOW))
                            # expire older counters
                            if now > expiry:
                                cnt = 0
                                expiry = now + JAM_DETECTION_WINDOW
                            cnt += 1
                            jam_counters[key] = (cnt, expiry)


                            logging.debug(f"JAM CHECK: freq={freq_mhz} conf={conf:.2f} avg_power={avg_power:.2f} cnt={cnt}")


                            # require multiple consecutive detections to avoid false positives
                            if cnt >= JAM_DETECTION_COUNT and time.time() - last_hop_time >= HOP_DEBOUNCE:
                                hopper.report_jamming(freq_mhz)
                                add_log(f"⚠️ Confirmed jamming on {freq_mhz} MHz (count={cnt}) - preparing to hop")
                                execute_smart_hop(f"Low Confidence ({conf:.0%})")
                                # reset counter after action
                                jam_counters[key] = (0, now + JAM_DETECTION_WINDOW)
                        else:
                            # Passive jamming (not on active node channel) - keep debug-only to avoid alarming the user
                            logging.debug(f"PASSIVE JAM: freq={freq_mhz} (Active: {current_network_freq})")

                    spectrum_ui = scipy.signal.resample(np.mean(Sxx_db, axis=1), 256).tolist()

                    now = time.time()
                    with lock:
                        # prune stale nodes
                        latest_state["nodes"] = {k:v for k,v in latest_state["nodes"].items() if now - v["last_seen"] < 120}
                        # auto-normalize transient statuses (e.g., FREQ_CHANGED or EVADING) back to ONLINE after a short time
                        for k, v in latest_state["nodes"].items():
                            if v.get("status") in ["FREQ_CHANGED", "EVADING"] and now - v.get("last_seen", 0) > 5:
                                v["status"] = "ONLINE"
                        latest_state["status"] = "JAMMED" if is_jammed else "SAFE"
                        latest_state["confidence"] = float(f"{conf:.2f}")
                        latest_state["freq"] = float(freq_mhz)
                        latest_state["spectrum"] = spectrum_ui
                        latest_state["pending_hop"] = pending_hop_freq
                        latest_state["jam_counters"] = {k: v[0] for k, v in jam_counters.items()}


                except queue.Empty: pass
                except Exception as e: logging.error(f"Processor Error: {e}")


def hopping_manager():
    while running:
        time.sleep(1.0)


def add_log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    with lock:
        latest_state["logs"].insert(0, f"[{ts}] {msg}")
        if len(latest_state["logs"]) > 20: latest_state["logs"].pop()


def execute_smart_hop(reason="Threat Detected"):
    global last_hop_time
    # Debounce check
    if time.time() - last_hop_time < HOP_DEBOUNCE: return


    target = hopper.get_hop_suggestion()
    if not target:
        return


    # 1) Broadcast to ALL
    success_broadcast = publish_hop(target)


    # 2) Also try per-node publish for known nodes (works if nodes expect node-specific commands)
    node_success = False
    with lock:
        nodes = list(latest_state.get('nodes', {}).keys())
    for nid in nodes:
        try:
            if publish_hop(target, node_id=nid, per_node=False):
                node_success = True
        except Exception as e:
            logging.debug(f"Per-node publish failed for {nid}: {e}")


    if success_broadcast or node_success:
        add_log(f"🚀 {reason} -> HOPPING TO {target} MHz")
    else:
        add_log(f"❌ Failed to send HOP Command to {target} MHz")




# Duplicate publish_hop removed; consolidated implementation above handles publishing HOP commands


if FLASK_AVAILABLE:
    app = Flask(__name__)
else:
    class _DummyApp:
        def route(self, *a, **k):
            def _decor(fn): return fn
            return _decor
        def after_request(self, *a, **k):
            def _decor(fn): return fn
            return _decor
        def run(self, *a, **k):
            logging.warning("Flask not available; app.run skipped")
    app = _DummyApp()


@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response


@app.route('/')
def index(): return render_template('index.html')


@app.route('/data')
def get_data():
    with lock: return jsonify(latest_state)


@app.route('/hop', methods=['POST'])
def manual_hop():
    try:
        freq = float(request.json.get('freq', 915.0))
        node = request.json.get('node', None)
        success = publish_hop(freq, node_id=node) if node else publish_hop(freq)
        if node:
            add_log(f"👨‍💻 COMMAND: MANUAL HOP TO {freq} MHz (node: {node})")
        else:
            add_log(f"👨‍💻 COMMAND: MANUAL HOP TO {freq} MHz")
        return jsonify({"status": "sent" if success else "failed"})
    except Exception as e:
        logging.error(f"manual_hop error: {e}")
        return jsonify({"status": "error"})


if __name__ == "__main__":
    try:
        mqtt_c.connect(MQTT_BROKER, MQTT_PORT)
        mqtt_c.loop_start()
    except Exception:
        logging.warning("MQTT Offline")

    # Start background workers
    threading.Thread(target=scanner_worker, daemon=True).start()
    threading.Thread(target=processor_worker, daemon=True).start()
    threading.Thread(target=hopping_manager, daemon=True).start()
    threading.Thread(target=status_manager, daemon=True).start()

    # Start Flask
    app.run(host='0.0.0.0', port=5000, debug=False)



