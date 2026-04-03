import asyncio
from typing import Any, Dict, List, Optional, Union
from models.ast_nodes import (
    ASTNode, NumberNode, StringNode, VariableNode, BinOpNode,
    FunctionCallNode, DirectiveOverwriteNode, DirectiveMacroNode, DirectiveEffectNode
)
from models.actor import ActorRuntime
from models.combat_models import CombatActor
from core.function_registry import FunctionRegistry
from utils.helpers import get_logger, safe_divide

logger = get_logger()

class Evaluator:
    """
    Duyệt và tính giá trị AST.
    Hỗ trợ đệ quy, biến cục bộ, ngữ cảnh self/target.
    """
    def __init__(self, function_registry: FunctionRegistry, max_recursion_depth: int = 10):
        self.function_registry = function_registry
        self.max_recursion_depth = max_recursion_depth
        self.current_depth = 0
        self.current_actor: Optional[Union[ActorRuntime, CombatActor]] = None
        self.target_list: List[Union[ActorRuntime, CombatActor]] = []
        self.local_vars: Dict[str, Any] = {}
        self.global_config: Dict[str, str] = {}       # từ sheet CONFIG
        self.global_effect_pool: Dict[str, str] = {}  # từ sheet Effect
        self.in_combat: bool = False                  # macro gọi trong combat hay ngoài
    
    def reset_context(self):
        """Reset context giữa các lần evaluate (macro riêng biệt)"""
        self.local_vars.clear()
        self.current_depth = 0
        self.target_list = []
    
    async def evaluate(self, node: ASTNode) -> Any:
        """Điểm vào chính, tăng depth kiểm tra đệ quy"""
        self.current_depth += 1
        if self.current_depth > self.max_recursion_depth:
            self.current_depth -= 1
            logger.warning(f"Vượt quá độ sâu đệ quy cho phép: {self.max_recursion_depth}")
            return 0
        try:
            result = await self._eval(node)
            return result
        finally:
            self.current_depth -= 1
    
    async def _eval(self, node: ASTNode) -> Any:
        if isinstance(node, NumberNode):
            return node.value
        elif isinstance(node, StringNode):
            return node.value
        elif isinstance(node, VariableNode):
            return await self._eval_variable(node)
        elif isinstance(node, BinOpNode):
            return await self._eval_binop(node)
        elif isinstance(node, FunctionCallNode):
            return await self._eval_function(node)
        elif isinstance(node, DirectiveOverwriteNode):
            return await self._eval_directive_overwrite(node)
        elif isinstance(node, DirectiveMacroNode):
            return await self._eval_directive_macro(node)
        elif isinstance(node, DirectiveEffectNode):
            return await self._eval_directive_effect(node)
        else:
            logger.warning(f"Node type không được hỗ trợ: {type(node)}")
            return 0
    
    async def _eval_variable(self, node: VariableNode) -> Any:
        """Lấy giá trị biến từ context (self, target, local, hoặc runtime)"""
        var_name = node.name
        var_index = node.index
        
        if var_name.startswith("local."):
            local_name = var_name[6:]
            return self.local_vars.get(local_name, 0)
        
        if var_name.startswith("target."):
            if not self.target_list:
                if not self.in_combat:
                    full = f"{{{var_name}}}" + (f".{var_index}" if var_index else "")
                    return full
                return 0
            target = self.target_list[0]
            actual_var = var_name[7:]
            return target.get_var(actual_var, var_index)
        
        if var_name.startswith("self."):
            actual_var = var_name[5:]
            if not self.current_actor:
                return 0
            return self.current_actor.get_var(actual_var, var_index)
        
        if self.current_actor:
            return self.current_actor.get_var(var_name, var_index)
        
        return 0
    
    async def _eval_binop(self, node: BinOpNode) -> Any:
        left = await self.evaluate(node.left)
        right = await self.evaluate(node.right)
        
        if node.op == '&':
            return str(left) + str(right)
        
        try:
            lnum = float(left) if not isinstance(left, str) else float(left)
            rnum = float(right) if not isinstance(right, str) else float(right)
        except (ValueError, TypeError):
            logger.warning(f"Toán tử {node.op} yêu cầu số, nhận {left} và {right}")
            return 0
        
        if node.op == '+':
            return lnum + rnum
        elif node.op == '-':
            return lnum - rnum
        elif node.op == '*':
            return lnum * rnum
        elif node.op == '/':
            return safe_divide(lnum, rnum)
        return 0
    
    async def _eval_function(self, node: FunctionCallNode) -> Any:
        func_name = node.name.lower()
        func = self.function_registry.get(func_name)
        if not func:
            logger.warning(f"Hàm không tồn tại: {node.name}")
            return 0
        
        evaluated_args = []
        for arg_node in node.args:
            evaluated_args.append(await self.evaluate(arg_node))
        
        try:
            if asyncio.iscoroutinefunction(func):
                result = await func(self, node.args)
            else:
                result = func(self, node.args)
            return result
        except Exception as e:
            logger.error(f"Lỗi khi gọi hàm {func_name}: {e}")
            return 0
    
    async def _eval_directive_overwrite(self, node: DirectiveOverwriteNode) -> Any:
        """Xử lý chỉ thị {var}=expr: ghi đè runtime, trả về 0"""
        if not self.current_actor:
            return 0
        var_node = node.target_var
        value = await self.evaluate(node.expression)
        self.current_actor.set_var(var_node.name, value, var_node.index)
        return 0
    
    async def _eval_directive_macro(self, node: DirectiveMacroNode) -> Any:
        """Xử lý chỉ thị macro: đăng ký macro vào pool, trả về 0"""
        if not self.current_actor:
            return 0
        macro_content = await self._serialize_expression(node.expression)
        self.current_actor.add_macro(node.macro_name, macro_content, node.tag)
        return 0
    
    async def _eval_directive_effect(self, node: DirectiveEffectNode) -> Any:
        """Xử lý chỉ thị effect: lưu vào effect_pool toàn cục, trả về 0"""
        effect_content = await self._serialize_expression(node.expression)
        self.global_effect_pool[node.effect_name] = effect_content
        return 0
    
    async def _serialize_expression(self, node: ASTNode) -> str:
        """Tái tạo chuỗi từ AST (chỉ dùng cho macro/effect chưa parse)"""
        if isinstance(node, NumberNode):
            return str(node.value)
        if isinstance(node, StringNode):
            return f'"{node.value}"'
        if isinstance(node, VariableNode):
            if node.index:
                return f"{{{node.name}.{node.index}}}"
            return f"{{{node.name}}}"
        if isinstance(node, BinOpNode):
            left = await self._serialize_expression(node.left)
            right = await self._serialize_expression(node.right)
            return f"({left} {node.op} {right})"
        if isinstance(node, FunctionCallNode):
            args = []
            for a in node.args:
                args.append(await self._serialize_expression(a))
            return f"{node.name}({', '.join(args)})"
        return ""