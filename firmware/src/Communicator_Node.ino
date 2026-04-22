#include <WiFi.h>
#include <PubSubClient.h>
#include <RadioLib.h>

// =============================================================
// === CONFIGURATION (EDIT THIS) ===============================
// =============================================================
const char* ssid = "Verizon_RTW34X";        // Your WiFi Name
const char* password = "YOUR_WIFI_PASS";    // REPLACE THIS!
const char* mqtt_server = "192.168.1.189";  // Your Raspberry Pi IP Address

// =============================================================
// === HELTEC V3 PIN DEFINITIONS ===============================
// =============================================================
SX1262 radio = new Module(8, 14, 12, 13);
#define LED_PIN 35

// =============================================================
// === STATE VARIABLES =========================================
// =============================================================
WiFiClient espClient;
PubSubClient client(espClient);
float currentFreq = 915.0;

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  
  // 1. Setup Radio
  Serial.print("[System] Initializing Radio... ");
  int state = radio.begin(currentFreq, 125.0, 9, 7, 0x12, 10, 8);
  if (state == RADIOLIB_ERR_NONE) {
    Serial.println("Success!");
  } else {
    Serial.print("Failed, code ");
    Serial.println(state);
    while (true);
  }

  // 2. Setup WiFi
  setup_wifi();
  
  // 3. Setup MQTT
  client.setServer(mqtt_server, 1883);
  client.setCallback(callback);
}

void setup_wifi() {
  delay(10);
  Serial.println();
  Serial.print("[WiFi] Connecting to ");
  Serial.println(ssid);
  
  WiFi.begin(ssid, password);
  
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
    attempts++;
    if (attempts > 20) {
        Serial.println("\n[WiFi] Failed! Check credentials.");
        break;
    }
  }
  Serial.println("\n[WiFi] Connected");
}

// MQTT Callback: This is where we receive the HOP command from the Pi
void callback(char* topic, byte* message, unsigned int length) {
  String messageTemp;
  for (int i = 0; i < length; i++) {
    messageTemp += (char)message[i];
  }

  // Parse: HOP:ALL:923.3
  if (String(topic) == "lora/frequency/command") {
    if (messageTemp.startsWith("HOP")) {
       int lastColon = messageTemp.lastIndexOf(':');
       String freqStr = messageTemp.substring(lastColon + 1);
       float newFreq = freqStr.toFloat();
       
       // Validity Check
       if (newFreq > 900.0 && newFreq < 930.0) {
         Serial.println("\n------------------------------------------------");
         Serial.print("☢️ COMMAND RECEIVED: Hopping to ");
         Serial.print(newFreq);
         Serial.println(" MHz");
         Serial.println("------------------------------------------------\n");
         
         // EXECUTE HOP
         radio.setFrequency(newFreq);
         currentFreq = newFreq;
         
         // Visual Confirmation (Double Blink)
         digitalWrite(LED_PIN, HIGH); delay(100); 
         digitalWrite(LED_PIN, LOW); delay(100);
         digitalWrite(LED_PIN, HIGH); delay(100); 
         digitalWrite(LED_PIN, LOW);
       }
    }
  }
}

void reconnect() {
  while (!client.connected()) {
    Serial.print("[MQTT] Attempting connection... ");
    // Create a random client ID
    String clientId = "SpectraClient-";
    clientId += String(random(0xffff), HEX);
    
    if (client.connect(clientId.c_str())) {
      Serial.println("Connected");
      client.subscribe("lora/frequency/command");
    } else {
      Serial.print("failed, rc=");
      Serial.print(client.state());
      Serial.println(" try again in 5s");
      delay(5000);
    }
  }
}

void loop() {
  if (!client.connected()) {
    reconnect();
  }
  client.loop();

  // Send Heartbeat Packet every 2 seconds
  static unsigned long lastMsg = 0;
  if (millis() - lastMsg > 2000) {
    lastMsg = millis();
    String msg = "Status:SAFE|Freq:" + String(currentFreq);
    
    // Transmit
    radio.transmit(msg);
    Serial.print("[LoRa] Transmitted Heartbeat on ");
    Serial.print(currentFreq);
    Serial.println(" MHz");
    
    // Single Blink
    digitalWrite(LED_PIN, HIGH); delay(50); digitalWrite(LED_PIN, LOW);
  }
}