import asyncio
import logging
import os
import ssl
import sys
from datetime import datetime, timedelta, timezone
from functools import partial

from aiohttp import web

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openleadr import objects, enums, utils
from openleadr.messaging import create_message
from openleadr.service import (
    EventService,
    PollService,
    RegistrationService,
    ReportService,
    VTNService
)


logger = logging.getLogger("openleadr")
logging.basicConfig(level=logging.INFO)


class OpenADRServer:
    _MAP = {
        'on_created_event': 'event_service',
        'on_request_event': 'event_service',
        'on_register_report': 'report_service',
        'on_create_report': 'report_service',
        'on_created_report': 'report_service',
        'on_request_report': 'report_service',
        'on_update_report': 'report_service',
        'on_poll': 'poll_service',
        'on_query_registration': 'registration_service',
        'on_create_party_registration': 'registration_service',
        'on_cancel_party_registration': 'registration_service'
    }

    def __init__(
        self,
        vtn_id="MyVTN",
        cert=None,
        key=None,
        passphrase=None,
        fingerprint_lookup=None,
        show_fingerprint=True,
        http_port=8080,
        http_host='127.0.0.1',
        http_cert=None,
        http_key=None,
        http_key_passphrase=None,
        http_path_prefix='/OpenADR2/Simple/2.0b',
        requested_poll_freq=timedelta(seconds=10),
        http_ca_file=None,
        ven_lookup=None,
        verify_message_signatures=False
    ):

        self.app = web.Application()
        self.services = {}
        
        # Store EMS controller reference
        self.ems_controller = None

        VTNService.verify_message_signatures = verify_message_signatures
        if ven_lookup:
            VTNService.ven_lookup = staticmethod(ven_lookup)
        if fingerprint_lookup:
            VTNService.fingerprint_lookup = staticmethod(fingerprint_lookup)

        self.services['event_service'] = EventService(vtn_id)
        self.services['report_service'] = ReportService(vtn_id)
        self.services['poll_service'] = PollService(vtn_id)
        self.services['registration_service'] = RegistrationService(vtn_id, requested_poll_freq)

        self.services['poll_service'].event_service = self.services['event_service']
        self.services['poll_service'].report_service = self.services['report_service']

        http_path_prefix = http_path_prefix.rstrip("/")
        self.app.add_routes([
            web.post(f"{http_path_prefix}/{s.__service_name__}", s.handler)
            for s in self.services.values()
        ])
        self.app['server'] = self

        self.http_port = http_port
        self.http_host = http_host
        self.http_path_prefix = http_path_prefix
        self.vtn_id = vtn_id

        # TLS Configuration
        if http_cert and http_key and http_ca_file:
            self.ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            self.ssl_context.load_verify_locations(http_ca_file)
            self.ssl_context.verify_mode = ssl.CERT_REQUIRED
            self.ssl_context.load_cert_chain(http_cert, http_key, http_key_passphrase)
        else:
            self.ssl_context = None

        # XML Signing Configuration
        if cert and key:
            with open(cert, "rb") as f:
                cert_data = f.read()
            with open(key, "rb") as f:
                key_data = f.read()

            if show_fingerprint:
                fp = utils.certificate_fingerprint(cert_data)
                print("\n" + "*" * 80)
                print(f"VTN Fingerprint: {fp}".center(80))
                print("*" * 80 + "\n")
        else:
            cert_data = key_data = None

        VTNService._create_message = partial(
            create_message,
            cert=cert_data,
            key=key_data,
            passphrase=passphrase
        )
        
        # Store active DR events for tracking
        self.active_dr_events = {}

    async def run(self):
        runner = web.AppRunner(self.app)
        await runner.setup()

        site = web.TCPSite(
            runner,
            port=self.http_port,
            host=self.http_host,
            ssl_context=self.ssl_context
        )
        await site.start()

        protocol = "https" if self.ssl_context else "http"
        print("\n" + "*" * 80)
        print("🏢 OPENADR VTN SERVER RUNNING".center(80))
        print(f"{protocol}://{self.http_host}:{self.http_port}{self.http_path_prefix}".center(80))
        print("VTN SENDS ONLY REDUCTION TARGETS - NO LOAD DETAILS".center(80))
        print("*" * 80 + "\n")

    def add_event(self, ven_id, signal_name, signal_type, intervals,
                  callback=None, delivery_callback=None, event_id=None,
                  targets=None, target=None, response_required='always',
                  market_context="oadr://unknown.context"):

        if not intervals:
            raise ValueError("Intervals are required")

        event_id = event_id or utils.generate_id()

        if target is not None:
            targets = [target]
        if targets is None:
            targets = [{'ven_id': ven_id}]

        event_descriptor = objects.EventDescriptor(
            event_id=event_id,
            modification_number=0,
            market_context=market_context,
            event_status=enums.EVENT_STATUS.FAR,
            created_date_time=datetime.now(timezone.utc),
            priority=1
        )

        event_signal = objects.EventSignal(
            intervals=intervals,
            signal_name=signal_name,
            signal_type=signal_type,
            signal_id=utils.generate_id()
        )

        active_period = utils.get_active_period_from_intervals(intervals, as_dict=False)

        event = objects.Event(
            event_descriptor=event_descriptor,
            event_signals=[event_signal],
            targets=targets,
            active_period=active_period,
            response_required=response_required
        )

        event_service = self.services['event_service']
        if ven_id not in event_service.events:
            event_service.events[ven_id] = []

        event_service.events[ven_id].append(event)

        if callback:
            event_service.event_callbacks[event_id] = (event, callback)
        if delivery_callback:
            event_service.event_delivery_callbacks[event_id] = delivery_callback

        poll_service = self.services['poll_service']
        poll_service.events_updated[ven_id] = True
        
        # Store event info for tracking
        self.active_dr_events[event_id] = {
            'ven_id': ven_id,
            'reduction_kw': intervals[0].signal_payload if intervals else 0,
            'duration': intervals[0].duration if intervals else None,
            'start_time': intervals[0].dtstart if intervals else None,
            'status': 'active'
        }
        
        # Log the event (VTN only knows reduction target)
        print(f"\n{'='*60}")
        print(f"📋 DR EVENT CREATED BY VTN")
        print(f"   Event ID: {event_id}")
        print(f"   Target VEN: {ven_id}")
        print(f"   Reduction Required: {intervals[0].signal_payload} kW")
        print(f"   Duration: {intervals[0].duration}")
        print(f"   Start Time: {intervals[0].dtstart}")
        print(f"{'='*60}\n")
        print(f"ℹ️ VTN does NOT know which loads VEN will control")
        print(f"   VEN will decide based on its house configuration\n")

        return event_id

    def cancel_event(self, ven_id, event_id):
        event_service = self.services['event_service']
        events = event_service.events.get(ven_id, [])
        for event in events:
            if event.event_descriptor.event_id == event_id:
                event.event_descriptor.event_status = enums.EVENT_STATUS.CANCELLED
                event.event_descriptor.modification_number += 1
                self.services['poll_service'].events_updated[ven_id] = True
                if event_id in self.active_dr_events:
                    self.active_dr_events[event_id]['status'] = 'cancelled'
                print(f"\n🚫 DR EVENT CANCELLED: {event_id}")
                return True
        return False

    def add_handler(self, name, func):
        logger.info(f"Adding handler: {name}")
        if name in self._MAP:
            service = self.services[self._MAP[name]]
            setattr(service, name, func)
            if name == 'on_poll':
                service.polling_method = 'external'
                self.services['event_service'].polling_method = 'external'
        else:
            raise NameError(f"Unknown handler '{name}'")

    def get_events(self):
        return self.services['event_service'].events
    
    def set_ems_controller(self, ems_controller):
        """Set the EMS controller reference"""
        self.ems_controller = ems_controller
    
    def get_active_events(self):
        """Get all active DR events"""
        return self.active_dr_events


