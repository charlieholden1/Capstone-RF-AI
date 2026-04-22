#!/bin/bash

echo "🛡️  SpectraGuard Environment Setup"
echo "=================================="

# 1. Create Directories
mkdir -p grafana/provisioning/datasources
mkdir -p grafana/provisioning/dashboards
mkdir -p mosquitto/config
mkdir -p mosquitto/data
mkdir -p mosquitto/log

# 2. Mosquitto Config (Open Listener)
echo "configure mosquitto..."
cat <<EOF > mosquitto/config/mosquitto.conf
listener 1883
allow_anonymous true
persistence true
persistence_location /mosquitto/data/
log_dest file /mosquitto/log/mosquitto.log
EOF

# 3. Grafana Datasource Auto-Link
echo "configure grafana..."
cat <<EOF > grafana/provisioning/datasources/datasource.yml
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
EOF

# 4. Permissions (Fixes common Docker errors on Pi)
echo "fixing permissions..."
sudo chown -R 472:472 grafana
sudo chmod -R 777 mosquitto

echo "✅ Setup Complete. Run 'docker compose up -d' next."