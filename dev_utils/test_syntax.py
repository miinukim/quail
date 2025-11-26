
import sys
import os

# Add project root to path
sys.path.append('/home/mkim/entserver/quail')

from orchestrator.processor import VirtualProcessor

# Mock context
class MockInstrumentClient:
    def get_parameter(self, inst, param):
        return 0.123
    def set_parameter(self, inst, param, val):
        print(f"[MOCK] Set {inst}.{param} = {val}")
    def run_fpga(self, *args):
        print(f"[MOCK] run_fpga called with {args}")
        # Return 4xN data
        return [[0]*100]*4

context = {
    'log': lambda msg: print(f"[LOG] {msg}"),
    'instruments': MockInstrumentClient(),
    'sleep': lambda x: print(f"[SLEEP] {x}"),
    'save_data': lambda p, d: print(f"[SAVE] Params={p}, DataShape=({len(d)}, {len(d[0])})")
}

code = """
# Initialize data table schema (no num_data needed)
init_data(param=("v", "i"))

# Define a variable
param v_start = 0

for v in (from v_start to 1 step 0.5) {
    set dac v
    
    # Run sequencer and capture single data object
    d = run("seq1", "code.py")
    
    # Get parameter
    i = get keithley.current
    
    # Save data (single data object)
    save(params=(v, i), data=d)
}
"""

print("Testing new syntax...")
processor = VirtualProcessor(context)
try:
    processor.execute(code)
    print("Execution successful!")
except Exception as e:
    print(f"Execution failed: {e}")
    import traceback
    traceback.print_exc()