def _get_cert_path(filename):
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cert_path = os.path.join(base, 'certificates', filename)
    # Create directory if it doesn't exist
    os.makedirs(os.path.dirname(cert_path), exist_ok=True)
    return cert_path


async def main():
    # VEN Lookup Function - VTN only needs basic VEN info
    async def ven_lookup(ven_id):
        # VTN only stores basic VEN identification
        # NO load details are stored here!
        
        print(f"\n[VTN] Looking up VEN: {ven_id}")
        
        # In production, this would query a database
        # But VTN never stores load details - only registration info
        if ven_id == "TestVEN":
            ven_info = {
                "ven_name": "TestVEN",
                "ven_id": "TestVEN",
                "fingerprint": "A3:C6:86:8C:8A:E3:DC:69:C5:E0:00:00:00:00:00:00:00:00:00:00",
                "registration_id": "reg_TestVEN_001"
                # ❌ NO load details (fans, lights, AC) - VTN doesn't need them!
            }
            print(f"[VTN] VEN found - VTN only stores basic info, not load details")
            return ven_info

        print(f"[VTN] VEN {ven_id} not found")
        return None

    # Pre-flight patch for OpenADR
    try:
        from openleadr import preflight
        orig = preflight._preflight_oadrDistributeEvent

        def safe(payload):
            if 'events' not in payload:
                payload['events'] = []
            return orig(payload)

        preflight._preflight_oadrDistributeEvent = safe
        logger.info("Preflight patch applied.")
    except Exception as e:
        logger.warning(f"Preflight patch failed: {e}")

    # Create server
    server = OpenADRServer(
        vtn_id="UtilityVTN",  # Changed to Utility name
        http_host="0.0.0.0",
        http_port=8443,
        http_cert=_get_cert_path("dummy_vtn.crt"),
        http_key=_get_cert_path("dummy_vtn.key"),
        http_ca_file=_get_cert_path("dummy_ca.crt"),
        cert=_get_cert_path("dummy_vtn.crt"),
        key=_get_cert_path("dummy_vtn.key"),
        ven_lookup=ven_lookup,
        verify_message_signatures=False
    )

    # Registration handler - VTN only stores basic registration info
    async def on_create_party_registration(registration_info):
        ven_name = registration_info.get('ven_name')
        ven_id = registration_info.get('ven_id', ven_name)
        reg_id = utils.generate_id()

        print("\n" + "=" * 60)
        print("[VTN] NEW VEN REGISTRATION")
        print(f"  VEN Name: {ven_name}")
        print(f"  VEN ID: {ven_id}")
        print(f"  Registration ID: {reg_id}")
        print("\n  ℹ️ VTN does NOT know:")
        print("     - What loads this VEN controls")
        print("     - How many fans/lights/AC the house has")
        print("     - Load priorities or preferences")
        print("  ✅ VTN only knows VEN is available for DR")
        print("=" * 60 + "\n")

        return ven_id, reg_id

    server.add_handler('on_create_party_registration', on_create_party_registration)

    # Event response handler - VEN tells VTN if it accepted
    async def on_created_event(ven_id, event_id, opt_type):
        print(f"\n[VTN] VEN RESPONSE RECEIVED")
        print(f"  VEN ID: {ven_id}")
        print(f"  Event ID: {event_id}")
        print(f"  Response: {opt_type}")
        
        if opt_type == "optIn":
            print(f"  ✅ VEN will reduce load (VEN decides which loads)")
        else:
            print(f"  ❌ VEN declined - cannot meet reduction")
        
        return True

    server.add_handler('on_created_event', on_created_event)

    # Request event handler - VEN polls for events
    async def on_request_event(ven_id):
        print(f"[VTN] VEN {ven_id} polling for events")
        events = server.get_events().get(ven_id, [])
        if events:
            print(f"  → {len(events)} event(s) available")
        return events if events else None

    server.add_handler('on_request_event', on_request_event)

    # Start the server
    await server.run()

    # Function to create DR events with ONLY reduction targets
    async def create_dr_event(reduction_kw: float, duration_minutes: int, 
                              reason: str = "grid_constraint", delay_seconds: int = 5):
        """Create a DR event - VTN only specifies HOW MUCH to reduce, not WHAT to reduce"""
        
        await asyncio.sleep(delay_seconds)
        
        now = datetime.now(timezone.utc)
        start_time = now + timedelta(seconds=5)
        
        print("\n" + "🎯" * 40)
        print(f"VTN CREATING DR EVENT".center(80))
        print("🎯" * 40)
        print(f"\n  Reason: {reason}")
        print(f"  Target Reduction: {reduction_kw} kW")
        print(f"  Duration: {duration_minutes} minutes")
        print(f"\n  ℹ️ VTN does NOT specify which loads to reduce")
        print(f"     That decision is made by VEN/EMS based on house configuration\n")
        
        interval = objects.Interval(
            dtstart=start_time,
            duration=timedelta(minutes=duration_minutes),
            signal_payload=reduction_kw  # ONLY the reduction target
        )
        
        event_id = server.add_event(
            ven_id="TestVEN",
            signal_name="load_reduction",
            signal_type="level",
            intervals=[interval],
            response_required="always"
        )
        
        print(f"\n✅ DR Event Created: {event_id}")
        print(f"   Start: {start_time.strftime('%H:%M:%S')}")
        print(f"   VEN will decide which loads to control\n")
        
        return event_id

    # Schedule different DR events for testing
    async def schedule_dr_events():
        # Wait for VEN to connect
        await asyncio.sleep(5)
        
        print("\n" + "=" * 60)
        print("📢 VTN SCHEDULING DEMAND RESPONSE EVENTS")
        print("=" * 60)
        print("VTN only knows reduction targets - NOT house load details")
        print("=" * 60 + "\n")
        
        # Event 1: Small reduction (VEN might turn off fans + lights)
        await create_dr_event(
            reduction_kw=0.25,  # 250W reduction
            duration_minutes=2,
            reason="peak_load_reduction",
            delay_seconds=2
        )
        
        # Event 2: Medium reduction (VEN might turn off AC)
        await create_dr_event(
            reduction_kw=1.5,   # 1500W reduction
            duration_minutes=3,
            reason="grid_emergency",
            delay_seconds=30
        )
        
        # Event 3: Full reduction (VEN might turn off all loads)
        await create_dr_event(
            reduction_kw=1.75,  # 1750W reduction (all loads)
            duration_minutes=2,
            reason="critical_grid_event",
            delay_seconds=90
        )
    
    # Uncomment to enable automatic DR events
    # asyncio.create_task(schedule_dr_events())
    
    # Interactive console for manual DR events
    print("\n" + "=" * 60)
    print("🎮 VTN CONTROL CONSOLE")
    print("=" * 60)
    print("\nCommands:")
    print("  fans    - Create event: Reduce 0.15 kW (Fans only)")
    print("  lights  - Create event: Reduce 0.10 kW (Lights only)")
    print("  ac      - Create event: Reduce 1.50 kW (AC only)")
    print("  all     - Create event: Reduce 1.75 kW (All loads)")
    print("  custom X - Create event: Reduce X kW")
    print("  status  - Show active events")
    print("  cancel <event_id> - Cancel event")
    print("  quit    - Exit\n")
    
    async def console_input():
        while True:
            try:
                cmd = await asyncio.get_event_loop().run_in_executor(None, input, "\n📟 Enter command: ")
                
                if cmd == "fans":
                    await create_dr_event(0.15, 2, "fan_reduction_test", 0)
                elif cmd == "lights":
                    await create_dr_event(0.10, 2, "light_reduction_test", 0)
                elif cmd == "ac":
                    await create_dr_event(1.5, 3, "ac_reduction_test", 0)
                elif cmd == "all":
                    await create_dr_event(1.75, 3, "full_reduction_test", 0)
                elif cmd.startswith("custom"):
                    parts = cmd.split()
                    if len(parts) == 2:
                        kw = float(parts[1])
                        await create_dr_event(kw, 2, f"custom_{kw}kw_reduction", 0)
                elif cmd == "status":
                    print("\n📊 Active DR Events:")
                    for eid, event in server.get_active_events().items():
                        if event['status'] == 'active':
                            print(f"  {eid}: {event['reduction_kw']}kW for VEN {event['ven_id']}")
                elif cmd.startswith("cancel"):
                    parts = cmd.split()
                    if len(parts) == 2:
                        server.cancel_event("TestVEN", parts[1])
                elif cmd == "quit":
                    print("\n🛑 Shutting down VTN...")
                    sys.exit(0)
                else:
                    print("Unknown command. Try: fans, lights, ac, all, custom 2.5, status, cancel <id>, quit")
                    
            except Exception as e:
                print(f"Error: {e}")
    
    # Start console input handler
    asyncio.create_task(console_input())

    # Keep server running
    while True:
        await asyncio.sleep(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n[VTN] Server Stopped")
    except Exception as e:
        print(f"\n[VTN] Error: {e}")
        import traceback
        traceback.print_exc()