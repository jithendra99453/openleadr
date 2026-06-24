import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openleadr.client import OpenADRClient
from simple_ems_controller import SimpleEMSController

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ven_client")


class VENClientWithEMS:

    def __init__(
        self,
        vtn_url="https://127.0.0.1:8443/OpenADR2/Simple/2.0b",
        cert_path=None,
        key_path=None,
        ca_path=None
    ):

        self.client = OpenADRClient(
            ven_name="TestVEN",
            vtn_url=vtn_url,
            debug=False,
            cert=cert_path,
            key=key_path,
            ca_file=ca_path,
            check_hostname=False,
            disable_signature=False,
            show_fingerprint=True  # Add this to see VEN fingerprint for VTN configuration
        )

        self.ems = SimpleEMSController()
        self.running = False  # Add running flag for clean shutdown

    async def on_event(self, event):
        """Handle incoming events from VTN"""
        
        event_id = event.get(
            "event_descriptor",
            {}
        ).get(
            "event_id",
            "unknown"
        )

        print("\n" + "=" * 60)
        print(f"📨 EVENT RECEIVED : {event_id}")

        signals = event.get("event_signals", [])

        if not signals:
            print("❌ No event signals found")
            return "optOut"

        intervals = signals[0].get("intervals", [])

        if not intervals:
            print("❌ No intervals found")
            return "optOut"

        reduction_kw = 0
        start_time = None
        duration = None

        for interval in intervals:
            reduction_kw = interval.get("signal_payload", 0)
            start_time = interval.get("dtstart")
            duration = interval.get("duration")
            print(f"\n📊 Interval Details:")
            print(f"   Start: {start_time}")
            print(f"   Duration: {duration}")
            print(f"   Reduction: {reduction_kw} kW")
            break

        print(f"\n🎯 Target Reduction = {reduction_kw} kW")

        # Only execute if reduction > 0
        if reduction_kw > 0:
            await self.ems.execute_reduction(reduction_kw)
            response = "optIn"
        else:
            print("ℹ️ No reduction required, restoring loads")
            await self.ems.restore_loads()
            response = "optIn"

        print("=" * 60)
        
        return response

    async def on_update_event(self, event):
        """Handle event updates (cancellations) from VTN"""

        event_descriptor = event.get("event_descriptor", {})
        event_id = event_descriptor.get("event_id", "unknown")
        event_status = event_descriptor.get("event_status", "unknown")
        modification_number = event_descriptor.get("modification_number", 0)

        print("\n" + "=" * 60)
        print(f"📨 EVENT UPDATE")
        print(f"   Event ID: {event_id}")
        print(f"   Status: {event_status}")
        print(f"   Modification: {modification_number}")

        if event_status == "cancelled":
            print("⚠️ Event CANCELLED - Restoring all loads...")
            await self.ems.restore_loads()
        elif event_status == "modified":
            print("📝 Event MODIFIED - Will wait for new event")
            
        print("=" * 60)
        
        return "optIn"

    async def on_registration_completed(self, registration_info):
        """Handle successful registration with VTN"""
        ven_id = registration_info.get("ven_id")
        registration_id = registration_info.get("registration_id")
        
        print("\n" + "=" * 60)
        print("✅ REGISTRATION SUCCESSFUL")
        print(f"   VEN ID: {ven_id}")
        print(f"   Registration ID: {registration_id}")
        print("=" * 60 + "\n")

    async def run(self):
        """Main run method for the VEN client"""

        print("\n" + "=" * 70)
        print("🚀 STARTING VEN CLIENT WITH EMS")
        print("=" * 70)
        
        try:
            # Connect to MQTT broker
            print("\n🔌 Connecting to MQTT broker...")
            await self.ems.connect()
            print("✅ MQTT connected")
            
            # Add OpenADR handlers
            self.client.add_handler("on_event", self.on_event)
            self.client.add_handler("on_update_event", self.on_update_event)
            self.client.add_handler("on_registration_completed", self.on_registration_completed)
            
            # Start OpenADR client
            print("\n📡 Connecting to VTN...")
            await self.client.run()
            
            self.running = True
            
            # Keep running
            while self.running:
                await asyncio.sleep(1)
                
        except Exception as e:
            logger.error(f"Error in VEN client: {e}")
            raise
        finally:
            await self.cleanup()

    async def cleanup(self):
        """Clean up resources"""
        print("\n🧹 Cleaning up...")
        self.running = False
        
        if hasattr(self, 'client'):
            await self.client.stop()
            
        if hasattr(self, 'ems'):
            await self.ems.disconnect()
            
        print("✅ Cleanup complete")

    async def stop(self):
        """Stop the VEN client gracefully"""
        print("\n🛑 Stopping VEN client...")
        self.running = False
        await self.cleanup()


if __name__ == "__main__":
    import os

    base = os.path.dirname(
        os.path.dirname(
            os.path.abspath(__file__)
        )
    )

    ven = VENClientWithEMS(
        cert_path=os.path.join(
            base,
            "certificates",
            "dummy_ven.crt"
        ),
        key_path=os.path.join(
            base,
            "certificates",
            "dummy_ven.key"
        ),
        ca_path=os.path.join(
            base,
            "certificates",
            "dummy_ca.crt"
        )
    )

    try:
        asyncio.run(ven.run())
    except KeyboardInterrupt:
        print("\n\n👋 VEN client stopped by user")
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()