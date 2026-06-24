#!/usr/bin/env python3
"""
OpenADR 2.0b VEN Client - Fixed Version
Corrects the shed_loads method/list conflict
"""

import asyncio
import aiohttp
import xml.etree.ElementTree as ET
import uuid
import re
from datetime import datetime
from typing import Optional, List
import threading
import sys
import os

# Windows fixes
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')

# Ensure we import from the same directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from simple_ems_controller import SimpleEMSController

try:
    from openleadr.security import get_client_ssl_context, get_client_server_ssl_context, MTLS_ENABLED
except ImportError:
    from security import get_client_ssl_context, get_client_server_ssl_context, MTLS_ENABLED

# ===== CONFIGURATION =====
VTN_HOST = "192.168.0.105"  # MacBook IP (where the OpenADR VTN server is running)
VTN_PORT = 8443

EMS_WS_URL = "ws://127.0.0.1:8000/ws/dashboard"  # WebSocket server URL (runs locally on this Windows machine)
# If you run the WebSocket server on the MacBook instead, change "localhost" to the MacBook's IP.

# Local configuration only (Google Sheets removed)

VEN_INDEX = os.environ.get("VEN_INDEX", "")
if VEN_INDEX:
    VEN_NAME = os.environ.get("VEN_NAME", f"TestVEN{VEN_INDEX}")
else:
    VEN_NAME = os.environ.get("VEN_NAME", "TEST_VEN_7")

VEN_TYPE = "Residential"
POLL_INTERVAL = 5  # Poll every 5 seconds
# =========================


class OpenADRVen:
    def __init__(self, base_url: str, ven_name: str):
        self.base_url = base_url
        self.ven_name = ven_name
        self.ven_id = None
        self.session = None
        
        # Extract index from VEN_INDEX env var or from ven_name suffix
        ven_idx_str = os.environ.get("VEN_INDEX", "")
        if not ven_idx_str:
            match = re.search(r'\d+$', ven_name)
            if match:
                ven_idx_str = match.group()
        try:
            self.ven_index = int(ven_idx_str)
        except ValueError:
            self.ven_index = 1 # default to 1
            
        if self.ven_index not in [1, 2, 3]:
            self.ven_index = 1
            
        self.ems = SimpleEMSController(ws_url=f"{EMS_WS_URL}?client_id={self.ven_index}")
        self.restore_task = None
        self.input_queue = None
        self.input_thread = None
        self.active_event_start = None
        self.active_event_end = None
        self.active_event_duration_min = None
        
    async def _post(self, endpoint: str, xml_data: str) -> Optional[str]:
        """Send POST request"""
        url = f"{self.base_url}/{endpoint}"
        try:
            async with self.session.post(
                url, 
                data=xml_data.encode(),
                headers={"Content-Type": "application/xml"}
            ) as resp:
                text = await resp.text()
                if resp.status == 200:
                    return text
                print(f"   HTTP {resp.status}")
                return None
        except Exception as e:
            print(f"   Error: {e}")
            return None

    async def register(self) -> bool:
        """Register with VTN"""
        print(f"\n📡 Registering {self.ven_name}...")
        
        xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<oadr:oadrPayload xmlns:oadr="http://openadr.org/oadr-2.0b/2012/07"
                  xmlns:ei="http://docs.oasis-open.org/ns/energyinterop/201110">
    <oadr:oadrSignedObject>
        <oadr:oadrCreatePartyRegistration ei:schemaVersion="2.0b">
            <ei:requestID>req_{uuid.uuid4().hex[:8]}</ei:requestID>
            <ei:venID>{self.ven_name}</ei:venID>
            <oadr:oadrProfileName>2.0b</oadr:oadrProfileName>
            <oadr:oadrTransportName>simpleHttp</oadr:oadrTransportName>
            <oadr:oadrHttpPullModel>true</oadr:oadrHttpPullModel>
            <oadr:oadrVenName>{self.ven_name}</oadr:oadrVenName>
            <oadr:oadrVenType>Residential</oadr:oadrVenType>
        </oadr:oadrCreatePartyRegistration>
    </oadr:oadrSignedObject>
