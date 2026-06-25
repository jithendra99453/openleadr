import asyncio
import json
import logging
import os
import random
from datetime import datetime
from typing import Dict, Set
from urllib.parse import parse_qs

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
import websockets

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI()

# Root directory of the app
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_FILE = os.path.join(BASE_DIR, "index.html")
REPLAY_BUFFER_FILE = os.path.join(BASE_DIR, "replay_buffer.jsonl")

# ESP8266 WebSocket Server Configuration (ESP IP can be set via env var ESP_IP)
ESP_IP = os.environ.get("ESP_IP", "192.168.4.1")  # Defaults to ESP IP, change as needed
ESP_PORT = int(os.environ.get("ESP_PORT", "81"))

# Client ID → Device mapping (access control)
CLIENT_DEVICE_MAP = {
    1: "lights",
    2: "lights2",
    3: "lights3"
}

# State Management
relay_states = {
    "lights": 1,
    "lights2": 1,
    "lights3": 1,
    "fans": 1,
    "ac": 1
}

sensor_state = {
    "voltage": 230.0,
    "current": 0.0,
    "energy": 0.0,
    "temperature": 25.0,
    "humidity": 55.0,
    "occupancy": 0
}

# WebSocket connections tracking
dashboard_connections: Set[WebSocket] = set()
esp_connection = None
esp_connected_status = False
esp_format = "text"  # Can be "text" (legacy) or "json" (new)

# Mutex to protect state updates
state_lock = asyncio.Lock()

def get_current_time_str():
    now = datetime.now()
    return now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S")

def calculate_sensor_simulation(dt: float):
    """Simulate sensor values based on relay states and time step dt in seconds."""
    global sensor_state, relay_states
    
    # Current simulation
    # Base current is 0.0A (starts at 0.0 when no relays are ON)
    current = 0.0
    if relay_states["lights"] == 1:
        current += 0.25
    if relay_states["lights2"] == 1 or relay_states.get("fans") == 1:
        current += 0.25
    if relay_states["lights3"] == 1 or relay_states.get("ac") == 1:
        current += 0.25
        
    sensor_state["current"] = round(current, 2)
    
    # Voltage simulation (slight random fluctuations around 230V)
    sensor_state["voltage"] = round(230.0 + random.uniform(-1.5, 1.5), 1) if current > 0.0 else 0.0
    
    # Energy simulation (kWh)
    power = sensor_state["voltage"] * sensor_state["current"]
    hours = dt / 3600.0
    kwh = (power / 1000.0) * hours
    sensor_state["energy"] = round(sensor_state["energy"] + kwh, 5)

    # Temperature simulation:
    # If AC is ON (lights3 or ac == 1), temp falls slowly towards 18°C.
    # Otherwise, it rises slowly towards 27°C (ambient).
    ac_on = (relay_states.get("ac") == 1) or (relay_states.get("lights3") == 1)
    current_temp = sensor_state.get("temperature", 25.0)
    if ac_on:
        target_temp = 18.0
        current_temp += (target_temp - current_temp) * 0.05
    else:
        target_temp = 27.0
        current_temp += (target_temp - current_temp) * 0.02
    current_temp += random.uniform(-0.1, 0.1)
    sensor_state["temperature"] = round(current_temp, 1)

    # Humidity simulation:
    # If AC is ON, humidity falls towards 45%. Otherwise, rises towards 60%.
    current_humidity = sensor_state.get("humidity", 55.0)
    if ac_on:
        target_humidity = 45.0
        current_humidity += (target_humidity - current_humidity) * 0.04
    else:
        target_humidity = 60.0
        current_humidity += (target_humidity - current_humidity) * 0.02
    current_humidity += random.uniform(-0.2, 0.2)
    sensor_state["humidity"] = round(current_humidity, 1)

    # Occupancy simulation:
    # 5% chance of state change every interval
    if random.random() < 0.05:
        sensor_state["occupancy"] = 1 if sensor_state["occupancy"] == 0 else 0


def log_replay_buffer_transition(action_device: str, action_state: int, state_before: dict, state_after: dict):
    """Log transitions to a local JSONL file for future DQN training."""
    transition = {
        "timestamp": datetime.now().isoformat(),
        "action": {"device": action_device, "state": action_state},
        "state_before": state_before.copy(),
        "state_after": state_after.copy()
    }
    try:
        with open(REPLAY_BUFFER_FILE, "a") as f:
            f.write(json.dumps(transition) + "\n")
        logger.info(f"Logged transition to replay buffer: {action_device} -> {action_state}")
    except Exception as e:
        logger.error(f"Failed to log replay buffer: {e}")

