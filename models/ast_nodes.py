from dataclasses import dataclass
from typing import List, Any, Optional


class ASTNode:
    pass


@dataclass
class NumberNode(ASTNode):
    value: float


@dataclass
class StringNode(ASTNode):
    value: str


@dataclass
class VariableNode(ASTNode):
    name: str
    index: Optional[str] = None


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