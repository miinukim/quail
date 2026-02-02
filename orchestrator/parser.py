"""
Lexer and Parser classes for the QuAIL custom experiment syntax.
It is responsible for tokenizing the input code and generating an Abstract Syntax Tree (AST).
"""
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
        """
        Initialize the Lexer with the source code.
        """
        self.code = code
        self.pos = 0
        self.line = 1
        self.current_char = self.code[self.pos] if self.pos < len(self.code) else None

    def advance(self):
        """
        Advance the `pos` pointer and update the `current_char`.
        """
        self.pos += 1
        if self.pos < len(self.code):
            self.current_char = self.code[self.pos]
        else:
            self.current_char = None

    def peek(self):
        """
        Look ahead one character without consuming it.
        """
        nxt = self.pos + 1
        if nxt < len(self.code):
            return self.code[nxt]
        return None

    def skip_whitespace(self):
        """
        Skip whitespace characters.
        """
        while self.current_char is not None and self.current_char.isspace():
            if self.current_char == '\n':
                self.line += 1
            self.advance()

    def number(self, negative=False):
        """
        Return a (multidigit) integer or float consumed from the input.
        """
        result = '-' if negative else ''
        while self.current_char is not None and (self.current_char.isdigit() or self.current_char == '.'):
            result += self.current_char
            self.advance()
        
        if '.' in result:
            return Token('NUMBER', float(result), self.line)
        else:
            return Token('NUMBER', int(result), self.line)

    def string(self):
        """
        Return a string consumed from the input.
        """
        self.advance() # Skip opening quote
        result = ''
        while self.current_char is not None and self.current_char != '"':
            result += self.current_char
            self.advance()
        self.advance() # Skip closing quote
        return Token('STRING', result, self.line)

    def identifier(self):
        """
        Handle identifiers and reserved keywords.
        """
        result = ''
        while self.current_char is not None and (self.current_char.isalnum() or self.current_char == '_' or self.current_char == '.'):
            result += self.current_char
            self.advance()

        if result in ('true', 'false', 'True', 'False'):
            return Token('BOOLEAN', result.lower() == 'true', self.line)

        keywords = {
            'param': 'PARAM', 'dynp': 'DYN_PARAM', 'set': 'SET', 'run': 'RUN',
            'save': 'SAVE', 'sleep': 'SLEEP', 'for': 'FOR', 'in': 'IN',
            'from': 'FROM', 'to': 'TO', 'step': 'STEP', 'get': 'GET',
            'init_data': 'INIT_DATA', 'if': 'IF', 'else': 'ELSE'
        }
        
        return Token(keywords.get(result, 'IDENTIFIER'), result, self.line)

    def get_next_token(self):
        """
        Lexical analyzer (also known as scanner or tokenizer).
        This method is responsible for breaking a sentence
        apart into tokens. One token at a time.
        """
        while self.current_char is not None:
            if self.current_char.isspace():
                self.skip_whitespace()
                continue
            
            if self.current_char.isdigit():
                return self.number()

            if self.current_char == '-' and (self.peek() and (self.peek().isdigit() or self.peek() == '.')):
                self.advance()
                return self.number(negative=True)
            
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

            if self.current_char == ';':
                self.advance()
                return Token('SEMI', ';', self.line)

            if self.current_char == ':':
                self.advance()
                return Token('COLON', ':', self.line)
            
            if self.current_char == '#':
                # Comment
                while self.current_char is not None and self.current_char != '\n':
                    self.advance()
                continue

            raise Exception(f"Invalid character '{self.current_char}' at line {self.line}")
        
        return Token('EOF', None, self.line)

