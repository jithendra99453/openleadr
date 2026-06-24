#include <ESP8266WiFi.h>
#include <ArduinoWebsockets.h>

using namespace websockets;

// WiFi Credentials
const char* ssid = "IDEA LAB-5G";
const char* password = "idealab$9889";

// WebSocket Server Configuration (Laptop IP)
const char* websocket_server_host = "192.168.0.159";
const uint16_t websocket_server_port = 8000;
const char* websocket_server_path = "/ws/esp";

// Relay Pins
#define RELAY1 D1
#define RELAY2 D2
#define RELAY3 D3

// Global Websocket Client
WebsocketsClient client;
unsigned long last_reconnect_attempt = 0;

void onMessageCallback(WebsocketsMessage message) {
  String msg = message.data();
  Serial.print("Received: ");
  Serial.println(msg);

  // Relay 1 (Active-Low: LOW = ON, HIGH = OFF)
  if (msg == "R1_ON") {
    digitalWrite(RELAY1, LOW);
    client.send("Relay1 ON");
  }
  else if (msg == "R1_OFF") {
    digitalWrite(RELAY1, HIGH);
    client.send("Relay1 OFF");
  }

  // Relay 2
  else if (msg == "R2_ON") {
    digitalWrite(RELAY2, LOW);
    client.send("Relay2 ON");
  }
  else if (msg == "R2_OFF") {
    digitalWrite(RELAY2, HIGH);
    client.send("Relay2 OFF");
  }

  // Relay 3
  else if (msg == "R3_ON") {
    digitalWrite(RELAY3, LOW);
    client.send("Relay3 ON");
  }
  else if (msg == "R3_OFF") {
    digitalWrite(RELAY3, HIGH);
    client.send("Relay3 OFF");
  }

  else {
    client.send("Unknown Command");
  }
}

void onEventCallback(WebsocketsEvent event, String data) {
  if (event == WebsocketsEvent::ConnectionClosed) {
    Serial.println("Connection closed");
  }
}

void setup() {
  Serial.begin(115200);

  pinMode(RELAY1, OUTPUT);
  pinMode(RELAY2, OUTPUT);
  pinMode(RELAY3, OUTPUT);

  // All relays ON initially (Active-Low: LOW = ON)
  digitalWrite(RELAY1, LOW);
  digitalWrite(RELAY2, LOW);
  digitalWrite(RELAY3, LOW);

  WiFi.begin(ssid, password);

  Serial.print("Connecting to WiFi");

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println();
  Serial.println("WiFi Connected");
  Serial.print("IP Address: ");
  Serial.println(WiFi.localIP());

  // Setup callbacks
  client.onMessage(onMessageCallback);
  client.onEvent(onEventCallback);

  Serial.println("Connecting to WebSocket server...");
  bool connected = client.connect(websocket_server_host, websocket_server_port, websocket_server_path);
  if (connected) {
    Serial.println("Connected to WebSocket Server!");
    // Send initial states to server (Active-Low: LOW = ON, HIGH = OFF)
    client.send(digitalRead(RELAY1) == LOW ? "Relay1 ON" : "Relay1 OFF");
    client.send(digitalRead(RELAY2) == LOW ? "Relay2 ON" : "Relay2 OFF");
    client.send(digitalRead(RELAY3) == LOW ? "Relay3 ON" : "Relay3 OFF");
  } else {
    Serial.println("Connection to WebSocket Server failed.");
  }
}

void loop() {
  if (WiFi.status() == WL_CONNECTED) {
    if (client.available()) {
      client.poll();
    } else {
      unsigned long now = millis();
      if (now - last_reconnect_attempt > 5000) {
        last_reconnect_attempt = now;
        Serial.println("Reconnecting to WebSocket server...");
        bool connected = client.connect(websocket_server_host, websocket_server_port, websocket_server_path);
        if (connected) {
          Serial.println("Connected to WebSocket Server!");
          // Send initial states
          client.send(digitalRead(RELAY1) == LOW ? "Relay1 ON" : "Relay1 OFF");
          client.send(digitalRead(RELAY2) == LOW ? "Relay2 ON" : "Relay2 OFF");
          client.send(digitalRead(RELAY3) == LOW ? "Relay3 ON" : "Relay3 OFF");
        } else {
          Serial.println("Connection failed!");
        }
      }
    }
  } else {
    // If WiFi disconnected, wait a bit
    delay(1000);
  }
}