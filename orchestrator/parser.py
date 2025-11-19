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
        return Token('NUMBER', float(result))

    def string(self):
        self.advance() # Skip opening quote
        result = ''
        while self.current_char is not None and self.current_char != '"':
            result += self.current_char
            self.advance()
        self.advance() # Skip closing quote
        return Token('STRING', result)

    def identifier(self):
        result = ''
        while self.current_char is not None and (self.current_char.isalnum() or self.current_char == '_'):
            result += self.current_char
            self.advance()
        
        keywords = {
            'param': 'PARAM', 'set': 'SET', 'run': 'RUN',
            'save': 'SAVE', 'sleep': 'SLEEP', 'for': 'FOR', 'in': 'IN',
            'from': 'FROM', 'to': 'TO', 'step': 'STEP', 'get': 'GET'
        }
        
        return Token(keywords.get(result, 'IDENTIFIER'), result)

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
                return Token('ASSIGN', '=')
            
            if self.current_char == '(':
                self.advance()
                return Token('LPAREN', '(')
            
            if self.current_char == ')':
                self.advance()
                return Token('RPAREN', ')')
            
            if self.current_char == '{':
                self.advance()
                return Token('LBRACE', '{')
            
            if self.current_char == '}':
                self.advance()
                return Token('RBRACE', '}')
            
            if self.current_char == ',':
                self.advance()
                return Token('COMMA', ',')
            
            if self.current_char == '#':
                # Comment
                while self.current_char is not None and self.current_char != '\n':
                    self.advance()
                continue

            raise Exception(f"Invalid character '{self.current_char}' at line {self.line}")
        
        return Token('EOF', None)

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
        elif self.current_token.type == 'SLEEP':
            return self.sleep_cmd()
        elif self.current_token.type == 'FOR':
            return self.for_loop()
        else:
            self.error(f"Unexpected token {self.current_token.value}")

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
        self.eat('RPAREN')
        return {'type': 'RUN', 'targets': []}

    def assignment(self):
        targets = []
        targets.append(self.current_token.value)
        self.eat('IDENTIFIER')
        
        while self.current_token.type == 'COMMA':
            self.eat('COMMA')
            targets.append(self.current_token.value)
            self.eat('IDENTIFIER')
            
        self.eat('ASSIGN')
        
        if self.current_token.type == 'RUN':
            self.run_cmd()
            return {'type': 'RUN', 'targets': targets}
        else:
            self.error("Assignment only supported for RUN command currently")

    def save_cmd(self):
        self.eat('SAVE')
        self.eat('LPAREN')
        args = {}
        while self.current_token.type != 'RPAREN':
            key = self.current_token.value
            self.eat('IDENTIFIER')
            self.eat('ASSIGN')
            val = self.expression()
            args[key] = val
            if self.current_token.type == 'COMMA':
                self.eat('COMMA')
        self.eat('RPAREN')
        return {'type': 'SAVE', 'data': args}

    def sleep_cmd(self):
        self.eat('SLEEP')
        self.eat('LPAREN')
        val = self.expression()
        self.eat('RPAREN')
        return {'type': 'SLEEP', 'duration': val}

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

    def expression(self):
        if self.current_token.type == 'NUMBER':
            val = self.current_token.value
            self.eat('NUMBER')
            return val
        elif self.current_token.type == 'STRING':
            val = self.current_token.value
            self.eat('STRING')
            return val
        elif self.current_token.type == 'IDENTIFIER':
            val = self.current_token.value
            self.eat('IDENTIFIER')
            return {'type': 'VAR', 'name': val}
        elif self.current_token.type == 'GET':
            self.eat('GET')
            target = self.current_token.value
            self.eat('IDENTIFIER')
            return {'type': 'GET', 'target': target}
        elif self.current_token.type == 'LPAREN':
            # Tuple: (a, b, ...)
            self.eat('LPAREN')
            items = []
            items.append(self.expression())
            while self.current_token.type == 'COMMA':
                self.eat('COMMA')
                items.append(self.expression())
            self.eat('RPAREN')
            return {'type': 'TUPLE', 'items': items}
        else:
            self.error(f"Expected expression, got {self.current_token.type}")

class CustomScriptParser:
    def __init__(self, context):
        self.context = context
        self.vars = {}   # name -> value (includes params)

    def parse(self, code):
        lexer = Lexer(code)
        parser = Parser(lexer)
        return parser.program()

    def execute(self, code):
        ast = self.parse(code)
        self.run_block(ast)

    def evaluate(self, expr):
        if isinstance(expr, dict):
            if expr['type'] == 'VAR':
                return self.vars.get(expr['name'], expr['name']) # Return name if not found (for alias usage)
            elif expr['type'] == 'GET':
                target_name = expr['target']
                # Resolve target if it's a variable holding a path string
                path = self.vars.get(target_name, target_name)
                # Assume path is "instrument.parameter"
                if isinstance(path, str) and '.' in path:
                    parts = path.split('.')
                    if len(parts) >= 2:
                        inst = parts[0]
                        param = '.'.join(parts[1:])
                        val = self.context['instruments'].get_parameter(inst, param)
                        self.context['log'](f"Got {path} = {val}")
                        return val
                return 0 # Fallback
            elif expr['type'] == 'TUPLE':
                return tuple(self.evaluate(item) for item in expr['items'])
        return expr

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
            path = self.vars.get(target_name, target_name)
            
            if isinstance(path, str) and '.' in path:
                parts = path.split('.')
                if len(parts) >= 2:
                    inst = parts[0]
                    param = '.'.join(parts[1:])
                    self.context['instruments'].set_parameter(inst, param, val)
                    self.context['log'](f"Set {path} = {val}")
            else:
                # Just a variable assignment
                self.vars[target_name] = val
                
        elif stype == 'RUN':
            self.context['log']("Running Sequencer...")
            import random
            data = [[random.random() for _ in range(10)] for _ in range(len(stmt['targets']))]
            
            if not stmt['targets']:
                 self.context['instruments'].trigger_fpga()
            else:
                for i, target in enumerate(stmt['targets']):
                    if i < len(data):
                        self.vars[target] = data[i]
                        self.context['log'](f"Assigned data to {target}")
            
        elif stype == 'SAVE':
            data = {k: self.evaluate(v) for k, v in stmt['data'].items()}
            self.context['log'](f"Saving data: {data}")
            
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
