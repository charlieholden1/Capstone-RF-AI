from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

# --- PASTE YOUR TOKEN BELOW ---
INFLUX_TOKEN = "my-super-secret-auth-token" 
INFLUX_ORG = "spectraguard_org"
INFLUX_BUCKET = "spectraguard_bucket"

print(f"1. Attempting to connect with token: {INFLUX_TOKEN[:10]}...")

try:
    client = InfluxDBClient(url="http://localhost:8086", token=INFLUX_TOKEN, org=INFLUX_ORG)
    write_api = client.write_api(write_options=SYNCHRONOUS)
    
    print("2. Client created. Attempting to write a test point...")
    
    p = Point("debug_test").tag("status", "check").field("value", 1.0)
    write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=p)
    
    print("✅ SUCCESS! The database accepted the token.")
except Exception as e:
    print(f"❌ FAILED. Error details:\n{e}")
