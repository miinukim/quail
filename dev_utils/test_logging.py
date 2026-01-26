
import sys
import os
import shutil

# Add project root to path
sys.path.append('/home/mkim/entserver/quail')

from orchestrator.processor import VirtualProcessor

# Mock context
class MockInstrumentClient:
    def get_parameter(self, inst, param):
        return 0.123
    def set_parameter(self, inst, param, val):
        pass # Logged by processor now
    def run_fpga(self, *args):
        return [[0]*100]*4

context = {
    # 'log': lambda msg: print(f"[LOG] {msg}"), # Removed
    'instruments': MockInstrumentClient(),
    'sleep': lambda x: None,
    'save_data': lambda p, d: print(f"[SAVE] Params={p}")
}

code = """
init_data(param=("v", "i"))
set dac 1.0
d = run("seq1", "code")
save(params=(1.0, 0.123), data=d)
"""

log_dir = '/home/mkim/entserver/quail/logs'
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

print("Testing file logging...")
processor = VirtualProcessor(context, log_dir=log_dir)
try:
    processor.execute(code)
    print("Execution successful!")
    
    # Check log file
    files = os.listdir(log_dir)
    files.sort()
    latest_log = os.path.join(log_dir, files[-1])
    print(f"Reading log file: {latest_log}")
    with open(latest_log, 'r') as f:
        print(f.read())
        
except Exception as e:
    print(f"Execution failed: {e}")
    import traceback
    traceback.print_exc()
