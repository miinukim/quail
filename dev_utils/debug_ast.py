import sys
import os
import json

# Add project root to path
sys.path.append(os.getcwd())

from orchestrator.processor import VirtualProcessor

code = """
# Initialize data schema
init_data(param=("voltage",), num_data=1)

param dac = "instrument.dac_voltage"
set dac 0
for i in (from 0 to 5 step 1) {
    set dac i
    
    # Test IF statement
    if (i > 2) {
        d1, = run()
        save(params=(i,), data=(d1,))
    } else {
        # Do nothing or log
    }
}
"""

print("Raw Code:")
print(code)
print("-" * 20)

parser = VirtualProcessor({})
try:
    ast = parser.parse(code)
    print("Parsed AST (JSON-like structure):")
    #print(json.dumps(ast, indent=2))
    
    # Save to file
    output_path = os.path.join(os.path.dirname(__file__), 'ast_output.json')
    with open(output_path, 'w') as f:
        json.dump(ast, f, indent=2)
    print(f"\nAST saved to {output_path}")
except Exception as e:
    print(f"Error: {e}")