async def broadcast_to_dashboards(message: dict):
    """Send JSON message to all connected dashboards."""
    if not dashboard_connections:
        return
    payload = json.dumps(message)
    # Create list of tasks to send concurrently
    tasks = []
    for ws in list(dashboard_connections):
        try:
            tasks.append(asyncio.create_task(ws.send_text(payload)))
        except Exception as e:
            logger.error(f"Error preparing broadcast: {e}")
            dashboard_connections.discard(ws)
            
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

async def sensor_simulation_loop():
    """Background task to simulate sensors and broadcast to dashboards."""
    interval = 2.0  # seconds
    while True:
        try:
            await asyncio.sleep(interval)
            async with state_lock:
                calculate_sensor_simulation(interval)
                date_str, time_str = get_current_time_str()
                
                # Build data message
                payload = {
                    "type": "sensor_data",
                    "date": date_str,
                    "time": time_str,
                    "voltage": sensor_state["voltage"],
                    "current": sensor_state["current"],
                    "energy": sensor_state["energy"],
                    "temperature": sensor_state["temperature"],
                    "humidity": sensor_state["humidity"],
                    "occupancy": sensor_state["occupancy"]
                }
                
            await broadcast_to_dashboards(payload)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in sensor simulation loop: {e}")

@app.websocket("/ws/esp")
async def websocket_esp(websocket: WebSocket):
    global esp_connection, esp_connected_status, esp_format
    await websocket.accept()
    esp_connection = websocket
    esp_connected_status = True
    logger.info("✅ ESP8266 connected to FastAPI WebSocket server!")
    
    # Notify all dashboards
    await broadcast_to_dashboards({
        "type": "esp_status",
        "connected": True
    })
    
    try:
        async for message in websocket.iter_text():
            msg_str = str(message).strip()
            logger.info(f"Received from ESP: {msg_str}")
            
            device = None
            state_val = None
            
            # Attempt to parse as JSON first (from relaycode.ino)
            try:
                data = json.loads(msg_str)
                esp_format = "json"
                device = data.get("device")
                state_val = int(data.get("state", 0))
            except json.JSONDecodeError:
                # Fallback to plain text parsing (from relaycode2.ino)
                if "Relay1" in msg_str:
                    device = "lights"
                    state_val = 1 if "ON" in msg_str else 0
                elif "Relay2" in msg_str:
                    device = "lights2"
                    state_val = 1 if "ON" in msg_str else 0
                elif "Relay3" in msg_str:
                    device = "lights3"
                    state_val = 1 if "ON" in msg_str else 0
                
            if device is not None and state_val is not None:
                async with state_lock:
                    state_before = sensor_state.copy()
                    state_before.update({
                        "relay_lights": relay_states["lights"],
                        "relay_lights2": relay_states["lights2"],
                        "relay_lights3": relay_states["lights3"],
                        "relay_fans": relay_states.get("fans", 1),
                        "relay_ac": relay_states.get("ac", 1)
                    })
                    
                    # Update server's internal relay state
                    relay_states[device] = state_val
                    
                    # Sync aliases
                    if device == "fans":
                        relay_states["lights2"] = state_val
                    elif device == "ac":
                        relay_states["lights3"] = state_val
                    elif device == "lights2":
                        relay_states["fans"] = state_val
                    elif device == "lights3":
                        relay_states["ac"] = state_val
                    
                    state_after = sensor_state.copy()
                    state_after.update({
                        "relay_lights": relay_states["lights"],
                        "relay_lights2": relay_states["lights2"],
                        "relay_lights3": relay_states["lights3"],
                        "relay_fans": relay_states.get("fans", 1),
                        "relay_ac": relay_states.get("ac", 1)
                    })
                    
                logger.info(f"ESP Confirmed: {device} -> {state_val}")
                log_replay_buffer_transition(device, state_val, state_before, state_after)
                
                # Send update to all dashboards
                await broadcast_to_dashboards({
                    "type": "relay_update",
                    "device": device,
                    "state": state_val
                })
                
                # Broadcast alias update
                alias_device = None
                if device == "fans":
                    alias_device = "lights2"
                elif device == "ac":
                    alias_device = "lights3"
                elif device == "lights2":
                    alias_device = "fans"
                elif device == "lights3":
                    alias_device = "ac"
                    
                if alias_device:
                    await broadcast_to_dashboards({
                        "type": "relay_update",
                        "device": alias_device,
                        "state": state_val
                    })
    except WebSocketDisconnect:
        logger.warning("ESP8266 disconnected.")
    except Exception as e:
        logger.error(f"Error in ESP WebSocket: {e}")
    finally:
        esp_connection = None
        esp_connected_status = False
        logger.warning("Disconnected from ESP8266 WebSocket.")
        await broadcast_to_dashboards({
            "type": "esp_status",
            "connected": False
        })

