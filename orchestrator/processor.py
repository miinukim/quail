from .parser import Lexer, Parser

class VirtualProcessor:
    def __init__(self, context):
        self.context = context
        self.vars = {}   # name -> value (includes params)
        self.data_schema = None # { 'param_axes': [], 'num_data': N }

    def parse(self, code):
        lexer = Lexer(code)
        parser = Parser(lexer)
        return parser.program()

    def execute(self, code):
        ast = self.parse(code)
        self.run_block(ast)

    def evaluate(self, node):
        if isinstance(node, (int, float, str)):
            return node
        
        if isinstance(node, dict):
            if node['type'] == 'VAR':
                # Return value if it exists (local var), else return the name itself (potential param name)
                return self.vars.get(node['name'], node['name'])
            elif node['type'] == 'GET':
                target_name = node['target']
                # If target is a local var, get its value (which might be a param name)
                # If not, use the target name directly
                path = self.vars.get(target_name, target_name)
                
                # Try to get parameter
                # We assume path is "instrument.parameter" or just "parameter"
                if isinstance(path, str):
                    parts = path.split('.')
                    if len(parts) >= 2:
                        inst = parts[0]
                        param = '.'.join(parts[1:])
                        val = self.context['instruments'].get_parameter(inst, param)
                        self.context['log'](f"Got {path} = {val}")
                        return val
                    else:
                        # Try with single name (maybe instrument is implied or handled by server)
                        val = self.context['instruments'].get_parameter(None, path)
                        self.context['log'](f"Got {path} = {val}")
                        return val
                return 0 # Fallback
            elif node['type'] == 'TUPLE':
                return tuple(self.evaluate(item) for item in node['items'])
            elif node['type'] == 'BINOP':
                left = self.evaluate(node['left'])
                right = self.evaluate(node['right'])
                op = node['op']
                if op == 'EQ': return left == right
                elif op == 'NEQ': return left != right
                elif op == 'LT': return left < right
                elif op == 'LTE': return left <= right
                elif op == 'GT': return left > right
                elif op == 'GTE': return left >= right
        return node

    def run_block(self, statements):
        for stmt in statements:
            self.execute_statement(stmt)

    def execute_statement(self, stmt):
        stype = stmt['type']
        
        if stype == 'PARAM':
            val = self.evaluate(stmt['value'])
            self.vars[stmt['name']] = val
            self.context['log'](f"Defined param {stmt['name']} = {val}")
            
        elif stype == 'SET':
            val = self.evaluate(stmt['value'])
            target_name = stmt['name']
            
            if target_name in self.vars:
                self.vars[target_name] = val
            else:
                path = target_name
                if '.' in path:
                    parts = path.split('.')
                    inst = parts[0]
                    param = '.'.join(parts[1:])
                    self.context['instruments'].set_parameter(inst, param, val)
                    self.context['log'](f"Set {path} = {val}")
                else:
                    self.context['instruments'].set_parameter(None, path, val)
                    self.context['log'](f"Set {path} = {val}")

        elif stype == 'ASSIGN':
            val = self.evaluate(stmt['value'])
            self.vars[stmt['name']] = val
            # self.context['log'](f"Assigned {stmt['name']} = {val}")

        elif stype == 'RUN':
            args = [self.evaluate(a) for a in stmt['args']]
            self.context['log'](f"Running Sequencer with args: {args}")
            
            # In reality: data = self.context['instruments'].run_fpga(*args)
            # Mock return data: 4xN array
            import random
            N = 100
            # 4 rows of data
            data = [[random.random() for _ in range(N)] for _ in range(4)]
            
            target = stmt.get('target')
            if target:
                self.vars[target] = data
                self.context['log'](f"Assigned data to {target}")

        elif stype == 'INIT_DATA':
            kwargs = {k: self.evaluate(v) for k, v in stmt['kwargs'].items()}
            
            if 'param' not in kwargs:
                raise Exception("init_data requires 'param' argument")
            
            params = kwargs['param']
            if not isinstance(params, (list, tuple)):
                raise Exception("'param' argument in init_data must be a tuple of strings")
                
            self.data_schema = {
                'param_axes': params
            }
            self.context['log'](f"Initialized data schema: {self.data_schema}")

        elif stype == 'SAVE':
            if self.data_schema is None:
                raise Exception("Cannot call save() before init_data()")
                
            kwargs = {k: self.evaluate(v) for k, v in stmt['kwargs'].items()}
            
            if 'params' not in kwargs or 'data' not in kwargs:
                raise Exception("save() requires 'params' and 'data' arguments")
            
            params = kwargs['params']
            data = kwargs['data']
            
            # Validate params
            if not isinstance(params, (list, tuple)):
                # Allow single value if schema expects 1 param
                if len(self.data_schema['param_axes']) == 1:
                    params = (params,)
                else:
                    raise Exception(f"save() 'params' must be a tuple matching {self.data_schema['param_axes']}")
            
            if len(params) != len(self.data_schema['param_axes']):
                raise Exception(f"Expected {len(self.data_schema['param_axes'])} params, got {len(params)}")
                
            # Validate data: expect 4xN array (list of lists)
            if not isinstance(data, (list, tuple)):
                 raise Exception("save() 'data' must be a list/tuple (4xN array)")
            
            if len(data) != 4:
                raise Exception(f"Expected data to have 4 rows (4xN), got {len(data)}")
                
            # Validate data lengths
            vector_len = None
            for i, d in enumerate(data):
                if not isinstance(d, (list, tuple)):
                     raise Exception(f"Data row {i} is not a vector (list/tuple)")
                if vector_len is None:
                    vector_len = len(d)
                elif len(d) != vector_len:
                    raise Exception(f"Data vector lengths mismatch: {vector_len} vs {len(d)}")
            
            self.context['log'](f"Saving valid data block: params={params}, data_shape=(4, {vector_len})")
            
            if 'save_data' in self.context:
                self.context['save_data'](params, data)
            else:
                self.context['log']("WARNING: No save_data handler in context")

        elif stype == 'SLEEP':
            duration = self.evaluate(stmt['duration'])
            self.context['sleep'](duration)
            
        elif stype == 'FOR':
            start = self.evaluate(stmt['start'])
            end = self.evaluate(stmt['end'])
            step = self.evaluate(stmt['step'])
            var = stmt['var']
            
            # Create range
            # Handle float steps
            import numpy as np
            # Use a small epsilon to include the end value if it matches the step
            vals = np.arange(start, end + step/1000.0, step)
            
            for val in vals:
                self.vars[var] = float(val)
                self.run_block(stmt['body'])

        elif stype == 'IF':
            condition = self.evaluate(stmt['condition'])
            if condition:
                self.run_block(stmt['true_body'])
            elif stmt['false_body']:
                self.run_block(stmt['false_body'])
