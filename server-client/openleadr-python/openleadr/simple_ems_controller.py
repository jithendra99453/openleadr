#!/usr/bin/env python3
"""
EMS Controller for ESP8266 – Stable WebSocket Client
- Groups: Lights (Relay1&2), Fans (Relay3&4), AC (Relay5)
- Uses FastAPI WebSocket server (localhost:8000)
- Robust reconnection and keep‑alive
"""

import asyncio
import sys
import json
import websockets
from datetime import datetime
from typing import Optional, List, Tuple

# Windows fixes
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    sys.stdout.reconfigure(encoding='utf-8')


class SimpleEMSController:
    def __init__(self, ws_url="ws://127.0.0.1:8000/ws/dashboard", broker=None, port=None):
        self.ws_url = ws_url
        self.client = None
        self._running = False
        self._listen_task = None
        self._reconnect_task = None
        self.last_status = None
        self.device_registered = False

        # Corrected Relay mapping to match C++ (relaycode.ino) and Dashboard:
        # Client 1 controls Relay 1 (lights)
        # Client 2 controls Relay 2 (lights2)
        # Client 3 controls Relay 3 (lights3)
        self.loads = {
            "lights3": {
                "relay_ids": [3],
                "name": "Lights 3",
                "type": "light",
                "power_w": 200,
                "state": "ON",
                "priority": 3,
                "emoji": "💡"
            },
            "lights2": {
                "relay_ids": [2],
                "name": "Lights 2",
                "type": "light",
                "power_w": 200,
                "state": "ON",
                "priority": 2,
                "emoji": "💡"
            },
            "lights": {
                "relay_ids": [1],
                "name": "Lights 1",
                "type": "light",
                "power_w": 200,
                "state": "ON",
                "priority": 1,
                "emoji": "💡"
            }
        }
        self.relay_states = {1: True, 2: True, 3: True}
        self.active_reduction = None

    async def connect(self, timeout=5):
        print(f"\n🔌 EMS Connecting to WebSocket: {self.ws_url}")
        self._running = True
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())
        try:
            async with asyncio.timeout(timeout):
                self.client = await websockets.connect(self.ws_url)
                print(f"✅ WebSocket Connected and state synced")
                self._listen_task = asyncio.create_task(self._listen_for_messages())
                self._print_config()
                return self
        except asyncio.TimeoutError:
            print(f"⚠️ WebSocket connection TIMEOUT – simulation mode (reconnect loop active)")
            self.client = None
            return self
        except Exception as e:
            print(f"⚠️ WebSocket Connection failed: {e} – simulation mode (reconnect loop active)")
            self.client = None
            return self

    def _print_config(self):
        print("\n📋 Load Configuration (Priority Order - Higher = Shed First):")
        for load in sorted(self.loads.values(), key=lambda x: -x["priority"]):
            print(f"   {load['emoji']} {load['name']}: {load['power_w']}W (Priority {load['priority']}) - Relays: {load['relay_ids']}")

    async def disconnect(self):
        self._running = False
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        if self._reconnect_task:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
        if self.client:
            await self.client.close()
            self.client = None
            print("\n🔌 WebSocket Disconnected")

    async def _reconnect_loop(self):
        while self._running:
            await asyncio.sleep(10)
            if self.client is None or self.client.state != websockets.State.OPEN:
                print("⚠️ WebSocket connection lost, reconnecting...")
                await self._reconnect()

    async def _reconnect(self):
        try:
            if self.client:
                await self.client.close()
            self.client = await websockets.connect(self.ws_url)
            print("✅ WebSocket reconnected")
        except Exception as e:
            print(f"Reconnection failed: {e}")
            self.client = None

    async def _listen_for_messages(self):
        while self._running:
            try:
                if not self.client or self.client.state != websockets.State.OPEN:
                    await asyncio.sleep(1)
                    continue
                async for message in self.client:
                    await self._handle_message(message)
            except asyncio.CancelledError:
                raise
            except websockets.exceptions.ConnectionClosed:
                if self._running:
                    print("WebSocket listener connection closed, retrying...")
                    await asyncio.sleep(3)
            except Exception as e:
                print(f"Unexpected listener error: {e}")
                await asyncio.sleep(5)

    async def _handle_message(self, message: str):
        try:
            data = json.loads(message)
            msg_type = data.get("type")
            if msg_type == "initial_state":
                states = data.get("states", {})
                await self._update_local_states_from_dict(states)
                print(f"📊 Initial state synchronized: {states}")
            elif msg_type == "relay_update":
                device = data.get("device")
                state = data.get("state")
                if device and state is not None:
                    states = {device: state}
                    await self._update_local_states_from_dict(states)
                    print(f"📊 State update: {device} -> {state}")
            elif msg_type == "sensor_data":
                self.last_status = data
        except Exception as e:
            print(f"Message parse error: {e}")

    async def _update_local_states_from_dict(self, states: dict):
        device_to_relays = {
            "lights": [1],
            "lights2": [2],
            "lights3": [3]
        }
        for device, state in states.items():
            if device in device_to_relays:
                val = (state == 1)
                for rid in device_to_relays[device]:
                    self.relay_states[rid] = val

        for load_info in self.loads.values():
            all_on = all(self.relay_states[rid] for rid in load_info["relay_ids"])
            load_info["state"] = "ON" if all_on else "OFF"

    async def send_command(self, relay_id: int, state: str):
        # Map relay_id to device name:
        # 1 -> "lights"
        # 2 -> "lights2"
        # 3 -> "lights3"
        if relay_id == 1:
            device = "lights"
        elif relay_id == 2:
            device = "lights2"
        elif relay_id == 3:
            device = "lights3"
        else:
            print(f"⚠️ Unknown relay_id: {relay_id}", flush=True)
            return

        state_val = 1 if state == "ON" else 0
        payload = {
            "type": "command",
            "device": device,
            "state": state_val
        }
        payload_json = json.dumps(payload)

        print(f"  [DEBUG] send_command payload: {payload_json}", flush=True)

        if self.client and self.client.state == websockets.State.OPEN:
            try:
                print(f"  [DEBUG] Sending command over WebSocket...", flush=True)
                await self.client.send(payload_json)
                print(f"  📡 WebSocket → ESP8266: {device} -> {state}", flush=True)
            except Exception as e:
                print(f"  ⚠️ WebSocket send failed: {e}", flush=True)
        else:
            print(f"  🔌 SIMULATION: {device} -> {state}", flush=True)

        self.relay_states[relay_id] = (state == "ON")
        for load_info in self.loads.values():
            all_on = all(self.relay_states[rid] for rid in load_info["relay_ids"])
            load_info["state"] = "ON" if all_on else "OFF"
        await asyncio.sleep(0.2)

    async def control_group(self, group_name: str, state: bool):
        if group_name not in self.loads:
            print(f"❌ Unknown group: {group_name}", flush=True)
            return False
        load_info = self.loads[group_name]
        cmd_state = "ON" if state else "OFF"
        print(f"\n🔧 Controlling {load_info['name']} group -> {cmd_state}", flush=True)
        for relay_id in load_info["relay_ids"]:
            await self.send_command(relay_id, cmd_state)
        return True

    def decide_which_loads_to_reduce(self, reduction_kw: float, program_name: str = None, target_resource_ids: List[str] = None):
        target_watts = reduction_kw * 1000
        current_watts = sum(l["power_w"] for l in self.loads.values() if l["state"] == "ON")
        print(f"\n{'='*50}\n🧠 EMS DECISION MAKING\n{'='*50}")
        print(f"   Target: {reduction_kw} kW ({target_watts}W)")
        if program_name:
            print(f"   Program: {program_name}")
        if target_resource_ids:
            print(f"   Target Resource IDs: {target_resource_ids}")
        print(f"   Current: {current_watts/1000:.2f} kW ({current_watts}W)\n")

        # Map target resource IDs to internal load keys
        resource_to_load_key = {
            "lights3": "lights3",
            "lights2": "lights2",
            "lights": "lights",
            "lighting": "lights"
        }

        targeted_load_keys = set()
        if target_resource_ids:
            for rid in target_resource_ids:
                rid_lower = rid.lower()
                for pattern, lkey in resource_to_load_key.items():
                    if pattern in rid_lower:
                        targeted_load_keys.add(lkey)

        # Determine target load type from program name
        target_type = None
        if program_name:
            prog_lower = program_name.lower()
            if "direct load" in prog_lower or "dlc" in prog_lower or "ac" in prog_lower:
                target_type = "ac"
            elif "fan" in prog_lower:
                target_type = "fan"
            elif "light" in prog_lower:
                target_type = "light"

        available = []
        for key, load in self.loads.items():
            if load["state"] == "ON":
                # Filter by targeted load keys if they were specifically targeted
                if targeted_load_keys and key not in targeted_load_keys:
                    continue
                available.append({
                    "key": key,
                    "name": load["name"],
                    "power_w": load["power_w"],
                    "priority": load["priority"],
                    "relay_ids": load["relay_ids"],
                    "emoji": load["emoji"],
                    "type": load["type"]
                })
                print(f"      {load['emoji']} {load['name']}: {load['power_w']}W (Priority {load['priority']})")
        
        # Sort based on program target match and major load status (focus on major loads first)
        def get_load_score(load_info):
            score = 0
            # Strongly prioritize matching target resource type
            if target_type and load_info["type"] == target_type:
                score += 10
            # Prioritize major loads (>= 1000W)
            if load_info["power_w"] >= 1000:
                score += 5
            return score

        available.sort(key=lambda x: (-get_load_score(x), -x["priority"], -x["power_w"]))
        
        to_off, accumulated = [], 0
        for load in available:
            if accumulated >= target_watts:
                break
            to_off.append(load)
            accumulated += load["power_w"]
        actual_kw = accumulated / 1000
        print(f"\n   📊 DECISION RESULT:")
        if to_off:
            print(f"      Shed these to achieve {actual_kw:.2f} kW:")
            for load in to_off:
                print(f"        {load['emoji']} {load['name']} (Relays {load['relay_ids']}) - {load['power_w']}W")
        else:
            print("      No loads to shed")
        if accumulated < target_watts:
            print(f"      ⚠️ Shortfall: {(target_watts - accumulated)/1000:.2f} kW")
        print(f"{'='*50}")
        return to_off, actual_kw

    async def execute_reduction(self, reduction_kw: float, duration_minutes: int = None, program_name: str = None, target_resource_ids: List[str] = None, client_id: int = None) -> float:
        print(f"\n{'='*50}\n⚡ LOAD REDUCTION EXECUTION\n{'='*50}", flush=True)
        print(f"   Required: {reduction_kw} kW", flush=True)
        if duration_minutes:
            print(f"   Duration: {duration_minutes} minutes", flush=True)
        
        # Determine targeted load based on client_id:
        # client_id 1 -> lights (Relay 1)
        # client_id 2 -> lights2 (Relay 2)
        # client_id 3 -> lights3 (Relay 3)
        loads_to_shed = []
        if client_id == 1:
            loads_to_shed = ["lights"]
        elif client_id == 2:
            loads_to_shed = ["lights2"]
        elif client_id == 3:
            loads_to_shed = ["lights3"]
        else:
            # Default to all if no client_id or invalid
            loads_to_shed = list(self.loads.keys())
            
        print(f"\n🔧 Sending commands to turn OFF targeted relays:", flush=True)
        for load_key in loads_to_shed:
            if load_key in self.loads:
                await self.control_group(load_key, False)
                
        actual_kw = sum(self.loads[l]["power_w"] for l in loads_to_shed if l in self.loads) / 1000.0
        print(f"\n✅ TARGETED RELAYS SWITCHED OFF: {actual_kw:.2f} kW", flush=True)
        
        self.active_reduction = {
            "target_kw": reduction_kw,
            "actual_kw": actual_kw,
            "loads_off": [self.loads[l]["name"] for l in loads_to_shed if l in self.loads],
            "timestamp": datetime.now().isoformat(),
            "duration_minutes": duration_minutes,
            "program_name": program_name,
            "target_resource_ids": target_resource_ids
        }
        return actual_kw

    async def restore_loads(self):
        if self.active_reduction is None:
            print("\nℹ️ No active reduction to restore")
            return
        print(f"\n{'='*50}\n🔄 RESTORING LOADS\n{'='*50}")
        for load_name, load_info in self.loads.items():
            if load_info["state"] == "OFF":
                await self.control_group(load_name, True)
        self.active_reduction = None
        print(f"\n✅ All loads restored")

    async def get_status(self) -> dict:
        total_power_w = sum(l["power_w"] for l in self.loads.values() if l["state"] == "ON")
        return {
            "total_power_kw": total_power_w/1000,
            "total_power_w": total_power_w,
            "active_reduction": self.active_reduction,
            "loads": self.loads,
            "relay_states": self.relay_states,
            "websocket_connected": self.client is not None and self.client.state == websockets.State.OPEN
        }

if __name__ == "__main__":
    async def demo():
        ems = SimpleEMSController(ws_url="ws://127.0.0.1:8000/ws/dashboard")
        await ems.connect()
        await asyncio.sleep(10)
        await ems.disconnect()
    asyncio.run(demo())