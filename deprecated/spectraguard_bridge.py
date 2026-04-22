import os
import time
import queue
import threading
import subprocess
import numpy as np
import scipy.signal
import paho.mqtt.client as mqtt
from datetime import datetime
from PIL import Image
from flask import Flask, jsonify, request, make_response
from influxdb_client import InfluxDBClient, Point, WriteOptions
import logging

# =========================================================================================
# === CONFIGURATION =======================================================================
# =========================================================================================

# --- Hardware / SDR ---
START_FREQ = 902e6
STOP_FREQ = 928e6
STEP_FREQ = 0.5e6
SAMPLE_RATE = 4e6
NUM_SAMPLES = 2**17
TEMP_FILE = "capture.iq" 

# --- Processing ---
FFT_SIZE = 1024
DOWNSAMPLE_BINS = 256
IMG_WIDTH, IMG_HEIGHT = 64, 64
NPERSEG, NOVERLAP = 1024, 512

# --- InfluxDB ---
INFLUX_URL = "http://localhost:8086"
INFLUX_TOKEN = "my-super-secret-auth-token"
INFLUX_ORG = "spectraguard_org"
INFLUX_BUCKET = "spectraguard_bucket"

# --- MQTT (Local Broker) ---
MQTT_BROKER = "127.0.0.1" 
MQTT_PORT = 1883
MQTT_TOPIC_COMMAND = "lora/frequency/command"
MQTT_TOPIC_JAMMER = "lora/jammer/command"

# --- AI ---
MODEL_FILE = "spectraguard_model.hef"

# --- Globals ---
data_queue = queue.Queue(maxsize=10)
running = True
mqtt_client = None
last_hop_time = 0
HOP_COOLDOWN = 10 
freqs_to_scan = np.arange(START_FREQ, STOP_FREQ, STEP_FREQ)
safe_frequency_candidates = [903.5, 905.3, 915.0, 923.3, 925.7, 927.5]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# =========================================================================================
# === HAILO SETUP (v4.20 COMPATIBLE) ======================================================
# =========================================================================================
HAILO_AVAILABLE = False
try:
    from hailo_platform import HEF, VDevice, InferVStreams, ConfigureParams, InputVStreamParams, OutputVStreamParams, FormatType, HailoStreamInterface
    HAILO_AVAILABLE = True
    logging.info("✅ Hailo Platform imported.")
except ImportError:
    logging.critical("❌ CRITICAL: Hailo drivers missing. AI Inference will fail.")

# =========================================================================================
# === WORKER THREADS ======================================================================
# =========================================================================================
def scanner_worker():
    logging.info("📡 Scanner Thread Started")
    capture_file = TEMP_FILE

    while running:
        for freq in freqs_to_scan:
            if os.path.exists(capture_file): os.remove(capture_file)
            
            # HackRF Capture
            # Amp Enable = 1 (Fixed)
            cmd = [
                "hackrf_transfer", "-r", capture_file, 
                "-f", str(int(freq)), "-s", str(int(SAMPLE_RATE)), 
                "-n", str(NUM_SAMPLES), 
                "-l", "40", "-g", "32", "-a", "1" 
            ]
            
            try:
                # Capture stderr to see errors
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
                if result.returncode != 0:
                     logging.warning(f"HackRF Error: {result.stderr.strip()}")
            except Exception as e:
                logging.error(f"Subprocess Failed: {e}")

            if os.path.exists(capture_file):
                raw = np.fromfile(capture_file, dtype=np.int8)
                if raw.size > 0:
                    iq = raw.astype(np.float32).view(np.complex64)
                    try:
                        data_queue.put({"freq": freq, "iq": iq, "ts": time.time()}, block=False)
                    except queue.Full: pass
            
            time.sleep(0.05)

