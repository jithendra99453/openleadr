# run_ems_system.py
#!/usr/bin/env python3
"""
Complete OpenADR + EMS + MQTT System Runner
"""

import asyncio
import sys
import signal
import logging

from vtn_server_ems import VTNServerWithEMS
from ven_client_ems import VENClientWithEMS

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("main")

class OpenADRSystem:
    """Complete OpenADR system with EMS"""
    
    def __init__(self):
        self.vtn = None
        self.ven = None
        self.vtn_task = None
        self.ven_task = None
        
    async def start_vtn(self):
        """Start VTN server"""
        logger.info("Starting VTN Server...")
        self.vtn = VTNServerWithEMS()
        await self.vtn.run()
        
    async def start_ven(self):
        """Start VEN client"""
        logger.info("Starting VEN Client with EMS...")
        
        # Paths to certificates
        import os
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        
        self.ven = VENClientWithEMS(
            vtn_url="https://127.0.0.1:8443/OpenADR2/Simple/2.0b",
            cert_path=os.path.join(base, 'certificates', 'dummy_ven.crt'),
            key_path=os.path.join(base, 'certificates', 'dummy_ven.key'),
            ca_path=os.path.join(base, 'certificates', 'dummy_ca.crt'),
            mqtt_broker="localhost",
            mqtt_port=1883
        )
        
        await self.ven.run()
        
    async def run(self, mode="both"):
        """Run the system"""
        
        print("""
╔══════════════════════════════════════════════════════════════════════╗
║              OpenADR + EMS + MQTT Load Control System               ║
╚══════════════════════════════════════════════════════════════════════╝

Architecture:
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│   VTN    │────│   VEN    │────│   EMS    │────│   MQTT   │
│  Server  │    │  Client  │    │Controller│    │  Bridge  │
└──────────┘    └──────────┘    └──────────┘    └──────────┘
      ↓              ↓              ↓              ↓
 OpenADR        OpenADR        Load Logic     Commands to
 Events         Events         & Priority     Loads
""")
        
        if mode == "vtn":
            await self.start_vtn()
        elif mode == "ven":
            await self.start_ven()
        else:
            # Run both
            self.vtn_task = asyncio.create_task(self.start_vtn())
            await asyncio.sleep(2)  # Wait for VTN to initialize
            self.ven_task = asyncio.create_task(self.start_ven())
            
            await asyncio.gather(self.vtn_task, self.ven_task)
            
def main():
    """Main entry point"""
    
    if len(sys.argv) > 1:
        mode = sys.argv[1]
        if mode not in ["vtn", "ven", "both"]:
            print("Usage: python run_ems_system.py [vtn|ven|both]")
            sys.exit(1)
    else:
        mode = "both"
        
    system = OpenADRSystem()
    
    try:
        asyncio.run(system.run(mode))
    except KeyboardInterrupt:
        print("\n\n🛑 System stopped by user")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        
if __name__ == "__main__":
    main()