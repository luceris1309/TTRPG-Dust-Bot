# core/parser.py
# Lexer + Parser xây dựng AST từ chuỗi biểu thức

import re
from typing import List, Optional, Union, Any, Tuple
from dataclasses import dataclass
from enum import Enum
from utils.helpers import get_logger

logger = get_logger()

# -------------------- TOKEN TYPES --------------------
class TokenType(Enum):
    NUMBER = "NUMBER"
    STRING = "STRING"
    VARIABLE = "VARIABLE"
    IDENTIFIER = "IDENTIFIER"
    OPERATOR = "OPERATOR"
    LPAREN = "LPAREN"
    RPAREN = "RPAREN"
    COMMA = "COMMA"
    DIRECTIVE_EQ = "DIRECTIVE_EQ"
    DIRECTIVE_MACRO = "DIRECTIVE_MACRO"
    DIRECTIVE_EFFECT = "DIRECTIVE_EFFECT"
    EOF = "EOF"

@dataclass
class Token:
    type: TokenType
    value: Any
    line: int
    col: int

# -------------------- AST NODES --------------------
class ASTNode:
    pass

@dataclass
class NumberNode(ASTNode):
    value: Union[int, float]

@dataclass
class StringNode(ASTNode):
    value: str

@dataclass
class VariableNode(ASTNode):
    name: str
    index: Optional[str]

@dataclass
class BinOpNode(ASTNode):
    left: ASTNode
    op: str
    right: ASTNode

@dataclass
class FunctionCallNode(ASTNode):
    name: str
    args: List[ASTNode]

@dataclass
class DirectiveOverwriteNode(ASTNode):
    target_var: VariableNode
    expression: ASTNode

@dataclass
class DirectiveMacroNode(ASTNode):
    macro_name: str
    tag: str
    expression: ASTNode

@dataclass
class DirectiveEffectNode(ASTNode):
    effect_name: str
    tag: str
    expression: ASTNode

