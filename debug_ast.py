import os
import sys

# Add the project root to python path
sys.path.append('/home/mkim/entserver/quail')

# os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'quail.settings')
# django.setup()

from orchestrator.parser import CustomScriptParser

# Mock context
class MockInstrumentClient:
    def set_parameter(self, inst, param, val):
        print(f"[MOCK] Setting {inst}.{param} to {val}")
    
    def get_parameter(self, inst, param):
        print(f"[MOCK] Getting {inst}.{param}")
        return 0.123 # Mock return value
        
    def trigger_fpga(self):
        print("[MOCK] Triggering FPGA")

context = {
    'log': lambda msg: print(f"[LOG] {msg}"),
    'sleep': lambda dur: print(f"[SLEEP] Sleeping for {dur}s"),
    'instruments': MockInstrumentClient()
}

code = """
# Initialize data table schema
init_data(param=("voltage", "current"), num_data=2)

# Define some variables
set dac_alias "keithley.voltage"

for v in (from 0 to 1 step 0.5) {
    # Set parameter using variable
    set dac_alias v
    
    # Wait
    sleep(0.1)
    
    # Get parameter
    meas_i = get keithley.current
    
    # Run sequencer
    d1, d2 = run("seq1", "code.py")
    
    # Save data
    save(params=(v, meas_i), data=(d1, d2))
}
"""

print("Parsing and Executing Code:")
print("-" * 40)
parser = CustomScriptParser(context)
try:
    parser.execute(code)
    print("-" * 40)
    print("Execution Successful!")
except Exception as e:
    print("-" * 40)
    print(f"Execution Failed: {e}")
    import traceback
    traceback.print_exc()
