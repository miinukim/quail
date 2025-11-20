import re

class Token:
    def __init__(self, type, value, line=0):
        self.type = type
        self.value = value
        self.line = line
    
    def __repr__(self):
        return f"Token({self.type}, {self.value})"

class Lexer:
    def __init__(self, code):
        self.code = code
        self.pos = 0
        self.line = 1
        self.current_char = self.code[self.pos] if self.pos < len(self.code) else None

    def advance(self):
        self.pos += 1
        if self.pos < len(self.code):
            self.current_char = self.code[self.pos]
        else:
            self.current_char = None

    def skip_whitespace(self):
        while self.current_char is not None and self.current_char.isspace():
            if self.current_char == '\n':
                self.line += 1
            self.advance()

    def number(self):
        result = ''
        while self.current_char is not None and (self.current_char.isdigit() or self.current_char == '.'):
            result += self.current_char
            self.advance()
        
        if '.' in result:
            return Token('NUMBER', float(result), self.line)
        else:
            return Token('NUMBER', int(result), self.line)

    def string(self):
        self.advance() # Skip opening quote
        result = ''
        while self.current_char is not None and self.current_char != '"':
            result += self.current_char
            self.advance()
        self.advance() # Skip closing quote
        return Token('STRING', result, self.line)

    def identifier(self):
        result = ''
        while self.current_char is not None and (self.current_char.isalnum() or self.current_char == '_' or self.current_char == '.'):
            result += self.current_char
            self.advance()
        
        keywords = {
            'param': 'PARAM', 'set': 'SET', 'run': 'RUN',
            'save': 'SAVE', 'sleep': 'SLEEP', 'for': 'FOR', 'in': 'IN',
            'from': 'FROM', 'to': 'TO', 'step': 'STEP', 'get': 'GET',
            'init_data': 'INIT_DATA', 'if': 'IF', 'else': 'ELSE'
        }
        
        return Token(keywords.get(result, 'IDENTIFIER'), result, self.line)

    def get_next_token(self):
        while self.current_char is not None:
            if self.current_char.isspace():
                self.skip_whitespace()
                continue
            
            if self.current_char.isdigit():
                return self.number()
            
            if self.current_char == '"':
                return self.string()
            
            if self.current_char.isalpha():
                return self.identifier()
            
            if self.current_char == '=':
                self.advance()
                if self.current_char == '=':
                    self.advance()
                    return Token('EQ', '==', self.line)
                return Token('ASSIGN', '=', self.line)
            
            if self.current_char == '!':
                self.advance()
                if self.current_char == '=':
                    self.advance()
                    return Token('NEQ', '!=', self.line)
                raise Exception(f"Invalid character '!' at line {self.line}")

            if self.current_char == '<':
                self.advance()
                if self.current_char == '=':
                    self.advance()
                    return Token('LTE', '<=', self.line)
                return Token('LT', '<', self.line)

            if self.current_char == '>':
                self.advance()
                if self.current_char == '=':
                    self.advance()
                    return Token('GTE', '>=', self.line)
                return Token('GT', '>', self.line)
            
            if self.current_char == '(':
                self.advance()
                return Token('LPAREN', '(', self.line)
            
            if self.current_char == ')':
                self.advance()
                return Token('RPAREN', ')', self.line)
            
            if self.current_char == '{':
                self.advance()
                return Token('LBRACE', '{', self.line)
            
            if self.current_char == '}':
                self.advance()
                return Token('RBRACE', '}', self.line)
            
            if self.current_char == ',':
                self.advance()
                return Token('COMMA', ',', self.line)
            
            if self.current_char == '#':
                # Comment
                while self.current_char is not None and self.current_char != '\n':
                    self.advance()
                continue

            raise Exception(f"Invalid character '{self.current_char}' at line {self.line}")
        
        return Token('EOF', None, self.line)

