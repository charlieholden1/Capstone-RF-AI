SpectraGuard Dashboard Configuration Guide

1. Auto-Provisioning Datasource

To ensure Grafana sees InfluxDB automatically, create the following file inside a folder named grafana/provisioning/datasources/ next to your docker-compose.yml.

File: grafana/provisioning/datasources/datasource.yml

apiVersion: 1

datasources:
  - name: InfluxDB_Spectra
    type: influxdb
    access: proxy
    url: http://spectraguard_influx:8086
    jsonData:
      version: Flux
      organization: spectraguard_org
      defaultBucket: spectraguard_bucket
    secureJsonData:
      token: my-super-secret-auth-token


2. Panel Queries (Flux)

A. The Waterfall (Heatmap Panel)

This is the most critical visual. It plots Frequency (X-Axis) vs Time (Y-Axis) with Color indicating Power.

Panel Type: Heatmap

Data Format: Time Series Buckets

Query:

from(bucket: "spectraguard_bucket")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r["_measurement"] == "spectrum")
  |> filter(fn: (r) => r["_field"] == "power")
  // Group by Frequency Bin tag to create separate series for the heatmap
  |> group(columns: ["freq_bin"]) 
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
  |> yield(name: "mean")


Settings: * Set Color Scheme to "Spectrum" or "Solar".

Set Y Axis to "freq_bin".

B. Threat Status (Stat Panel)

Shows "JAMMING DETECTED" if confidence drops below 50%.

Query:

from(bucket: "spectraguard_bucket")
  |> range(start: -1m)
  |> filter(fn: (r) => r["_measurement"] == "scan_meta")
  |> filter(fn: (r) => r["_field"] == "is_jammed")
  |> last()


Value Mapping: * 0 -> "SAFE" (Green)

1 -> "JAMMING" (Red + Blinking)

C. AI Confidence (Gauge)

Query:

from(bucket: "spectraguard_bucket")
  |> range(start: -1m)
  |> filter(fn: (r) => r["_measurement"] == "scan_meta")
  |> filter(fn: (r) => r["_field"] == "confidence")
  |> mean()


Units: Percent (0.0-1.0)

3. The "HOP NOW" Button (Command & Control)

Since Grafana cannot publish to MQTT natively without complex plugins, we use the Text Panel with HTML/JavaScript to talk to the API we built into the Python bridge.

Add a Text Panel.

Set Mode to HTML.

Paste the following code (Note: This assumes you are viewing Grafana on the same network):

<style>
  .hop-btn {
    background-color: #d00;
    color: white;
    padding: 15px 32px;
    text-align: center;
    font-size: 16px;
    border: none;
    cursor: pointer;
    width: 100%;
  }
  .hop-btn:hover { background-color: #a00; }
</style>

<button class="hop-btn" onclick="triggerHop()">☢️ EMERGENCY HOP ☢️</button>
<div id="hop-status" style="margin-top:10px; color:#aaa;"></div>

<script>
  function triggerHop() {
    // Determine the Python Bridge IP (Use window.location.hostname if running on same Pi)
    const bridgeIp = window.location.hostname; 
    
    fetch(`http://${bridgeIp}:5000/api/hop`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({freq: 923.3}) // Example safe freq
    })
    .then(response => response.json())
    .then(data => {
      document.getElementById("hop-status").innerText = "Command Sent: " + data.status;
      setTimeout(() => document.getElementById("hop-status").innerText = "", 3000);
    })
    .catch(error => {
      document.getElementById("hop-status").innerText = "Error: Bridge Unreachable";
    });
  }
</script>


Important: For this to work, the device viewing the dashboard must be able to reach port 5000 on the Raspberry Pi.