# -------------------- LEXER --------------------
class Lexer:
    def __init__(self, text: str):
        self.text = text
        self.pos = 0
        self.line = 1
        self.col = 1
        self.current_char = text[0] if text else None
    
    def advance(self):
        if self.current_char == '\n':
            self.line += 1
            self.col = 0
        self.pos += 1
        self.col += 1
        if self.pos < len(self.text):
            self.current_char = self.text[self.pos]
        else:
            self.current_char = None
    
    def skip_whitespace(self):
        while self.current_char and self.current_char.isspace():
            self.advance()
    
    def read_number(self) -> Token:
        start_col = self.col
        num_str = ''
        while self.current_char and (self.current_char.isdigit() or self.current_char == '.'):
            num_str += self.current_char
            self.advance()
        if '.' in num_str:
            return Token(TokenType.NUMBER, float(num_str), self.line, start_col)
        return Token(TokenType.NUMBER, int(num_str), self.line, start_col)
    
    def read_string(self) -> Token:
        start_col = self.col
        self.advance()
        value = ''
        while self.current_char and self.current_char != '"':
            if self.current_char == '\\':
                self.advance()
                if self.current_char == 'n':
                    value += '\n'
                elif self.current_char == 't':
                    value += '\t'
                else:
                    value += self.current_char
            else:
                value += self.current_char
            self.advance()
        self.advance()
        return Token(TokenType.STRING, value, self.line, start_col)
    
    def read_identifier(self) -> Token:
        start_col = self.col
        ident = ''
        while self.current_char and (self.current_char.isalnum() or self.current_char == '_'):
            ident += self.current_char
            self.advance()
        return Token(TokenType.IDENTIFIER, ident, self.line, start_col)
    
    def read_variable(self) -> Token:
        start_col = self.col
        self.advance()
        name = ''
        while self.current_char and self.current_char != '}' and self.current_char != '.':
            name += self.current_char
            self.advance()
        index = None
        if self.current_char == '.':
            self.advance()
            index = ''
            while self.current_char and self.current_char != '}':
                index += self.current_char
                self.advance()
        if self.current_char != '}':
            raise SyntaxError(f"Thiếu }} tại dòng {self.line}, cột {self.col}")
        self.advance()
        return Token(TokenType.VARIABLE, (name, index), self.line, start_col)
    
    def try_directive_eq(self) -> Optional[Token]:
        """Kiểm tra xem có phải overwrite directive {var}=... không"""
        pos_bak = self.pos
        line_bak = self.line
        col_bak = self.col
        try:
            self.advance()
            name = ''
            while self.current_char and self.current_char != '}' and self.current_char != '.':
                name += self.current_char
                self.advance()
            index = None
            if self.current_char == '.':
                self.advance()
                index = ''
                while self.current_char and self.current_char != '}':
                    index += self.current_char
                    self.advance()
            if self.current_char != '}':
                raise Exception
            self.advance()
            if self.current_char == '=':
                var_node = VariableNode(name, index)
                return Token(TokenType.DIRECTIVE_EQ, var_node, line_bak, col_bak)
        except:
            pass
        self.pos = pos_bak
        self.line = line_bak
        self.col = col_bak
        self.current_char = self.text[self.pos] if self.pos < len(self.text) else None
        return None
    
    def try_directive_macro(self) -> Optional[Token]:
        pos_bak = self.pos
        line_bak = self.line
        col_bak = self.col
        try:
            self.advance()
            name = ''
            while self.current_char and self.current_char != '}':
                name += self.current_char
                self.advance()
            if self.current_char != '}':
                raise Exception
            self.advance()
            tag = ''
            while self.current_char and self.current_char.isalpha():
                tag += self.current_char
                self.advance()
            if not tag:
                raise Exception
            if self.current_char == '/' and self.pos + 1 < len(self.text) and self.text[self.pos + 1] == '/':
                self.advance()
                self.advance()
                return Token(TokenType.DIRECTIVE_MACRO, (name, tag), line_bak, col_bak)
        except:
            pass
        self.pos = pos_bak
        self.line = line_bak
        self.col = col_bak
        self.current_char = self.text[self.pos] if self.pos < len(self.text) else None
        return None
    
    def try_directive_effect(self) -> Optional[Token]:
        pos_bak = self.pos
        line_bak = self.line
        col_bak = self.col
        try:
            self.advance()
            name = ''
            while self.current_char and self.current_char != ']':
                name += self.current_char
                self.advance()
            if self.current_char != ']':
                raise Exception
            self.advance()
            tag = ''
            while self.current_char and self.current_char.isalpha():
                tag += self.current_char
                self.advance()
            if self.current_char != '#':
                raise Exception
            self.advance()
            return Token(TokenType.DIRECTIVE_EFFECT, (name, tag), line_bak, col_bak)
        except:
            pass
        self.pos = pos_bak
        self.line = line_bak
        self.col = col_bak
        self.current_char = self.text[self.pos] if self.pos < len(self.text) else None
        return None
    
    def get_next_token(self) -> Token:
        while self.current_char:
            if self.current_char.isspace():
                self.skip_whitespace()
                continue
            if self.current_char.isdigit():
                return self.read_number()
            if self.current_char == '"':
                return self.read_string()
            if self.current_char == '{':
                tok = self.try_directive_eq()
                if tok:
                    return tok
                tok = self.try_directive_macro()
                if tok:
                    return tok
                return self.read_variable()
            if self.current_char == '[':
                tok = self.try_directive_effect()
                if tok:
                    return tok
                raise SyntaxError(f"Lỗi cú pháp effect tại {self.line}:{self.col}")
            if self.current_char.isalpha() or self.current_char == '_':
                return self.read_identifier()
            if self.current_char in '+-*/&':
                op = self.current_char
                self.advance()
                return Token(TokenType.OPERATOR, op, self.line, self.col-1)
            if self.current_char == '(':
                self.advance()
                return Token(TokenType.LPAREN, '(', self.line, self.col-1)
            if self.current_char == ')':
                self.advance()
                return Token(TokenType.RPAREN, ')', self.line, self.col-1)
            if self.current_char == ',':
                self.advance()
                return Token(TokenType.COMMA, ',', self.line, self.col-1)
            raise SyntaxError(f"Ký tự không hợp lệ: {self.current_char} tại {self.line}:{self.col}")
        return Token(TokenType.EOF, None, self.line, self.col)