class Parser:
    def __init__(self, lexer):
        self.lexer = lexer
        self.current_token = self.lexer.get_next_token()

    def error(self, msg):
        raise Exception(f"Parser Error at line {self.current_token.line}: {msg}")

    def eat(self, token_type):
        if self.current_token.type == token_type:
            self.current_token = self.lexer.get_next_token()
        else:
            self.error(f"Expected {token_type}, got {self.current_token.type}")

    def program(self):
        statements = []
        while self.current_token.type != 'EOF':
            statements.append(self.statement())
        return statements

    def statement(self):
        if self.current_token.type == 'PARAM':
            return self.param_decl()
        elif self.current_token.type == 'SET':
            return self.set_cmd()
        elif self.current_token.type == 'RUN':
            return self.run_cmd()
        elif self.current_token.type == 'IDENTIFIER':
            return self.assignment()
        elif self.current_token.type == 'SAVE':
            return self.save_cmd()
        elif self.current_token.type == 'INIT_DATA':
            return self.init_data_cmd()
        elif self.current_token.type == 'SLEEP':
            return self.sleep_cmd()
        elif self.current_token.type == 'FOR':
            return self.for_loop()
        elif self.current_token.type == 'IF':
            return self.if_stmt()
        else:
            self.error(f"Unexpected token {self.current_token.type}")

    def param_decl(self):
        self.eat('PARAM')
        name = self.current_token.value
        self.eat('IDENTIFIER')
        self.eat('ASSIGN')
        val = self.expression()
        return {'type': 'PARAM', 'name': name, 'value': val}

    def set_cmd(self):
        self.eat('SET')
        name = self.current_token.value
        self.eat('IDENTIFIER')
        val = self.expression()
        return {'type': 'SET', 'name': name, 'value': val}

    def run_cmd(self):
        self.eat('RUN')
        self.eat('LPAREN')
        args = []
        if self.current_token.type != 'RPAREN':
            args.append(self.expression())
            while self.current_token.type == 'COMMA':
                self.eat('COMMA')
                args.append(self.expression())
        self.eat('RPAREN')
        return {'type': 'RUN', 'args': args, 'targets': []}

    def assignment(self):
        # d1, d2 = run(...) OR var = expr
        targets = []
        targets.append(self.current_token.value)
        self.eat('IDENTIFIER')
        
        while self.current_token.type == 'COMMA':
            self.eat('COMMA')
            if self.current_token.type == 'ASSIGN':
                break
            targets.append(self.current_token.value)
            self.eat('IDENTIFIER')
            
        self.eat('ASSIGN')
        
        if self.current_token.type == 'RUN':
            cmd = self.run_cmd()
            cmd['targets'] = targets
            return cmd
        else:
            # General assignment: var = expr
            if len(targets) != 1:
                self.error("Multiple assignment only supported for RUN command")
            val = self.expression()
            return {'type': 'ASSIGN', 'name': targets[0], 'value': val}

    def init_data_cmd(self):
        self.eat('INIT_DATA')
        self.eat('LPAREN')
        kwargs = {}
        while self.current_token.type != 'RPAREN':
            if self.current_token.type == 'PARAM':
                key = 'param'
                self.eat('PARAM')
            else:
                key = self.current_token.value
                self.eat('IDENTIFIER')
            
            self.eat('ASSIGN')
            val = self.expression()
            kwargs[key] = val
            if self.current_token.type == 'COMMA':
                self.eat('COMMA')
        self.eat('RPAREN')
        return {'type': 'INIT_DATA', 'kwargs': kwargs}

    def save_cmd(self):
        self.eat('SAVE')
        self.eat('LPAREN')
        kwargs = {}
        while self.current_token.type != 'RPAREN':
            key = self.current_token.value
            self.eat('IDENTIFIER')
            self.eat('ASSIGN')
            val = self.expression()
            kwargs[key] = val
            if self.current_token.type == 'COMMA':
                self.eat('COMMA')
        self.eat('RPAREN')
        return {'type': 'SAVE', 'kwargs': kwargs}

    def sleep_cmd(self):
        self.eat('SLEEP')
        self.eat('LPAREN')
        duration = self.expression()
        self.eat('RPAREN')
        return {'type': 'SLEEP', 'duration': duration}

    def for_loop(self):
        self.eat('FOR')
        var_name = self.current_token.value
        self.eat('IDENTIFIER')
        self.eat('IN')
        self.eat('LPAREN')
        self.eat('FROM')
        start = self.expression()
        self.eat('TO')
        end = self.expression()
        step = 1.0
        if self.current_token.type == 'STEP':
            self.eat('STEP')
            step = self.expression()
        self.eat('RPAREN')
        
        self.eat('LBRACE')
        body = []
        while self.current_token.type != 'RBRACE':
            body.append(self.statement())
        self.eat('RBRACE')
        
        return {
            'type': 'FOR',
            'var': var_name,
            'start': start,
            'end': end,
            'step': step,
            'body': body
        }

    def if_stmt(self):
        self.eat('IF')
        self.eat('LPAREN')
        condition = self.expression()
        self.eat('RPAREN')
        self.eat('LBRACE')
        
        true_body = []
        while self.current_token.type != 'RBRACE':
            true_body.append(self.statement())
        self.eat('RBRACE')
        
        false_body = None
        if self.current_token.type == 'ELSE':
            self.eat('ELSE')
            self.eat('LBRACE')
            false_body = []
            while self.current_token.type != 'RBRACE':
                false_body.append(self.statement())
            self.eat('RBRACE')
            
        return {'type': 'IF', 'condition': condition, 'true_body': true_body, 'false_body': false_body}

    def expression(self):
        # Simple expression parser: only supports basic types and comparisons for now
        # Need to expand for full expression support (precedence, etc.)
        # For now, let's handle comparisons as a flat list or simple binary op
        
        left = self.term()
        
        while self.current_token.type in ('EQ', 'NEQ', 'LT', 'LTE', 'GT', 'GTE'):
            op = self.current_token.type
            self.eat(op)
            right = self.term()
            left = {'type': 'BINOP', 'left': left, 'op': op, 'right': right}
            
        return left

    def term(self):
        if self.current_token.type == 'NUMBER':
            token = self.current_token
            self.eat('NUMBER')
            return token.value
        elif self.current_token.type == 'STRING':
            token = self.current_token
            self.eat('STRING')
            return token.value
        elif self.current_token.type == 'IDENTIFIER':
            name = self.current_token.value
            self.eat('IDENTIFIER')
            return {'type': 'VAR', 'name': name}
        elif self.current_token.type == 'GET':
            self.eat('GET')
            name = self.current_token.value
            self.eat('IDENTIFIER')
            return {'type': 'GET', 'target': name}
        elif self.current_token.type == 'LPAREN':
            # Tuple: (a, b, ...)
            self.eat('LPAREN')
            items = []
            items.append(self.expression())
            while self.current_token.type == 'COMMA':
                self.eat('COMMA')
                if self.current_token.type == 'RPAREN':
                    break
                items.append(self.expression())
            self.eat('RPAREN')
            return {'type': 'TUPLE', 'items': items}
        else:
            self.error(f"Unexpected token in expression: {self.current_token.type}")

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
            
            # Mock return data
            import random
            # Return as many vectors as targets, or 1 if no targets
            num_returns = len(stmt['targets']) if stmt['targets'] else 1
            # Mock data: vectors of length 100
            data = [[random.random() for _ in range(100)] for _ in range(num_returns)]
            
            # In reality: self.context['instruments'].run_fpga(*args)
            
            if not stmt['targets']:
                 pass # Just run
            else:
                for i, target in enumerate(stmt['targets']):
                    if i < len(data):
                        self.vars[target] = data[i]
                        self.context['log'](f"Assigned data to {target}")

        elif stype == 'INIT_DATA':
            kwargs = {k: self.evaluate(v) for k, v in stmt['kwargs'].items()}
            
            if 'param' not in kwargs or 'num_data' not in kwargs:
                raise Exception("init_data requires 'param' and 'num_data' arguments")
            
            params = kwargs['param']
            if not isinstance(params, (list, tuple)):
                raise Exception("'param' argument in init_data must be a tuple of strings")
                
            num_data = kwargs['num_data']
            if not isinstance(num_data, int):
                raise Exception("'num_data' argument in init_data must be an integer")
                
            self.data_schema = {
                'param_axes': params,
                'num_data': num_data
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
                
            # Validate data
            if not isinstance(data, (list, tuple)):
                # Allow single value if schema expects 1 data
                if self.data_schema['num_data'] == 1:
                    data = (data,)
                else:
                    raise Exception(f"save() 'data' must be a tuple of length {self.data_schema['num_data']}")
            
            if len(data) != self.data_schema['num_data']:
                raise Exception(f"Expected {self.data_schema['num_data']} data vectors, got {len(data)}")
                
            # Validate data lengths
            vector_len = None
            for i, d in enumerate(data):
                if not isinstance(d, (list, tuple)):
                     raise Exception(f"Data item {i} is not a vector (list/tuple)")
                if vector_len is None:
                    vector_len = len(d)
                elif len(d) != vector_len:
                    raise Exception(f"Data vector lengths mismatch: {vector_len} vs {len(d)}")
            
            self.context['log'](f"Saving valid data block: params={params}, data_shape=({len(data)}, {vector_len})")
            
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
            
            curr = start
            while curr <= end:
                self.vars[var] = curr
                self.run_block(stmt['body'])
                curr += step