@app.on_event("startup")
async def startup_event():
    # Start the simulation loop
    asyncio.create_task(sensor_simulation_loop())
    logger.info("Startup complete: Sensor simulation loop started.")

@app.get("/")
async def get_index():
    ppo_path = os.path.join(BASE_DIR, "ppoWeb (1).html")
    if os.path.exists(ppo_path):
        return FileResponse(ppo_path, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    if os.path.exists(INDEX_FILE):
        return FileResponse(INDEX_FILE, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    return HTMLResponse("index.html / ppoWeb (1).html not found on server.", status_code=404)

@app.get("/dashboard")
async def get_dashboard():
    path = os.path.join(BASE_DIR, "ppoWeb (1).html")
    if os.path.exists(path):
        return FileResponse(path, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    return HTMLResponse("ppoWeb (1).html not found.", status_code=404)

# Serve the 3 client pages
@app.get("/client1")
async def get_client1():
    path = os.path.join(BASE_DIR, "client1_lights.html")
    if os.path.exists(path):
        return FileResponse(path, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    return HTMLResponse("client1_lights.html not found.", status_code=404)

@app.get("/client2")
async def get_client2():
    path = os.path.join(BASE_DIR, "client2_fans.html")
    if os.path.exists(path):
        return FileResponse(path, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    return HTMLResponse("client2_fans.html not found.", status_code=404)

@app.get("/client3")
async def get_client3():
    path = os.path.join(BASE_DIR, "client3_ac.html")
    if os.path.exists(path):
        return FileResponse(path, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    return HTMLResponse("client3_ac.html not found.", status_code=404)

@app.get("/api/set_esp_ip")
async def set_esp_ip(ip: str = Query(..., description="The IP address of the ESP8266")):
    global ESP_IP, esp_connection
    cleaned_ip = ip.strip()
    if cleaned_ip:
        ESP_IP = cleaned_ip
        logger.info(f"ESP IP dynamically updated to: {ESP_IP}")
        if esp_connection:
            logger.info("Closing active ESP connection to trigger immediate reconnect...")
            await esp_connection.close()
        return {"status": "ok", "esp_ip": ESP_IP}
    return {"status": "error", "message": "Invalid IP address"}

@app.websocket("/ws/dashboard")
async def websocket_dashboard(websocket: WebSocket):
    # Parse client_id from query params
    query_string = websocket.scope.get("query_string", b"").decode()
    params = parse_qs(query_string)
    client_id_list = params.get("client_id", [])
    
    if client_id_list:
        try:
            client_id = int(client_id_list[0])
        except (ValueError, IndexError):
            client_id = None
    else:
        client_id = None
    
    # Determine allowed device for this client
    allowed_device = CLIENT_DEVICE_MAP.get(client_id) if client_id else None
    
    await websocket.accept()
    dashboard_connections.add(websocket)
    
    if client_id and allowed_device:
        logger.info(f"Dashboard client {client_id} connected (controls: {allowed_device}). Total: {len(dashboard_connections)}")
    else:
        logger.info(f"Dashboard client connected (no client_id, full access). Total: {len(dashboard_connections)}")
    
    try:
        # Send initial status
        async with state_lock:
            date_str, time_str = get_current_time_str()
            # Send initial ESP connection state
            esp_connected = esp_connected_status
            await websocket.send_json({
                "type": "esp_status",
                "connected": esp_connected
            })
            
            # Send initial states
            await websocket.send_json({
                "type": "initial_state",
                "states": relay_states,
                "esp_ip": ESP_IP,
                "sensors": {
                    "date": date_str,
                    "time": time_str,
                    "voltage": sensor_state["voltage"],
                    "current": sensor_state["current"],
                    "energy": sensor_state["energy"],
                    "temperature": sensor_state["temperature"],
                    "humidity": sensor_state["humidity"],
                    "occupancy": sensor_state["occupancy"]
                }
            })
            
        while True:
            # Receive command from Dashboard
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "command":
                    device = msg.get("device")
                    state = int(msg.get("state", 0))
                    
                    # ACCESS CONTROL: enforce client_id -> device mapping
                    if allowed_device and device != allowed_device:
                        logger.warning(f"ACCESS DENIED: Client {client_id} tried to control '{device}' (allowed: '{allowed_device}')")
                        await websocket.send_json({
                            "type": "error",
                            "message": f"Access denied: Client {client_id} can only control {allowed_device}"
                        })
                        continue
                    
                    if device in relay_states:
                        # Log user action
                        date_str, time_str = get_current_time_str()
                        logger.info(f"[{time_str}] Client {client_id or 'legacy'} Action: {device.upper()} -> {state}")
                        logger.info(f"Current State: Voltage={sensor_state['voltage']:.1f}V, Current={sensor_state['current']:.2f}A, Energy={sensor_state['energy']:.5f}kWh")
                        
                        # Check if ESP is connected
                        if esp_connected_status and esp_connection:
                            if esp_format == "json":
                                # JSON format for new relaycode.ino
                                payload = json.dumps({
                                    "device": device,
                                    "state": state
                                })
                                await esp_connection.send_text(payload)
                                logger.info(f"Forwarded JSON command to ESP: {payload}")
                            else:
                                # Legacy Text format for relaycode2.ino
                                cmd_str = ""
                                if device == "lights":
                                    cmd_str = "R1_ON" if state == 1 else "R1_OFF"
                                elif device == "lights2" or device == "fans":
                                    cmd_str = "R2_ON" if state == 1 else "R2_OFF"
                                elif device == "lights3" or device == "ac":
                                    cmd_str = "R3_ON" if state == 1 else "R3_OFF"
                                    
                                if cmd_str:
                                    await esp_connection.send_text(cmd_str)
                                    logger.info(f"Forwarded text command to ESP: {cmd_str}")
                        else:
                            logger.warning("ESP8266 is not connected! Simulating response locally (Simulator Fallback).")
                            
                            async with state_lock:
                                state_before = sensor_state.copy()
                                state_before.update({
                                    "relay_lights": relay_states["lights"],
                                    "relay_lights2": relay_states["lights2"],
                                    "relay_lights3": relay_states["lights3"],
                                    "relay_fans": relay_states.get("fans", 1),
                                    "relay_ac": relay_states.get("ac", 1)
                                })
                                
                                # Update server's internal relay state
                                relay_states[device] = state
                                
                                # Sync aliases
                                if device == "fans":
                                    relay_states["lights2"] = state
                                elif device == "ac":
                                    relay_states["lights3"] = state
                                elif device == "lights2":
                                    relay_states["fans"] = state
                                elif device == "lights3":
                                    relay_states["ac"] = state
                                
                                state_after = sensor_state.copy()
                                state_after.update({
                                    "relay_lights": relay_states["lights"],
                                    "relay_lights2": relay_states["lights2"],
                                    "relay_lights3": relay_states["lights3"],
                                    "relay_fans": relay_states.get("fans", 1),
                                    "relay_ac": relay_states.get("ac", 1)
                                })
                                
                            # Log transition to replay buffer
                            log_replay_buffer_transition(device, state, state_before, state_after)
                            
                            # Broadcast confirmed update back to dashboards
                            await broadcast_to_dashboards({
                                "type": "relay_update",
                                "device": device,
                                "state": state
                             })
                            
                            # Broadcast alias update
                            alias_device = None
                            if device == "fans":
                                alias_device = "lights2"
                            elif device == "ac":
                                alias_device = "lights3"
                            elif device == "lights2":
                                alias_device = "fans"
                            elif device == "lights3":
                                alias_device = "ac"
                                
                            if alias_device:
                                await broadcast_to_dashboards({
                                    "type": "relay_update",
                                    "device": alias_device,
                                    "state": state
                                })
            except json.JSONDecodeError:
                logger.error(f"Invalid JSON received from dashboard: {data}")
            except Exception as e:
                logger.error(f"Error handling dashboard message: {e}")
                
    except WebSocketDisconnect:
        dashboard_connections.discard(websocket)
        logger.info(f"Dashboard client {client_id or 'legacy'} disconnected. Total: {len(dashboard_connections)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
