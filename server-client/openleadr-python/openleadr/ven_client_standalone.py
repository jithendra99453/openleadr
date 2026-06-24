#!/usr/bin/env python3
"""
Standalone OpenADR VEN Client with EMS-MQTT
Save this as ven_client_standalone.py (NOT client.py)
"""

import asyncio
import logging
import os
import sys
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

# Add parent directory to path for OpenADR imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import OpenADR client from the library
try:
    from openleadr import OpenADRClient, utils, objects
except ImportError:
    print("Error: Cannot import OpenADRClient. Make sure openleadr is installed.")
    print("Run: pip install openleadr")
    sys.exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("ven_client")

# ============================================================================
# MQTT Bridge (Simulation Mode)
# ============================================================================

class MQTTBridge:
    """MQTT bridge for communicating with load controllers"""
    
    def __init__(self, broker_host="localhost", broker_port=1883, use_simulation=True):
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.use_simulation = use_simulation
        self.simulated_loads = {}
        
    async def connect(self):
        """Connect to MQTT broker"""
        if self.use_simulation:
            logger.info("MQTT running in SIMULATION mode")
            return True
        try:
            import aiomqtt
            self.client = aiomqtt.Client(
                hostname=self.broker_host,
                port=self.broker_port,
                client_id="ems_controller"
            )
            await self.client.__aenter__()
            logger.info(f"MQTT connected to {self.broker_host}:{self.broker_port}")
            return True
        except Exception as e:
            logger.warning(f"MQTT connection failed: {e}. Running in simulation mode.")
            self.use_simulation = True
            return True
            
    async def disconnect(self):
        """Disconnect from MQTT broker"""
        if not self.use_simulation and hasattr(self, 'client'):
            await self.client.__aexit__(None, None, None)
            
    async def publish_load_command(self, load_id: str, command: str, value: float, duration: int = None):
        """Publish command to specific load"""
        payload = {
            "command": command,
            "value": value,
            "duration": duration,
            "timestamp": datetime.now().isoformat()
        }
        
        if self.use_simulation:
            logger.info(f"📡 MQTT SIM: {load_id} -> {command}={value}W for {duration}s")
            if load_id in self.simulated_loads:
                self.simulated_loads[load_id]['current_power_w'] = value
                if duration:
                    self.simulated_loads[load_id]['recovery_time'] = datetime.now().timestamp() + duration
        else:
            topic = f"loads/{load_id}/command"
            await self.client.publish(topic, json.dumps(payload))
            
    def register_simulated_load(self, load_id: str, max_power_w: int, min_power_w: int):
        """Register a simulated load"""
        self.simulated_loads[load_id] = {
            'id': load_id,
            'max_power_w': max_power_w,
            'min_power_w': min_power_w,
            'current_power_w': max_power_w,
            'recovery_time': None
        }
        
    async def update_simulated_loads(self):
        """Update simulated loads"""
        if not self.use_simulation:
            return
        now = datetime.now().timestamp()
        for load_id, load in self.simulated_loads.items():
            if load.get('recovery_time') and now >= load['recovery_time']:
                load['current_power_w'] = load['max_power_w']
                load['recovery_time'] = None
                logger.info(f"🔄 SIM: {load_id} recovered")

# ============================================================================
# Load Models
# ============================================================================

class LoadType(Enum):
    HVAC = "hvac"
    LIGHTING = "lighting"
    EV_CHARGER = "ev_charger"
    REFRIGERATION = "refrigeration"

@dataclass
class Load:
    """Load controlled by EMS"""
    id: str
    name: str
    type: LoadType
    current_power_w: int
    max_power_w: int
    min_power_w: int
    priority: int
    reduction_capacity_w: int = 0
    is_controllable: bool = True
    
    def __post_init__(self):
        if self.reduction_capacity_w == 0:
            self.reduction_capacity_w = self.current_power_w - self.min_power_w

# ============================================================================
# EMS Controller
# ============================================================================