class Parser:
    def __init__(self, lexer):
        """
        Initialize the Parser with a Lexer instance.
        """
        self.lexer = lexer
        self.current_token = self.lexer.get_next_token()

    def error(self, msg):
        """
        Raise a parser error.
        """
        raise Exception(f"Parser Error at line {self.current_token.line}: {msg}")

    def process_token(self, token_type):
        """
        Compare the current token type with the passed token
        type and if they match then "process_token" the current token
        and assign the next token to the self.current_token,
        otherwise raise an exception.
        """
        if self.current_token.type == token_type:
            self.current_token = self.lexer.get_next_token()
        else:
            self.error(f"Expected {token_type}, got {self.current_token.type}")

    def program(self):
        """
        program : statement*
        """
        statements = []
        while self.current_token.type != 'EOF':
            statements.append(self.statement())
        return statements

    def statement(self):
        """
        statement : param_decl | dyn_param_decl | set_cmd | run_cmd | assignment | save_cmd | init_data_cmd | sleep_cmd | for_loop | if_stmt
        """
        if self.current_token.type == 'PARAM':
            node = self.param_decl()
            self.process_token('SEMI')
            return node
        elif self.current_token.type == 'DYN_PARAM':
            node = self.dyn_param_decl()
            self.process_token('SEMI')
            return node
        elif self.current_token.type == 'SET':
            node = self.set_cmd()
            self.process_token('SEMI')
            return node
        elif self.current_token.type == 'RUN':
            node = self.run_cmd()
            self.process_token('SEMI')
            return node
        elif self.current_token.type == 'IDENTIFIER':
            node = self.assignment()
            self.process_token('SEMI')
            return node
        elif self.current_token.type == 'SAVE':
            node = self.save_cmd()
            self.process_token('SEMI')
            return node
        elif self.current_token.type == 'INIT_DATA':
            node = self.init_data_cmd()
            self.process_token('SEMI')
            return node
        elif self.current_token.type == 'SLEEP':
            node = self.sleep_cmd()
            self.process_token('SEMI')
            return node
        elif self.current_token.type == 'FOR':
            return self.for_loop()
        elif self.current_token.type == 'IF':
            return self.if_stmt()
        else:
            self.error(f"Unexpected token {self.current_token.type}")

    def param_decl(self):
        """
        param_decl : PARAM (TYPE)? IDENTIFIER ASSIGN expression
        """
        self.process_token('PARAM')
        dtype = None
        if self.current_token.type == 'IDENTIFIER' and self.current_token.value in ('int', 'float', 'bool'):
            dtype = self.current_token.value
            self.process_token('IDENTIFIER')
        name = self.current_token.value
        self.process_token('IDENTIFIER')
        self.process_token('ASSIGN')
        val = self.expression()
        return {'type': 'PARAM', 'name': name, 'value': val, 'dtype': dtype}

    def dyn_param_decl(self):
        """
        dyn_param_decl : DYN_PARAM IDENTIFIER ASSIGN expression
        """
        self.process_token('DYN_PARAM')
        name = self.current_token.value
        self.process_token('IDENTIFIER')
        self.process_token('ASSIGN')
        val = self.expression()
        return {'type': 'DYN_PARAM', 'name': name, 'value': val}

    def set_cmd(self):
        """
        set_cmd : SET IDENTIFIER expression
        """
        self.process_token('SET')
        name = self.current_token.value
        self.process_token('IDENTIFIER')
        val = self.expression()
        return {'type': 'SET', 'name': name, 'value': val}

    def run_cmd(self):
        """
        run_cmd : RUN LPAREN (expression (COMMA expression)*)? RPAREN
        """
        self.process_token('RUN')
        self.process_token('LPAREN')
        args = []
        if self.current_token.type != 'RPAREN':
            args.append(self.expression())
            while self.current_token.type == 'COMMA':
                self.process_token('COMMA')
                args.append(self.expression())
        self.process_token('RPAREN')
        return {'type': 'RUN', 'args': args, 'target': None}

    def assignment(self):
        """
        assignment : IDENTIFIER ASSIGN (expression | run_cmd)
        """
        # var = run(...) OR var = expr
        target = self.current_token.value
        self.process_token('IDENTIFIER')
        
        if self.current_token.type == 'COMMA':
             self.error("Multiple assignment is no longer supported. Use 'var = run(...)'")
            
        self.process_token('ASSIGN')
        
        if self.current_token.type == 'RUN':
            cmd = self.run_cmd()
            cmd['target'] = target
            return cmd
        else:
            # General assignment: var = expr
            val = self.expression()
            return {'type': 'ASSIGN', 'name': target, 'value': val}

    def init_data_cmd(self):
        """
        init_data_cmd : INIT_DATA LPAREN (IDENTIFIER ASSIGN expression (COMMA IDENTIFIER ASSIGN expression)*)? RPAREN
        """
        self.process_token('INIT_DATA')
        self.process_token('LPAREN')
        kwargs = {}
        while self.current_token.type != 'RPAREN':
            if self.current_token.type == 'PARAM':
                key = 'param'
                self.process_token('PARAM')
            else:
                key = self.current_token.value
                self.process_token('IDENTIFIER')
            
            self.process_token('ASSIGN')
            val = self.expression()
            kwargs[key] = val
            if self.current_token.type == 'COMMA':
                self.process_token('COMMA')
        self.process_token('RPAREN')
        return {'type': 'INIT_DATA', 'kwargs': kwargs}

    def save_cmd(self):
        """
        save_cmd : SAVE LPAREN (IDENTIFIER ASSIGN expression (COMMA IDENTIFIER ASSIGN expression)*)? RPAREN
        """
        self.process_token('SAVE')
        self.process_token('LPAREN')
        kwargs = {}
        while self.current_token.type != 'RPAREN':
            key = self.current_token.value
            self.process_token('IDENTIFIER')
            self.process_token('ASSIGN')
            val = self.expression()
            kwargs[key] = val
            if self.current_token.type == 'COMMA':
                self.process_token('COMMA')
        self.process_token('RPAREN')
        return {'type': 'SAVE', 'kwargs': kwargs}

    def sleep_cmd(self):
        """
        sleep_cmd : SLEEP LPAREN expression RPAREN
        """
        self.process_token('SLEEP')
        self.process_token('LPAREN')
        duration = self.expression()
        self.process_token('RPAREN')
        return {'type': 'SLEEP', 'duration': duration}

    def for_loop(self):
        """
        for_loop : FOR IDENTIFIER IN LPAREN FROM expression TO expression (STEP expression)? RPAREN COLON LBRACE statement* RBRACE
        """
        self.process_token('FOR')
        var_name = self.current_token.value
        self.process_token('IDENTIFIER')
        self.process_token('IN')
        self.process_token('LPAREN')
        self.process_token('FROM')
        start = self.expression()
        self.process_token('TO')
        end = self.expression()
        step = 1.0
        if self.current_token.type == 'STEP':
            self.process_token('STEP')
            step = self.expression()
        self.process_token('RPAREN')
        self.process_token('COLON')
        
        self.process_token('LBRACE')
        body = []
        while self.current_token.type != 'RBRACE':
            body.append(self.statement())
        self.process_token('RBRACE')
        
        return {
            'type': 'FOR',
            'var': var_name,
            'start': start,
            'end': end,
            'step': step,
            'body': body
        }

    def if_stmt(self):
        """
        if_stmt : IF LPAREN expression RPAREN COLON LBRACE statement* RBRACE (ELSE COLON LBRACE statement* RBRACE)?
        """
        self.process_token('IF')
        self.process_token('LPAREN')
        condition = self.expression()
        self.process_token('RPAREN')
        self.process_token('COLON')
        self.process_token('LBRACE')
        
        true_body = []
        while self.current_token.type != 'RBRACE':
            true_body.append(self.statement())
        self.process_token('RBRACE')
        
        false_body = None
        if self.current_token.type == 'ELSE':
            self.process_token('ELSE')
            self.process_token('COLON')
            self.process_token('LBRACE')
            false_body = []
            while self.current_token.type != 'RBRACE':
                false_body.append(self.statement())
            self.process_token('RBRACE')
            
        return {'type': 'IF', 'condition': condition, 'true_body': true_body, 'false_body': false_body}

    def expression(self):
        """
        expression : term ((EQ | NEQ | LT | LTE | GT | GTE) term)*
        """
        # Simple expression parser: only supports basic types and comparisons for now
        # Need to expand for full expression support (precedence, etc.)
        # For now, let's handle comparisons as a flat list or simple binary op
        
        left = self.term()
        
        while self.current_token.type in ('EQ', 'NEQ', 'LT', 'LTE', 'GT', 'GTE'):
            op = self.current_token.type
            self.process_token(op)
            right = self.term()
            left = {'type': 'BINOP', 'left': left, 'op': op, 'right': right}
            
        return left

    def term(self):
        """
        term : NUMBER | STRING | BOOLEAN | IDENTIFIER | GET IDENTIFIER | LPAREN expression (COMMA expression)* RPAREN
        """
        if self.current_token.type == 'NUMBER':
            token = self.current_token
            self.process_token('NUMBER')
            return token.value
        elif self.current_token.type == 'BOOLEAN':
            token = self.current_token
            self.process_token('BOOLEAN')
            return token.value
        elif self.current_token.type == 'STRING':
            token = self.current_token
            self.process_token('STRING')
            return token.value
        elif self.current_token.type == 'IDENTIFIER':
            name = self.current_token.value
            self.process_token('IDENTIFIER')
            return {'type': 'VAR', 'name': name}
        elif self.current_token.type == 'GET':
            self.process_token('GET')
            name = self.current_token.value
            self.process_token('IDENTIFIER')
            return {'type': 'GET', 'target': name}
        elif self.current_token.type == 'LPAREN':
            # Tuple: (a, b, ...)
            self.process_token('LPAREN')
            items = []
            items.append(self.expression())
            while self.current_token.type == 'COMMA':
                self.process_token('COMMA')
                if self.current_token.type == 'RPAREN':
                    break
                items.append(self.expression())
            self.process_token('RPAREN')
            return {'type': 'TUPLE', 'items': items}
        else:
            self.error(f"Unexpected token in expression: {self.current_token.type}")
