#include <ESP8266WiFi.h>
#include <ArduinoWebsockets.h>
#include <ArduinoJson.h>
#include <ESP8266WebServer.h>
#include <EEPROM.h>
#include <ESP8266HTTPClient.h>

using namespace websockets;

// ============================================================================
// CONFIGURATION - UPDATE THESE VALUES
// ============================================================================

// WiFi Credentials (will be saved to EEPROM after first setup)
String wifi_ssid = "IDEA LAB-5G";
String wifi_password = "idealab$9889";

// Python Backend WebSocket Configuration
// Change this to your PC's local IP address running the server
const char* ws_server_url = "ws://192.168.0.160:8000/ws/esp"; 

// Relay Configuration (Active LOW = relay triggers on LOW signal)
const bool RELAY_ACTIVE_LOW = true;

// ============================================================================
// PIN DEFINITIONS (ESP8266)
// ============================================================================
const int relay_pins[] = {
  D1,  // GPIO5 - Relay 1
  D2,  // GPIO4 - Relay 2
  D6,  // GPIO12 - Relay 3
  D0,  // GPIO16 - Relay 4
  D5   // GPIO14 - Relay 5
};

const int NUM_RELAYS = 5;

// ============================================================================
// GLOBALS
// ============================================================================
WebsocketsClient ws_client;
ESP8266WebServer server(80);

bool ap_mode_active = false;
unsigned long last_ws_attempt = 0;
const unsigned long ws_reconnect_interval = 5000;

bool relay_states[5] = {false};

// EEPROM addresses
const int EEPROM_SIZE = 512;
const int SSID_ADDR = 0;
const int PASS_ADDR = 64;

// ============================================================================
// HELPER FUNCTIONS
// ============================================================================

int get_pin_state(bool logical_state) {
  if (RELAY_ACTIVE_LOW) {
    return logical_state ? LOW : HIGH;
  } else {
    return logical_state ? HIGH : LOW;
  }
}

void set_relay(int relay_index, bool state) {
  if (relay_index >= 0 && relay_index < NUM_RELAYS) {
    digitalWrite(relay_pins[relay_index], get_pin_state(state));
    relay_states[relay_index] = state;
    
    Serial.printf("Relay %d -> %s\n", relay_index + 1, state ? "ON" : "OFF");
    
    // Control grouped relays together
    if (relay_index == 0 || relay_index == 1) {
      digitalWrite(relay_pins[0], get_pin_state(state));
      digitalWrite(relay_pins[1], get_pin_state(state));
      relay_states[0] = state;
      relay_states[1] = state;
    } else if (relay_index == 2 || relay_index == 3) {
      digitalWrite(relay_pins[2], get_pin_state(state));
      digitalWrite(relay_pins[3], get_pin_state(state));
      relay_states[2] = state;
      relay_states[3] = state;
    }
  }
}

// ============================================================================
// EEPROM FUNCTIONS
// ============================================================================

void save_wifi_credentials(String ssid, String password) {
  EEPROM.begin(EEPROM_SIZE);
  
  for (int i = 0; i < EEPROM_SIZE; i++) {
    EEPROM.write(i, 0);
  }
  
  for (int i = 0; i < ssid.length(); i++) {
    EEPROM.write(SSID_ADDR + i, ssid[i]);
  }
  
  for (int i = 0; i < password.length(); i++) {
    EEPROM.write(PASS_ADDR + i, password[i]);
  }
  
  EEPROM.commit();
  EEPROM.end();
  Serial.println("WiFi credentials saved");
}

String read_wifi_ssid() {
  EEPROM.begin(EEPROM_SIZE);
  String ssid = "";
  for (int i = 0; i < 64; i++) {
    char c = char(EEPROM.read(SSID_ADDR + i));
    if (c == 0) break;
    ssid += c;
  }
  EEPROM.end();
  return ssid;
}

String read_wifi_password() {
  EEPROM.begin(EEPROM_SIZE);
  String password = "";
  for (int i = 0; i < 64; i++) {
    char c = char(EEPROM.read(PASS_ADDR + i));
    if (c == 0) break;
    password += c;
  }
  EEPROM.end();
  return password;
}

// ============================================================================
// WEBSOCKET FUNCTIONS
// ============================================================================

void publish_status(const char* device, int state) {
  if (ws_client.available()) {
    StaticJsonDocument<128> doc;
    doc["device"] = device;
    doc["state"] = state;
    
    String response;
    serializeJson(doc, response);
    
    ws_client.send(response);
    Serial.print("Sent confirmation: ");
    Serial.println(response);
  }
}

void onMessageCallback(WebsocketsMessage message) {
  Serial.print("Got Message: ");
  Serial.println(message.data());
  
  StaticJsonDocument<200> doc;
  DeserializationError error = deserializeJson(doc, message.data());
  
  if (error) {
    Serial.print("deserializeJson() failed: ");
    Serial.println(error.c_str());
    return;
  }
  
  const char* device = doc["device"];
  int state = doc["state"];
  
  if (device != nullptr) {
    bool relay_state = (state == 1);
    if (strcmp(device, "lights") == 0) {
      set_relay(0, relay_state);
    } else if (strcmp(device, "fans") == 0) {
      set_relay(2, relay_state);
    } else if (strcmp(device, "ac") == 0) {
      set_relay(4, relay_state);
    }
    
    // Send back confirmation of the state
    publish_status(device, state);
  }
}