class EMSController:
    """EMS Controller that manages loads"""
    
    def __init__(self, use_simulation=True):
        self.mqtt = MQTTBridge(use_simulation=use_simulation)
        self.loads: Dict[str, Load] = {}
        self.active_reduction = None
        self.total_load_w = 0
        self.update_task = None
        
    async def start(self):
        """Start EMS controller"""
        await self.mqtt.connect()
        self.update_task = asyncio.create_task(self._background_update())
        logger.info("EMS Controller started")
        
    async def stop(self):
        """Stop EMS controller"""
        if self.update_task:
            self.update_task.cancel()
        await self.mqtt.disconnect()
        
    def add_load(self, load: Load):
        """Add a load to EMS"""
        self.loads[load.id] = load
        if self.mqtt.use_simulation:
            self.mqtt.register_simulated_load(load.id, load.max_power_w, load.min_power_w)
        self._update_total_load()
        logger.info(f"Added load: {load.name} ({load.current_power_w/1000:.1f}kW, priority {load.priority})")
        
    def add_multiple_loads(self, loads: List[Load]):
        for load in loads:
            self.add_load(load)
            
    def _update_total_load(self):
        self.total_load_w = sum(l.current_power_w for l in self.loads.values())
        
    async def _background_update(self):
        while True:
            await asyncio.sleep(1)
            await self.mqtt.update_simulated_loads()
            if self.mqtt.use_simulation:
                for load_id, load_data in self.mqtt.simulated_loads.items():
                    if load_id in self.loads:
                        self.loads[load_id].current_power_w = load_data['current_power_w']
                self._update_total_load()
                
    def calculate_reduction_plan(self, target_w: int) -> List[tuple]:
        """Calculate which loads to reduce"""
        reducible = [l for l in self.loads.values() if l.is_controllable]
        reducible.sort(key=lambda x: -x.priority)
        
        remaining = target_w
        plan = []
        
        for load in reducible:
            max_reduce = min(load.reduction_capacity_w, load.current_power_w - load.min_power_w)
            if max_reduce <= 0:
                continue
            reduction = min(max_reduce, remaining)
            if reduction > 0:
                plan.append((load, reduction))
                remaining -= reduction
            if remaining <= 0:
                break
                
        return plan, remaining
        
    async def execute_reduction(self, target_w: int, duration_min: int, event_id: str = None):
        """Execute load reduction"""
        logger.info(f"\n{'='*60}")
        logger.info(f"📢 EXECUTING LOAD REDUCTION")
        logger.info(f"  Target: {target_w}W ({target_w/1000:.1f}kW)")
        logger.info(f"  Duration: {duration_min} minutes")
        logger.info(f"{'='*60}")
        
        plan, shortfall = self.calculate_reduction_plan(target_w)
        
        if not plan:
            logger.warning("Cannot achieve any reduction!")
            return False, 0
            
        actual = 0
        for load, reduction in plan:
            target = load.current_power_w - reduction
            await self.mqtt.publish_load_command(load.id, "SET_POWER", target, duration_min * 60)
            actual += reduction
            logger.info(f"  → {load.name}: reduced by {reduction}W ({reduction/load.current_power_w*100:.0f}%)")
            
        if shortfall > 0:
            logger.warning(f"  ⚠️ Shortfall: {shortfall}W")
            
        self.active_reduction = {
            'event_id': event_id,
            'target_w': target_w,
            'actual_w': actual,
            'duration_min': duration_min
        }
        
        logger.info(f"  ✅ Total Reduction: {actual}W ({actual/1000:.1f}kW)")
        return True, actual
        
    async def recover_loads(self):
        """Recover all loads"""
        logger.info(f"\n🔄 RECOVERING LOADS")
        for load in self.loads.values():
            if load.current_power_w < load.max_power_w:
                await self.mqtt.publish_load_command(load.id, "SET_POWER", load.max_power_w, 0)
                load.current_power_w = load.max_power_w
        self.active_reduction = None
        logger.info("  ✅ All loads recovered")
        
    def get_status(self) -> Dict:
        return {
            "total_load_kw": round(self.total_load_w / 1000, 2),
            "active_reduction": self.active_reduction,
            "loads": [
                {
                    "name": l.name,
                    "power_kw": round(l.current_power_w / 1000, 2),
                    "priority": l.priority
                }
                for l in self.loads.values()
            ]
        }

# ============================================================================
# VEN Client
# ============================================================================