def processor_worker():
    logging.info("⚙️ Processor Thread Started")
    
    # 1. Setup InfluxDB
    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    write_api = client.write_api(write_options=WriteOptions(batch_size=20, flush_interval=1000))
    
    # 2. Setup AI
    class DummyContext:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def infer(self, data): return None

    pipeline_ctx = DummyContext()
    activation_manager = DummyContext()
    network_group = None
    
    if HAILO_AVAILABLE:
        try:
            logging.info("🧠 Configuring Hailo-8 AI Pipeline...")
            ai_target = VDevice()
            hef = HEF(MODEL_FILE)
            
            configure_params = ConfigureParams.create_from_hef(hef, interface=HailoStreamInterface.PCIe)
            network_groups = ai_target.configure(hef, configure_params)
            network_group = network_groups[0]
            
            # *** KEY FIX: UINT8 INPUT ***
            input_params = InputVStreamParams.make(network_group, format_type=FormatType.UINT8)
            output_params = OutputVStreamParams.make(network_group, format_type=FormatType.FLOAT32)
            
            # *** KEY FIX: V4.20 ACTIVATION ***
            activation_manager = network_group.activate()
            pipeline_ctx = InferVStreams(network_group, input_params, output_params)
            
            logging.info("✅ AI Pipeline Configured.")
        except Exception as e:
            logging.error(f"AI Init Failed: {e}")

    # 3. Enter Contexts
    with activation_manager:
        with pipeline_ctx as pipeline:
            while running:
                try:
                    item = data_queue.get(timeout=1)
                    freq_center = item['freq']
                    iq = item['iq']
                    ts_nano = int(item['ts'] * 1e9)
                    
                    # --- DSP (Spectrogram) ---
                    # Fix: return_onesided=False for complex data
                    f, t, Sxx = scipy.signal.spectrogram(iq, fs=SAMPLE_RATE, nperseg=NPERSEG, noverlap=NOVERLAP, return_onesided=False)
                    Sxx = np.fft.fftshift(Sxx, axes=0)
                    Sxx_db = 10 * np.log10(Sxx + 1e-9)
                    
                    # --- AI PREPROCESSING (Robust Normalization) ---
                    min_val = Sxx_db.min()
                    max_val = Sxx_db.max()
                    
                    if max_val - min_val < 1.0:
                        Sxx_norm = np.zeros_like(Sxx_db)
                    else:
                        Sxx_norm = (Sxx_db - min_val) / (max_val - min_val)
                        
                    img = Image.fromarray((Sxx_norm * 255).astype(np.uint8)).resize((IMG_WIDTH, IMG_HEIGHT))
                    input_tensor = np.array(img)
                    input_tensor = np.expand_dims(input_tensor, axis=0)
                    input_tensor = np.expand_dims(input_tensor, axis=-1)

                    # --- INFERENCE ---
                    confidence = 0.5
                    if HAILO_AVAILABLE and network_group:
                        try:
                            input_name = network_group.get_input_vstream_infos()[0].name
                            res = pipeline.infer({input_name: input_tensor})
                            raw_out = list(res.values())[0].flatten()[0]
                            confidence = 1 / (1 + np.exp(-raw_out))
                        except Exception as e:
                            # Log sparingly
                            if np.random.rand() < 0.01: logging.error(f"Inference Error: {e}")

                    is_jammed = confidence < 0.5
                    
                    if np.random.rand() < 0.05:
                         logging.info(f"Processing {freq_center/1e6} MHz | Conf: {confidence:.2f}")

                    # --- INFLUXDB WRITE ---
                    p_meta = Point("scan_meta").tag("sensor", "hackrf_1") \
                        .field("center_freq", float(freq_center/1e6)) \
                        .field("confidence", float(confidence)) \
                        .field("is_jammed", int(is_jammed)) \
                        .time(ts_nano)
                    
                    write_api.write(bucket=INFLUX_BUCKET, record=p_meta)
                    
                    # Downsample for Waterfall visualization
                    psd = 20 * np.log10(np.abs(np.fft.fft(iq[:FFT_SIZE])) + 1e-9)
                    psd_resampled = scipy.signal.resample(psd, DOWNSAMPLE_BINS)
                    
                    points = []
                    freq_step = (SAMPLE_RATE / DOWNSAMPLE_BINS)
                    start_f = freq_center - (SAMPLE_RATE/2)
                    for i, power in enumerate(psd_resampled):
                        points.append(Point("spectrum")
                                      .tag("freq_bin", f"{start_f + (i * freq_step)/1e6:.2f}")
                                      .field("power", float(power))
                                      .time(ts_nano))
                    write_api.write(bucket=INFLUX_BUCKET, record=points)
                    
                    if is_jammed: check_and_hop(freq_center)

                except queue.Empty: continue
                except Exception as e: logging.error(f"Processing Error: {e}")

# =========================================================================================
# === CONTROL LOGIC & API =================================================================
# =========================================================================================
def check_and_hop(current_freq):
    global last_hop_time
    if time.time() - last_hop_time < HOP_COOLDOWN: return

    candidates = [f for f in safe_frequency_candidates if abs(f*1e6 - current_freq) > 1e6]
    if candidates:
        target = candidates[0]
        logging.warning(f"🚨 LIVE THREAT DETECTED! Hopping to {target} MHz")
        publish_hop(target)

def publish_hop(freq_mhz):
    global last_hop_time
    if not mqtt_client: return False
    try:
        mqtt_client.publish(MQTT_TOPIC_COMMAND, f"HOP:ALL:{freq_mhz}")
        last_hop_time = time.time()
        logging.info(f"📡 MQTT Sent: HOP to {freq_mhz} MHz")
        return True
    except Exception as e: logging.error(f"MQTT Fail: {e}"); return False

mqtt_client = mqtt.Client()
try:
    mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
    mqtt_client.loop_start()
except Exception as e: logging.warning(f"MQTT Connection Failed: {e}")

api = Flask(__name__)

# --- CORS FIX (Crucial for Grafana Button) ---
@api.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response

@api.route('/')
def index():
    return "SpectraGuard Bridge Active", 200

@api.route('/api/hop', methods=['POST', 'OPTIONS'])
def manual_hop():
    if request.method == 'OPTIONS': return _build_cors_preflight_response()
    target = request.json.get('freq', 915.0)
    success = publish_hop(target)
    return jsonify({"status": "sent" if success else "failed", "freq": target})

def _build_cors_preflight_response():
    response = make_response()
    response.headers.add("Access-Control-Allow-Origin", "*")
    response.headers.add('Access-Control-Allow-Headers', "*")
    response.headers.add('Access-Control-Allow-Methods', "*")
    return response

def api_thread():
    api.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)

if __name__ == "__main__":
    t_scan = threading.Thread(target=scanner_worker, daemon=True)
    t_proc = threading.Thread(target=processor_worker, daemon=True)
    t_api = threading.Thread(target=api_thread, daemon=True)
    t_scan.start(); t_proc.start(); t_api.start()
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        running = False
        logging.info("Shutting down...")
