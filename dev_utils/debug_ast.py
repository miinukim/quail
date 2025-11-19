import sys
import os
import json

# Add project root to path
sys.path.append(os.getcwd())

from orchestrator.parser import CustomScriptParser

code = """
param dac = "instrument.dac_voltage"
set dac 0
for i in (from 0 to 5 step 1) {
    set dac i
    run_sequencer()
    save(voltage=i)
}
"""

print("Raw Code:")
print(code)
print("-" * 20)

parser = CustomScriptParser({})
try:
    ast = parser.parse(code)
    print("Parsed AST (JSON-like structure):")
    print(json.dumps(ast, indent=2))
except Exception as e:
    print(f"Error: {e}")