</oadr:oadrPayload>'''
        
        resp = await self._post("EiRegisterParty", xml)
        if not resp:
            return False
            
        # Extract VEN ID
        match = re.search(r'<venID>([^<]+)</venID>', resp)
        if match:
            self.ven_id = match.group(1)
            print(f"   ✅ Registered as: {self.ven_id}")
            return True
        else:
            self.ven_id = self.ven_name
            print(f"   ✅ Registered (using name)")
            return True

    async def start_push_listener(self):
        """Start local web server to listen for push notifications from VTN"""
        from aiohttp import web
        app = web.Application()
        
        async def handle_push(request):
            xml_text = await request.text()
            await self.handle_event(xml_text)
            return web.Response(text="Received")
            
        app.router.add_post("/push", handle_push)
        
        ssl_context = get_client_server_ssl_context()
        self.push_runner = web.AppRunner(app)
        await self.push_runner.setup()
        self.push_site = web.TCPSite(self.push_runner, host="127.0.0.1", port=8001, ssl_context=ssl_context)
        await self.push_site.start()
        print(f"   ✅ Client Push Listener started on port 8001")
        
    async def stop_push_listener(self):
        """Stop local push web server"""
        if hasattr(self, 'push_runner') and self.push_runner:
            await self.push_runner.cleanup()

    async def create_opt(self, opt_id: str, opt_type: str, resource_id: str, start_time: datetime, duration_minutes: int) -> bool:
        """Send opt schedule to VTN"""
        xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<oadr:oadrPayload xmlns:oadr="http://openadr.org/oadr-2.0b/2012/07"
                  xmlns:ei="http://docs.oasis-open.org/ns/energyinterop/201110">
    <oadr:oadrSignedObject>
        <ei:oadrCreateOpt ei:schemaVersion="2.0b">
            <ei:requestID>opt_{uuid.uuid4().hex[:8]}</ei:requestID>
            <ei:venID>{self.ven_id or self.ven_name}</ei:venID>
            <ei:optID>{opt_id}</ei:optID>
            <ei:optType>{opt_type}</ei:optType>
            <ei:resourceID>{resource_id}</ei:resourceID>
            <ei:createdDateTime>{start_time.isoformat()}Z</ei:createdDateTime>
            <ei:duration>PT{duration_minutes}M</ei:duration>
        </ei:oadrCreateOpt>
    </oadr:oadrSignedObject>
</oadr:oadrPayload>'''
        resp = await self._post("EiOpt", xml)
        return resp is not None

    async def cancel_opt(self, opt_id: str) -> bool:
        """Cancel opt schedule on VTN"""
        xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<oadr:oadrPayload xmlns:oadr="http://openadr.org/oadr-2.0b/2012/07"
                  xmlns:ei="http://docs.oasis-open.org/ns/energyinterop/201110">
    <oadr:oadrSignedObject>
        <ei:oadrCancelOpt ei:schemaVersion="2.0b">
            <ei:requestID>opt_{uuid.uuid4().hex[:8]}</ei:requestID>
            <ei:venID>{self.ven_id or self.ven_name}</ei:venID>
            <ei:optID>{opt_id}</ei:optID>
        </ei:oadrCancelOpt>
    </oadr:oadrSignedObject>
</oadr:oadrPayload>'''
        resp = await self._post("EiOpt", xml)
        return resp is not None

    async def poll(self):
        """Poll for events"""
        if not self.ven_id:
            return
            
        xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<oadr:oadrPayload xmlns:oadr="http://openadr.org/oadr-2.0b/2012/07"
                  xmlns:ei="http://docs.oasis-open.org/ns/energyinterop/201110">
    <oadr:oadrSignedObject>
        <oadr:oadrPoll ei:schemaVersion="2.0b">
            <ei:venID>{self.ven_id}</ei:venID>
        </oadr:oadrPoll>
    </oadr:oadrSignedObject>
</oadr:oadrPayload>'''
        
        resp = await self._post("OadrPoll", xml)
        
        if resp and ('oadrDistributeEvent' in resp or 'eventID' in resp):
            await self.handle_event(resp)
            
    async def send_response(self, event_id: str, opt_type: str, actual_kw: float = 0.0):
        """Send optIn/optOut response"""
        xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<oadr:oadrPayload xmlns:oadr="http://openadr.org/oadr-2.0b/2012/07"
                  xmlns:ei="http://docs.oasis-open.org/ns/energyinterop/201110">
    <oadr:oadrSignedObject>
        <ei:eiCreatedEvent ei:schemaVersion="2.0b">
            <ei:requestID>resp_{uuid.uuid4().hex[:8]}</ei:requestID>
            <ei:venID>{self.ven_id}</ei:venID>
            <ei:optType>{opt_type}</ei:optType>
            <ei:eventResponses>
                <ei:eventResponse>
                    <ei:responseCode>200</ei:responseCode>
                    <ei:responseDescription>{actual_kw}</ei:responseDescription>
                    <ei:qualifiedEventID>
                        <ei:eventID>{event_id}</ei:eventID>
                        <ei:modificationNumber>0</ei:modificationNumber>
                    </ei:qualifiedEventID>
                </ei:eventResponse>
            </ei:eventResponses>
        </ei:eiCreatedEvent>
    </oadr:oadrSignedObject>