# -------------------- PARSER --------------------
class Parser:
    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.pos = 0
        self.current_token = tokens[0] if tokens else Token(TokenType.EOF, None, 0, 0)
    
    def advance(self):
        self.pos += 1
        if self.pos < len(self.tokens):
            self.current_token = self.tokens[self.pos]
        else:
            self.current_token = Token(TokenType.EOF, None, 0, 0)
    
    def eat(self, token_type: TokenType):
        if self.current_token.type == token_type:
            self.advance()
        else:
            raise SyntaxError(f"Expected {token_type}, got {self.current_token.type}")
    
    def parse(self) -> ASTNode:
        if self.current_token.type == TokenType.DIRECTIVE_EQ:
            return self.parse_directive_overwrite()
        elif self.current_token.type == TokenType.DIRECTIVE_MACRO:
            return self.parse_directive_macro()
        elif self.current_token.type == TokenType.DIRECTIVE_EFFECT:
            return self.parse_directive_effect()
        else:
            return self.parse_expression()
    
    def parse_directive_overwrite(self) -> DirectiveOverwriteNode:
        token = self.current_token
        self.eat(TokenType.DIRECTIVE_EQ)
        target_var = token.value
        expr = self.parse_expression()
        return DirectiveOverwriteNode(target_var, expr)
    
    def parse_directive_macro(self) -> DirectiveMacroNode:
        token = self.current_token
        self.eat(TokenType.DIRECTIVE_MACRO)
        macro_name, tag = token.value
        expr = self.parse_expression()
        return DirectiveMacroNode(macro_name, tag, expr)
    
    def parse_directive_effect(self) -> DirectiveEffectNode:
        token = self.current_token
        self.eat(TokenType.DIRECTIVE_EFFECT)
        effect_name, tag = token.value
        expr = self.parse_expression()
        return DirectiveEffectNode(effect_name, tag, expr)
    
    def parse_expression(self) -> ASTNode:
        return self.parse_binary_op()
    
    def parse_binary_op(self, min_precedence: int = 0) -> ASTNode:
        precedence = {'&': 1, '+': 2, '-': 2, '*': 3, '/': 3}
        left = self.parse_primary()
        while self.current_token.type == TokenType.OPERATOR:
            op = self.current_token.value
            if precedence.get(op, 0) < min_precedence:
                break
            self.advance()
            right = self.parse_binary_op(precedence[op] + 1)
            left = BinOpNode(left, op, right)
        return left
    
    def parse_primary(self) -> ASTNode:
        token = self.current_token
        if token.type == TokenType.NUMBER:
            self.advance()
            return NumberNode(token.value)
        elif token.type == TokenType.STRING:
            self.advance()
            return StringNode(token.value)
        elif token.type == TokenType.VARIABLE:
            self.advance()
            name, index = token.value
            return VariableNode(name, index)
        elif token.type == TokenType.IDENTIFIER:
            func_name = token.value
            self.advance()
            if self.current_token.type != TokenType.LPAREN:
                raise SyntaxError(f"Expected '(' after {func_name}")
            self.eat(TokenType.LPAREN)
            args = []
            if self.current_token.type != TokenType.RPAREN:
                args.append(self.parse_expression())
                while self.current_token.type == TokenType.COMMA:
                    self.eat(TokenType.COMMA)
                    args.append(self.parse_expression())
            self.eat(TokenType.RPAREN)
            return FunctionCallNode(func_name, args)
        elif token.type == TokenType.LPAREN:
            self.eat(TokenType.LPAREN)
            expr = self.parse_expression()
            self.eat(TokenType.RPAREN)
            return expr
        else:
            raise SyntaxError(f"Unexpected token: {token.type}")

# -------------------- PUBLIC API --------------------
def tokenize(text: str) -> List[Token]:
    lexer = Lexer(text)
    tokens = []
    while True:
        tok = lexer.get_next_token()
        tokens.append(tok)
        if tok.type == TokenType.EOF:
            break
    return tokens

def parse(text: str) -> ASTNode:
    tokens = tokenize(text)
    parser = Parser(tokens)
    return parser.parse()