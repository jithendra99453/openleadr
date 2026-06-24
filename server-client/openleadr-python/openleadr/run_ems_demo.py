#!/usr/bin/env python3
"""
Complete EMS Demo - Run this to start both VTN and VEN
"""

import asyncio
import subprocess
import sys
import time

def print_banner():
    print("\n" + "=" * 80)
    print("OPENADR EMS LOAD MANAGEMENT DEMO".center(80))
    print("=" * 80)
    print("\nThis demo shows:")
    print("  1. VTN schedules load reductions at specific times")
    print("  2. VTN sends OpenADR events with time, duration, and wattage")
    print("  3. VEN receives events and executes load reductions")
    print("  4. EMS controller manages individual loads based on priority")
    print("\n" + "=" * 80 + "\n")

async def main():
    print_banner()
    
    print("Starting OpenADR EMS Demo...")
    print("\nYou need to run TWO terminals:")
    print("\n📡 TERMINAL 1 - Start the VTN Server:")
    print("   python server.py")
    print("\n💻 TERMINAL 2 - Start the VEN Client:")
    print("   python client.py")
    print("\n" + "=" * 80)
    
    print("\nExpected Output:")
    print("-" * 40)
    print("VTN Server will show:")
    print("  📅 SCHEDULED LOAD REDUCTION")
    print("     Reduction: 50 kW")
    print("     Start: 14:30:00")
    print("     Duration: 5 minutes")
    print("\n  🔔 SENDING LOAD REDUCTION EVENT")
    print("     Target Reduction: 50 kW")
    print("\nVEN Client will show:")
    print("  ⚡ PROCESSING LOAD REDUCTION EVENT")
    print("     Target Reduction: 50 kW")
    print("  ✓ Reduced HVAC System: 35kW → 25kW (10kW)")
    print("  ✓ Reduced Lighting: 25kW → 15kW (10kW)")
    print("  ✓ Reduced EV Charger: 11kW → 0kW (11kW)")
    print("     Total Reduction Achieved: 46kW")
    
    print("\n" + "=" * 80)
    print("Run the commands above in separate terminals to see the demo")
    print("=" * 80 + "\n")

if __name__ == "__main__":
    asyncio.run(main())