</oadr:oadrPayload>'''
        
        await self._post("EiCreatedEvent", xml)
        print(f"   ✅ Sent response: {opt_type}")

    async def handle_event(self, xml_text: str):
        """Process incoming DR event"""
        print("\n" + "="*60)
        print("📨 DR EVENT RECEIVED!")
        print("="*60)
        
        # Parse event details
        event_id = None
        reduction_kw = 0
        duration_min = 30
        program = "Unknown"
        dtstart = None
        
        # Try to parse XML
        try:
            root = ET.fromstring(xml_text)
            for el in root.iter():
                tag = el.tag.split('}')[-1]
                if el.text:
                    text = el.text.strip()
                    if tag == 'eventID':
                        event_id = text
                        print(f"   Event ID: {text}")
                    elif tag == 'dtstart':
                        try:
                            dt_str = text
                            if dt_str.endswith('Z'):
                                dt_str_parsed = dt_str[:-1] + "+00:00"
                                dtstart = datetime.fromisoformat(dt_str_parsed)
                            else:
                                dtstart = datetime.fromisoformat(dt_str)
                            dt_local = dtstart.astimezone() if dtstart.tzinfo else dtstart
                            print(f"   Start Time: {dt_local.strftime('%Y-%m-%d %I:%M:%S %p %Z')}")
                        except:
                            pass
                    elif tag == 'payloadFloat':
                        try:
                            reduction_kw = float(text)
                            print(f"   Reduction: {text} kW")
                        except:
                            pass
                    elif tag == 'currentValue':
                        try:
                            if reduction_kw == 0:
                                reduction_kw = float(text)
                                print(f"   Reduction: {text} kW")
                        except:
                            pass
                    elif tag == 'eventDescription':
                        program = text
                        print(f"   Program: {text}")
                    elif tag == 'duration':
                        # Parse PT5M format
                        match = re.search(r'PT(\d+)M', text)
                        if match:
                            duration_min = int(match.group(1))
                            print(f"   Duration: {duration_min} minutes")
        except Exception as e:
            print(f"   Parse error: {e}")
            # Try regex fallback
            event_match = re.search(r'<eventID>([^<]+)</eventID>', xml_text)
            if event_match:
                event_id = event_match.group(1)
                print(f"   Event ID (fallback): {event_id}")
            kw_match = re.search(r'<payloadFloat>([^<]+)</payloadFloat>', xml_text)
            if kw_match:
                try:
                    reduction_kw = float(kw_match.group(1))
                    print(f"   Reduction (fallback): {reduction_kw} kW")
                except:
                    pass
        
        if not event_id:
            print("   ❌ Could not parse event ID")
            return
            
        if reduction_kw == 0:
            print("   ⚠️ No reduction value found, using default 5kW")
            reduction_kw = 5
            
        # Display event info
        print(f"\n   📊 Event Details:")
        print(f"      Program: {program}")
        print(f"      Required Reduction: {reduction_kw} kW")
        print(f"      Duration: {duration_min} minutes")
        
        # Calculate which loads would be affected
        candidates, actual = self.ems.decide_which_loads_to_reduce(reduction_kw)
        
        if candidates:
            print(f"\n   📋 Loads to shed if you accept:")
            for load in candidates:
                print(f"      • {load['name']} ({load['power_w']}W)")
            print(f"      Achievable reduction: {actual:.2f} kW")
        else:
            print(f"\n   ⚠️ No loads available to shed (all loads already off)")
        
        # Ask user for decision
        print(f"\n🤔 Do you want to participate?")
        print("   Type 'yes' to accept, 'no' to decline: ", end="", flush=True)
        
        # Flush the queue of any stale inputs entered before this prompt
        if self.input_queue is not None:
            while not self.input_queue.empty():
                try:
                    self.input_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
        
        try:
            if self.input_queue is not None:
                answer = await asyncio.wait_for(self.input_queue.get(), timeout=30.0)
            else:
                answer = "no"
        except asyncio.TimeoutError:
            answer = "no"
            print("\n   ⏰ Timeout - defaulting to decline")
            
        if answer in ['yes', 'y', 'accept']:
            print(f"\n   ✅ ACCEPTING event - shedding loads")
            
            # Set active event time window for telemetry (using timezone-aware UTC)
            from datetime import timedelta, timezone
            now_utc = datetime.now(timezone.utc)
            # Force event start to now to eliminate any scheduled delay
            event_start = now_utc
            event_end = event_start + timedelta(minutes=duration_min)
            self.active_event_start = event_start
            self.active_event_end = event_end
            self.active_event_duration_min = duration_min
            start_local = self.active_event_start.astimezone() if self.active_event_start.tzinfo else self.active_event_start
            end_local = self.active_event_end.astimezone() if self.active_event_end.tzinfo else self.active_event_end
            print(f"   📊 Telemetry reporting active between: {start_local.strftime('%Y-%m-%d %I:%M:%S %p %Z')} and {end_local.strftime('%Y-%m-%d %I:%M:%S %p %Z')}")
            
            await self.send_response(event_id, "optIn", actual)
            
            # Cancel previous task if exists
            if self.restore_task and not self.restore_task.done():
                self.restore_task.cancel()
            
            # Schedule execution and restore
            async def event_execution(e_start, e_end):
                try:
                    # Execute immediately — no delay
                    print(f"\n🚀 Event accepted! Shedding loads immediately...", flush=True)
                    await self.ems.execute_reduction(reduction_kw, duration_min, client_id=self.ven_index)
                    
                    # Print connection warning/confirmation
                    import websockets
                    if not self.ems.client or self.ems.client.state != websockets.State.OPEN:
                        print("   ⚠️ WARNING: EMS is running in SIMULATION mode. Commands are NOT sent to the physical ESP!", flush=True)
                    else:
                        print("   👉 Commands successfully forwarded to the ESP controller via WebSocket!", flush=True)
                    
                    # Wait for event end
                    delay_to_end = (e_end - datetime.now(timezone.utc)).total_seconds()
                    if delay_to_end > 0:
                        print(f"   👉 Relays switched OFF immediately. Auto-restoring back to normal in {delay_to_end/60:.1f} minutes...", flush=True)
                        await asyncio.sleep(delay_to_end)
                        
                    print(f"\n⏰ Event duration expired - restoring loads", flush=True)
                    await self.ems.restore_loads()
                    print(f"   ✅ Loads restored", flush=True)
                    await asyncio.sleep(2)
                    await self.fetch_and_print_wallet()
                except asyncio.CancelledError:
                    print(f"\n⚠️ Event execution cancelled", flush=True)
                except Exception as e:
                    print(f"\n❌ ERROR during event execution: {e}", flush=True)
                    import traceback
                    traceback.print_exc()
                finally:
                    # Clear active event window only if it belongs to this execution
                    if self.active_event_start == e_start:
                        self.active_event_start = None
                        self.active_event_end = None
                        self.active_event_duration_min = None
            
            self.restore_task = asyncio.create_task(event_execution(event_start, event_end))
            
            # Wait for server to process and print wallet
            await asyncio.sleep(1)
            await self.fetch_and_print_wallet()
        else:
            print(f"\n   ❌ DECLINING event - no changes made")
            await self.send_response(event_id, "optOut", 0.0)
            
            # Wait for server to process and print wallet
            await asyncio.sleep(1)
            await self.fetch_and_print_wallet()
            
        print("="*60)

    async def fetch_and_print_wallet(self):
        """Fetch and print the latest wallet status from the VTN server"""
        url = f"http://{VTN_HOST}:{VTN_PORT}/api/vtn/wallet/{self.ven_id}"
        try:
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    balance = data.get("balance", 0.0)
                    rewards = data.get("rewards", 0.0)
                    penalties = data.get("penalties", 0.0)
                    print(f"💳 [WALLET STATUS] Balance: {balance} units | Rewards: {rewards} units | Penalties: {penalties} units")
        except Exception:
            pass

    async def fetch_local_telemetry(self) -> Optional[dict]:
        """Fetch the latest telemetry data dynamically from the local WebSocket server state"""
        if self.ems.last_status:
            status = self.ems.last_status
            try:
                voltage = float(status["voltage"])
                current = float(status["current"])
                power = voltage * current
                data = {
                    "voltage": voltage,
                    "current": current,
                    "power": round(power, 2),
                    "energy_kwh": float(status["energy"])
                }
                return data
            except (KeyError, ValueError, TypeError) as err:
                print(f"   ⚠️ [Local Telemetry] Missing or invalid telemetry fields in status: {err}")
        return None

    async def send_telemetry_report(self, telemetry_data: dict, event_start: Optional[datetime] = None, duration_min: Optional[int] = None) -> bool:
        """Send OadrUpdateReport XML containing telemetry data from local server to the VTN server"""
        print(f"\n📊 Sending Telemetry Report from Local Server State to VTN...")
        
        if event_start:
            event_start_str = event_start.strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            event_start_str = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            
        if duration_min:
            duration_str = f"PT{duration_min}M"
        else:
            duration_str = "PT15M"
            
        now_str = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        request_id = f"rup_{uuid.uuid4().hex[:8]}"
        report_id = f"rp_{uuid.uuid4().hex[:8]}"
        report_specifier_id = "0013A20040980FAE"
        
        # Extract local values dynamically
        voltage = telemetry_data["voltage"]
        current = telemetry_data["current"]
        power = telemetry_data["power"]
        energy = telemetry_data["energy_kwh"]
        
        print(f"   ⚡ Telemetry: V={voltage}V, I={current}A, P={power}W, Energy={energy}kWh")
        
        xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<oadr:oadrPayload xmlns:oadr="http://openadr.org/oadr-2.0b/2012/07"
                  xmlns:ei="http://docs.oasis-open.org/ns/energyinterop/201110"
                  xmlns:xcal="urn:ietf:params:xml:ns:icalendar-2.0"
                  xmlns:strm="urn:ietf:params:xml:ns:icalendar-2.0:stream"
                  xmlns:pyld="http://docs.oasis-open.org/ns/energyinterop/201110/payloads">
    <oadr:oadrSignedObject>
        <oadr:oadrUpdateReport ei:schemaVersion="2.0b">
            <pyld:requestID>{request_id}</pyld:requestID>
            <oadr:oadrReport>
                <xcal:dtstart>
                    <xcal:date-time>{event_start_str}</xcal:date-time>
                </xcal:dtstart>
                <xcal:duration>
                    <xcal:duration>{duration_str}</xcal:duration>
                </xcal:duration>
                <strm:intervals>
                    <ei:interval>
                        <xcal:dtstart>
                            <xcal:date-time>{event_start_str}</xcal:date-time>
                        </xcal:dtstart>
                        <xcal:duration>
                            <xcal:duration>{duration_str}</xcal:duration>
                        </xcal:duration>
                        
                        <!-- Voltage Payload -->
                        <oadr:oadrReportPayload>
                            <ei:rID>Voltage</ei:rID>
                            <ei:payloadFloat>
                                <ei:value>{voltage}</ei:value>
                            </ei:payloadFloat>
                            <oadr:oadrDataQuality>Quality Good - Non Specific</oadr:oadrDataQuality>
                        </oadr:oadrReportPayload>
                        
                        <!-- Current Payload -->
                        <oadr:oadrReportPayload>
                            <ei:rID>Current</ei:rID>
                            <ei:payloadFloat>
                                <ei:value>{current}</ei:value>
                            </ei:payloadFloat>
                            <oadr:oadrDataQuality>Quality Good - Non Specific</oadr:oadrDataQuality>
                        </oadr:oadrReportPayload>
                        
                        <!-- Power Payload -->
                        <oadr:oadrReportPayload>
                            <ei:rID>Power</ei:rID>
                            <ei:payloadFloat>
                                <ei:value>{power}</ei:value>
                            </ei:payloadFloat>
                            <oadr:oadrDataQuality>Quality Good - Non Specific</oadr:oadrDataQuality>
                        </oadr:oadrReportPayload>
                        
                        <!-- Energy Payload -->
                        <oadr:oadrReportPayload>
                            <ei:rID>Energy</ei:rID>
                            <ei:payloadFloat>
                                <ei:value>{energy}</ei:value>
                            </ei:payloadFloat>
                            <oadr:oadrDataQuality>Quality Good - Non Specific</oadr:oadrDataQuality>
                        </oadr:oadrReportPayload>
                        
                    </ei:interval>
                </strm:intervals>
                <ei:eiReportID>{report_id}</ei:eiReportID>
                <ei:reportRequestID>REQ:RReq:1395368583267</ei:reportRequestID>
                <ei:reportSpecifierID>{report_specifier_id}</ei:reportSpecifierID>
                <ei:reportName>TELEMETRY_STATUS</ei:reportName>
                <ei:createdDateTime>{now_str}</ei:createdDateTime>
            </oadr:oadrReport>
            <ei:venID>{self.ven_id}</ei:venID>
        </oadr:oadrUpdateReport>
    </oadr:oadrSignedObject>
</oadr:oadrPayload>'''
        
        resp = await self._post("OadrUpdateReport", xml)
        if resp:
            print("   ✅ Telemetry report successfully sent and acknowledged by VTN!")
            return True
        return False

    async def run(self):
        """Main loop"""
        self.input_queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        
        def input_thread_worker():
            while True:
                try:
                    line = sys.stdin.readline()
                    if not line:
                        break
                    loop.call_soon_threadsafe(self.input_queue.put_nowait, line.strip())
                except Exception:
                    break
                    
        self.input_thread = threading.Thread(target=input_thread_worker, daemon=True)
        self.input_thread.start()

        ssl_context = get_client_ssl_context() if MTLS_ENABLED else None
        connector = aiohttp.TCPConnector(ssl=ssl_context) if ssl_context else None
        async with aiohttp.ClientSession(connector=connector) as session:
            self.session = session
            
            # Connect to local EMS WebSocket in the background so it doesn't block registration
            asyncio.create_task(self.ems.connect())
            
            # Register
            while not await self.register():
                print("   Retrying in 5 seconds...")
                await asyncio.sleep(5)
            
            # Fetch and display initial wallet
            await self.fetch_and_print_wallet()
            
            print(f"\n{'='*60}")
            print(f"🏠 VEN Client Running")
            print(f"{'='*60}")
            print(f"   VEN ID: {self.ven_id}")
            print(f"   Polling every {POLL_INTERVAL} seconds")
            print(f"   Waiting for DR events...")
            print(f"   Press Ctrl+C to stop")
            print(f"{'='*60}\n")
            
            # Telemetry reporting loop disabled (Reporting to VTN removed, only opt-in/opt-out reported)
            pass
            
            # Polling loop
            poll_count = 0
            while True:
                await self.poll()
                
                # Periodic wallet status check every 3 polls (15 seconds)
                poll_count += 1
                if poll_count % 3 == 0:
                    await self.fetch_and_print_wallet()
                
                await asyncio.sleep(POLL_INTERVAL)


async def main():
    print("\n" + "*"*60)
    print("🏠 OpenADR 2.0b VEN Client")
    print("*"*60)
    
    protocol = "https" if MTLS_ENABLED else "http"
    base_url = f"{protocol}://{VTN_HOST}:{VTN_PORT}/OpenADR2/Simple/2.0b"
    print(f"   VTN: {base_url}")
    
    ven = OpenADRVen(base_url, VEN_NAME)
    
    try:
        await ven.run()
    except KeyboardInterrupt:
        print("\n\n🛑 Stopping VEN...")
        if ven.restore_task and not ven.restore_task.done():
            ven.restore_task.cancel()
        await ven.ems.restore_loads()
        await ven.ems.disconnect()
        print("✅ Done")


if __name__ == "__main__":
    asyncio.run(main())