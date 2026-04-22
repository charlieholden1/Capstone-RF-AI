#include <RadioLib.h>

// =============================================================
// === HELTEC V3 PIN DEFINITIONS ===============================
// =============================================================
SX1262 radio = new Module(8, 14, 12, 13);
#define LED_PIN 35

// =============================================================
// === STATE VARIABLES =========================================
// =============================================================
bool isJamming = false;
float currentFreq = 915.0;
String jamType = "NOISE"; // Options: NOISE, PULSE, SWEEP

// Sweep Variables
float sweepStart = 914.0;
float sweepEnd = 916.0;
float sweepStep = 0.2;
float sweepCurrent = 914.0;
bool sweepUp = true;

// Pulse Variables
unsigned long lastPulseTime = 0;
bool pulseState = false;

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  
  // Wait for Serial to initialize
  delay(1000);
  Serial.println("\n\n============================================");
  Serial.println("   SPECTRA GUARD - DYNAMIC JAMMER CONSOLE   ");
  Serial.println("============================================");
  Serial.println("Commands:");
  Serial.println("  START       -> Activate Jammer");
  Serial.println("  STOP        -> Deactivate Jammer");
  Serial.println("  FREQ 915.0  -> Set Center Frequency");
  Serial.println("  TYPE NOISE  -> Constant Packet Flood");
  Serial.println("  TYPE PULSE  -> 10Hz Intermittent Bursts");
  Serial.println("  TYPE SWEEP  -> Sweep +/- 1MHz");
  Serial.println("============================================\n");

  // Initialize Radio
  Serial.print("[System] Initializing Radio... ");
  // Frequency, Bandwidth, Spreading Factor, Coding Rate, Sync Word, Output Power, Preamble Length
  int state = radio.begin(currentFreq, 125.0, 9, 7, 0x12, 22, 8); // 22dBm = Max Power
  
  if (state == RADIOLIB_ERR_NONE) {
    Serial.println("Success!");
  } else {
    Serial.print("Failed, code ");
    Serial.println(state);
    while (true);
  }
}

void loop() {
  // 1. Handle Serial Commands (Non-blocking)
  handleSerial();

  // 2. Run Jammer Logic
  if (isJamming) {
    digitalWrite(LED_PIN, HIGH); // LED ON while active
    
    if (jamType == "NOISE") {
      runNoiseMode();
    } 
    else if (jamType == "PULSE") {
      runPulseMode();
    } 
    else if (jamType == "SWEEP") {
      runSweepMode();
    }
  } else {
    digitalWrite(LED_PIN, LOW); // LED OFF when idle
  }
}

// =============================================================
// === JAMMING MODES ===========================================
// =============================================================

void runNoiseMode() {
  // Standard Constant Wave (CW) or Packet Flood
  // We use Packet Flood as it's more effective against LoRa modems
  radio.transmit("NOISE_FLOOR_TEST_PACKET_FLOODING_CHANNEL_ACTIVE");
}

void runPulseMode() {
  // 50ms ON, 50ms OFF (10Hz)
  if (millis() - lastPulseTime > 50) {
    lastPulseTime = millis();
    pulseState = !pulseState;
    
    if (pulseState) {
       radio.transmit("PULSE_ATTACK_ACTIVE");
    }
  }
}

void runSweepMode() {
  // Move frequency, transmit, move frequency
  radio.setFrequency(sweepCurrent);
  radio.transmit("SWEEP_ATTACK_ACTIVE");
  
  if (sweepUp) {
    sweepCurrent += sweepStep;
    if (sweepCurrent >= sweepEnd) sweepUp = false;
  } else {
    sweepCurrent -= sweepStep;
    if (sweepCurrent <= sweepStart) sweepUp = true;
  }
}

// =============================================================
// === COMMAND PARSER ==========================================
// =============================================================

void handleSerial() {
  if (Serial.available() > 0) {
    String command = Serial.readStringUntil('\n');
    command.trim();
    command.toUpperCase();

    // --- TOGGLE COMMANDS ---
    if (command == "START") {
      isJamming = true;
      Serial.println(">> JAMMER STARTED");
    } 
    else if (command == "STOP") {
      isJamming = false;
      radio.standby(); // Stop RF emission immediately
      Serial.println(">> JAMMER STOPPED");
    }
    
    // --- FREQ COMMAND ---
    else if (command.startsWith("FREQ ")) {
      float newFreq = command.substring(5).toFloat();
      if (newFreq > 800 && newFreq < 960) {
        currentFreq = newFreq;
        radio.setFrequency(currentFreq);
        
        // Update sweep limits based on new center
        sweepStart = currentFreq - 1.0;
        sweepEnd = currentFreq + 1.0;
        sweepCurrent = sweepStart;
        
        Serial.print(">> FREQ SET TO: ");
        Serial.print(currentFreq);
        Serial.println(" MHz");
      } else {
        Serial.println(">> ERROR: Invalid Frequency");
      }
    }
    
    // --- TYPE COMMAND ---
    else if (command.startsWith("TYPE ")) {
      String newType = command.substring(5);
      if (newType == "NOISE" || newType == "PULSE" || newType == "SWEEP") {
        jamType = newType;
        Serial.print(">> MODE SET TO: ");
        Serial.println(jamType);
      } else {
        Serial.println(">> ERROR: Unknown Type (Use NOISE, PULSE, SWEEP)");
      }
    }
  }
}