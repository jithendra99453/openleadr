# vtn_server_ems.py
"""
VTN Server with Scheduled Load Reduction Events
"""

import asyncio
import logging
import sys
import os
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openleadr import objects, enums, utils
from server import OpenADRServer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vtn_server")

def get_cert_path(filename):
    """Get certificate path"""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, 'certificates', filename)

class VTNServerWithEMS:
    """VTN Server with EMS scheduling"""
    
    def __init__(self):
        self.server = None
        self.scheduled_events = []
        
    async def create_server(self):
        """Create and configure VTN server"""
        
        # VEN lookup function
        async def ven_lookup(ven_id):
            print(f"\n[VTN] Looking up VEN: {ven_id}")
            if ven_id == "TestVEN":
                return {
                    "ven_name": "TestVEN",
                    "ven_id": "TestVEN",
                    "fingerprint": "A3:C6:86:8C:8A:E3:DC:69:C5:E0:00:00:00:00:00:00:00:00:00:00",
                    "registration_id": "reg_TestVEN_001"
                }
            return None
            
        # Create server
        self.server = OpenADRServer(
            vtn_id="MyVTN",
            http_host="0.0.0.0",
            http_port=8443,
            http_cert=get_cert_path("dummy_vtn.crt"),
            http_key=get_cert_path("dummy_vtn.key"),
            http_ca_file=get_cert_path("dummy_ca.crt"),
            cert=get_cert_path("dummy_vtn.crt"),
            key=get_cert_path("dummy_vtn.key"),
            ven_lookup=ven_lookup,
            verify_message_signatures=False
        )
        
        # Add handlers
        async def on_create_party_registration(registration_info):
            ven_name = registration_info.get('ven_name')
            ven_id = registration_info.get('ven_id', ven_name)
            reg_id = utils.generate_id()
            
            print(f"\n{'='*60}")
            print(f"[VTN] NEW REGISTRATION")
            print(f"  VEN: {ven_name} ({ven_id})")
            print(f"  Registration ID: {reg_id}")
            print(f"{'='*60}\n")
            
            return ven_id, reg_id
            
        async def on_created_event(ven_id, event_id, opt_type):
            print(f"\n[VTN] Response from {ven_id}:")
            print(f"  Event: {event_id}")
            print(f"  Response: {opt_type}")
            return True
            
        self.server.add_handler('on_create_party_registration', on_create_party_registration)
        self.server.add_handler('on_created_event', on_created_event)
        
        return self.server
        
    def schedule_load_reduction(self, ven_id: str, reduction_kw: float, 
                                duration_minutes: int, delay_seconds: int = 5,
                                signal_name: str = "LOAD_REDUCTION"):
        """Schedule a load reduction event"""
        
        start_time = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
        
        intervals = [
            objects.Interval(
                dtstart=start_time,
                duration=timedelta(minutes=duration_minutes),
                signal_payload=reduction_kw  # kW reduction
            )
        ]
        
        event_id = self.server.add_event(
            ven_id=ven_id,
            signal_name=signal_name,
            signal_type="level",  # Valid OpenADR signal type
            intervals=intervals,
            response_required="always",
            market_context="oadr://LoadManagement"
        )
        
        print(f"\n📅 SCHEDULED REDUCTION EVENT")
        print(f"  Event ID: {event_id}")
        print(f"  VEN: {ven_id}")
        print(f"  Reduction: {reduction_kw} kW")
        print(f"  Duration: {duration_minutes} minutes")
        print(f"  Start: {utils.datetimeformat(start_time)}")
        
        self.scheduled_events.append({
            'event_id': event_id,
            'ven_id': ven_id,
            'reduction_kw': reduction_kw,
            'duration': duration_minutes,
            'start_time': start_time
        })
        
        return event_id
        
    def schedule_peak_time_reductions(self, ven_id: str):
        """Schedule reductions during peak hours"""
        now = datetime.now(timezone.utc)
        events = []
        
        # Schedule reductions at specific times
        schedules = [
            {"hour": 9, "minute": 0, "reduction_kw": 30, "duration": 30},   # 9:00 AM
            {"hour": 13, "minute": 0, "reduction_kw": 40, "duration": 60},  # 1:00 PM
            {"hour": 17, "minute": 0, "reduction_kw": 50, "duration": 45},  # 5:00 PM
            {"hour": 20, "minute": 0, "reduction_kw": 25, "duration": 120}  # 8:00 PM
        ]
        
        for schedule in schedules:
            target_time = now.replace(hour=schedule['hour'], minute=schedule['minute'], second=0)
            if target_time <= now:
                target_time += timedelta(days=1)
                
            delay_seconds = (target_time - now).total_seconds()
            
            if delay_seconds > 0:
                event_id = self.schedule_load_reduction(
                    ven_id=ven_id,
                    reduction_kw=schedule['reduction_kw'],
                    duration_minutes=schedule['duration'],
                    delay_seconds=int(delay_seconds)
                )
                events.append(event_id)
                
        return events
        
    async def run(self):
        """Run the VTN server"""
        await self.create_server()
        
        # Start server
        await self.server.run()
        
        # Wait for VEN to register
        await asyncio.sleep(5)
        
        print("\n" + "🎯"*35)
        print("SCHEDULING LOAD REDUCTION EVENTS".center(70))
        print("🎯"*35)
        
        # Send immediate test reduction
        self.schedule_load_reduction(
            ven_id="TestVEN",
            reduction_kw=30,  # 30 kW reduction
            duration_minutes=3,  # 3 minutes
            delay_seconds=5
        )
        
        # Send second reduction
        await asyncio.sleep(10)
        
        self.schedule_load_reduction(
            ven_id="TestVEN",
            reduction_kw=50,  # 50 kW reduction
            duration_minutes=4,  # 4 minutes
            delay_seconds=3
        )
        
        # Send third reduction
        await asyncio.sleep(15)
        
        self.schedule_load_reduction(
            ven_id="TestVEN",
            reduction_kw=70,  # 70 kW reduction
            duration_minutes=5,  # 5 minutes
            delay_seconds=2
        )
        
        print(f"\n✅ Scheduled {len(self.scheduled_events)} reduction events\n")
        
        # Keep running
        while True:
            await asyncio.sleep(1)