class VENClient:
    """OpenADR VEN Client with EMS"""
    
    def __init__(self, vtn_url="https://127.0.0.1:8443/OpenADR2/Simple/2.0b",
                 ven_name="TestVEN",
                 cert_path=None, key_path=None, ca_path=None,
                 use_simulation=True):
        
        self.vtn_url = vtn_url
        self.ven_name = ven_name
        
        # Initialize EMS
        self.ems = EMSController(use_simulation=use_simulation)
        
        # Create OpenADR client
        self.client = OpenADRClient(
            ven_name=ven_name,
            vtn_url=vtn_url,
            debug=False,
            cert=cert_path,
            key=key_path,
            ca_file=ca_path,
            check_hostname=False,
            disable_signature=False
        )
        
    def setup_loads(self):
        """Setup default loads"""
        loads = [
            Load("hvac_1", "HVAC System", LoadType.HVAC, 35000, 50000, 10000, 8),
            Load("lighting_1", "Building Lighting", LoadType.LIGHTING, 25000, 30000, 5000, 7),
            Load("ev_1", "EV Chargers", LoadType.EV_CHARGER, 15000, 22000, 0, 9),
            Load("fridge_1", "Cold Storage", LoadType.REFRIGERATION, 12000, 15000, 8000, 3),
        ]
        self.ems.add_multiple_loads(loads)
        
    async def on_event(self, event):
        """Handle OpenADR event"""
        event_id = event.get('event_descriptor', {}).get('event_id', 'unknown')
        
        print(f"\n{'='*60}")
        print(f"📨 EVENT RECEIVED: {event_id}")
        
        # Get reduction target
        intervals = event.get('event_signals', [{}])[0].get('intervals', [])
        target_kw = 0
        duration_min = 30
        
        for interval in intervals:
            payload = interval.get('signal_payload', 0)
            if payload > 0:
                target_kw = payload
                duration = interval.get('duration')
                if duration and hasattr(duration, 'total_seconds'):
                    duration_min = int(duration.total_seconds() / 60)
                break
                
        print(f"  Target: {target_kw} kW")
        print(f"  Duration: {duration_min} minutes")
        print(f"{'='*60}\n")
        
        if target_kw > 0:
            await self.ems.execute_reduction(int(target_kw * 1000), duration_min, event_id)
            return 'optIn'
        return 'optOut'
        
    async def on_update_event(self, event):
        """Handle event updates"""
        status = event.get('event_descriptor', {}).get('event_status')
        if status == 'cancelled':
            await self.ems.recover_loads()
        return 'optIn'
        
    async def run(self):
        """Run the client"""
        print("\n" + "="*60)
        print("STARTING VEN CLIENT WITH EMS")
        print("="*60)
        print(f"VTN URL: {self.vtn_url}")
        print(f"VEN Name: {self.ven_name}")
        print("="*60 + "\n")
        
        # Setup
        self.setup_loads()
        await self.ems.start()
        
        # Show initial status
        status = self.ems.get_status()
        print(f"Initial Total Load: {status['total_load_kw']} kW")
        
        # Add handlers
        self.client.add_handler('on_event', self.on_event)
        self.client.add_handler('on_update_event', self.on_update_event)
        
        # Run client
        await self.client.run()
        
        # Status loop
        try:
            while True:
                await asyncio.sleep(10)
                status = self.ems.get_status()
                if status['active_reduction']:
                    red = status['active_reduction']
                    print(f"📊 EMS: {status['total_load_kw']}kW total, "
                          f"Reducing: {red['actual_w']/1000:.1f}/{red['target_w']/1000:.1f}kW")
        except KeyboardInterrupt:
            await self.ems.stop()
            await self.client.stop()

# ============================================================================
# Main
# ============================================================================

def get_cert_path(filename):
    """Get certificate path"""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, 'certificates', filename)

async def main():
    """Main entry point"""
    
    # Check for certificates (optional for simulation)
    cert_path = get_cert_path("dummy_ven.crt")
    key_path = get_cert_path("dummy_ven.key")
    ca_path = get_cert_path("dummy_ca.crt")
    
    if not os.path.exists(cert_path):
        print("Certificate files not found. Running in simulation mode...")
        cert_path = None
        key_path = None
        ca_path = None
    
    # Create and run client
    client = VENClient(
        vtn_url="https://127.0.0.1:8443/OpenADR2/Simple/2.0b",
        ven_name="TestVEN",
        cert_path=cert_path,
        key_path=key_path,
        ca_path=ca_path,
        use_simulation=True  # Use simulation mode for testing
    )
    
    await client.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n👋 VEN Client stopped")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()