void onEventsCallback(WebsocketsEvent event, String data) {
  if (event == WebsocketsEvent::ConnectionOpened) {
    Serial.println("WebSocket Connection Opened");
    // Publish current states to server upon connection
    publish_status("lights", relay_states[0] ? 1 : 0);
    publish_status("fans", relay_states[2] ? 1 : 0);
    publish_status("ac", relay_states[4] ? 1 : 0);
  } else if (event == WebsocketsEvent::ConnectionClosed) {
    Serial.println("WebSocket Connection Closed");
  } else if (event == WebsocketsEvent::GotPing) {
    Serial.println("Got a Ping!");
  } else if (event == WebsocketsEvent::GotPong) {
    Serial.println("Got a Pong!");
  }
}

void connect_to_websocket() {
  Serial.print("Connecting to WebSocket: ");
  Serial.println(ws_server_url);
  
  bool connected = ws_client.connect(ws_server_url);
  if (connected) {
    Serial.println("WebSocket connected!");
  } else {
    Serial.println("WebSocket connection failed!");
  }
}

// ============================================================================
// WEB SERVER
// ============================================================================

void handle_root() {
  String html = "<!DOCTYPE html><html>";
  html += "<head><meta name='viewport' content='width=device-width, initial-scale=1'>";
  html += "<title>ESP8266 WiFi Setup</title>";
  html += "<style>body{font-family:Arial;margin:20px;}input{margin:10px 0;padding:8px;width:100%;}</style>";
  html += "</head><body>";
  html += "<h2>ESP8266 Relay Controller - WiFi Setup</h2>";
  html += "<form action='/connect' method='POST'>";
  html += "WiFi SSID: <input type='text' name='ssid' required><br>";
  html += "WiFi Password: <input type='password' name='password' required><br>";
  html += "<input type='submit' value='Connect'>";
  html += "</form>";
  html += "</body></html>";
  server.send(200, "text/html", html);
}

void handle_connect() {
  String ssid = server.arg("ssid");
  String password = server.arg("password");
  
  server.send(200, "text/html", "<html><body><h2>Connecting to WiFi...</h2><p>ESP8266 will restart.</p></body></html>");
  delay(1000);
  
  save_wifi_credentials(ssid, password);
  ESP.restart();
}

void start_ap_mode() {
  WiFi.mode(WIFI_AP);
  WiFi.softAP("ESP8266_Relay_AP", "12345678");
  
  server.on("/", handle_root);
  server.on("/connect", handle_connect);
  server.begin();
  
  ap_mode_active = true;
  Serial.println("AP Mode Active");
  Serial.print("AP IP: ");
  Serial.println(WiFi.softAPIP());
}

// ============================================================================
// WIFI CONNECTION
// ============================================================================

bool connect_to_wifi() {
  String ssid = read_wifi_ssid();
  String password = read_wifi_password();
  
  if (ssid.length() == 0) {
    Serial.println("No saved WiFi credentials");
    return false;
  }
  
  Serial.print("Connecting to WiFi: ");
  Serial.println(ssid);
  
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid.c_str(), password.c_str());
  
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 20) {
    delay(1000);
    Serial.print(".");
    attempts++;
  }
  
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\nWiFi connected!");
    Serial.print("IP address: ");
    Serial.println(WiFi.localIP());
    return true;
  } else {
    Serial.println("\nWiFi connection failed");
    return false;
  }
}

// ============================================================================
// SETUP & LOOP
// ============================================================================

void setup() {
  Serial.begin(115200);
  delay(1000);
  
  Serial.println("\n\nESP8266 Relay Controller Starting...");
  
  // Initialize relay pins
  for (int i = 0; i < NUM_RELAYS; i++) {
    pinMode(relay_pins[i], OUTPUT);
    digitalWrite(relay_pins[i], get_pin_state(false));
  }
  
  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, HIGH);
  
  // Try to connect to WiFi
  if (!connect_to_wifi()) {
    Serial.println("Starting AP mode for configuration...");
    start_ap_mode();
    return;
  }
  
  // Setup WebSocket callbacks
  ws_client.onMessage(onMessageCallback);
  ws_client.onEvent(onEventsCallback);
  
  // Initial connection to WebSocket
  connect_to_websocket();
  
  Serial.println("\n=== ESP8266 Ready ===");
  Serial.print("Server URL: ");
  Serial.println(ws_server_url);
  Serial.print("Web Interface: http://");
  Serial.println(WiFi.localIP());
}

void loop() {
  server.handleClient();
  
  if (ap_mode_active) {
    digitalWrite(LED_BUILTIN, millis() % 1000 < 500 ? LOW : HIGH);
    return;
  }
  
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi lost! Reconnecting...");
    if (connect_to_wifi()) {
      Serial.println("WiFi reconnected");
    } else {
      delay(5000);
      return;
    }
  }
  
  // Handle WebSocket client loop and keep-alive ping
  if (ws_client.available()) {
    ws_client.poll();
    static unsigned long last_ping = 0;
    if (millis() - last_ping > 20000) {
      last_ping = millis();
      ws_client.ping();
    }
  } else {
    unsigned long now = millis();
    if (now - last_ws_attempt > ws_reconnect_interval) {
      last_ws_attempt = now;
      connect_to_websocket();
    }
  }
  
  static unsigned long last_blink = 0;
  if (millis() - last_blink > 2000) {
    last_blink = millis();
    digitalWrite(LED_BUILTIN, !digitalRead(LED_BUILTIN));
  }
  
  delay(10);
}