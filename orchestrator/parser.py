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
        return {'type': 'RUN', 'args': args, 'target': None}

    def assignment(self):
        # var = run(...) OR var = expr
        target = self.current_token.value
        self.eat('IDENTIFIER')
        
        if self.current_token.type == 'COMMA':
             self.error("Multiple assignment is no longer supported. Use 'var = run(...)'")
            
        self.eat('ASSIGN')
        
        if self.current_token.type == 'RUN':
            cmd = self.run_cmd()
            cmd['target'] = target
            return cmd
        else:
            # General assignment: var = expr
            val = self.expression()
            return {'type': 'ASSIGN', 'name': target, 'value': val}

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


