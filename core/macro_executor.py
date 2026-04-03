# core/macro_executor.py
# Thực thi macro: parse nội dung, gọi evaluator, xử lý kết quả

from typing import List, Optional, Tuple, Union, Any
from core.parser import parse
from core.evaluator import Evaluator
from models.actor import ActorRuntime
from models.combat_models import CombatActor
from utils.helpers import get_logger, format_macro_result

logger = get_logger()

class MacroExecutor:
    """Điều phối việc thực thi macro trong và ngoài combat"""
    
    def __init__(self, evaluator: Evaluator):
        self.evaluator = evaluator
    
    async def execute(
        self,
        actor: Union[ActorRuntime, CombatActor],
        macro_name: str,
        target_actors: List[Union[ActorRuntime, CombatActor]],
        in_combat: bool = False,
        global_config: Optional[dict] = None,
        global_effect_pool: Optional[dict] = None
    ) -> Tuple[str, bool]:
        """
        Thực thi macro.
        Trả về (message, success)
        success = True nếu macro được thực thi thành công (có side-effect hoặc tính toán được)
        """
        # Lấy nội dung macro
        macro_content = None
        if hasattr(actor, 'macro_pool'):
            macro_content = actor.macro_pool.get(macro_name)
        if not macro_content and hasattr(actor, 'passive_pool'):
            macro_content = actor.passive_pool.get(macro_name)
        
        if not macro_content:
            return format_macro_result(actor.name, macro_name, f"Không tìm thấy macro '{macro_name}'", error=True), False
        
        # Cấu hình evaluator
        self.evaluator.reset_context()
        self.evaluator.current_actor = actor
        self.evaluator.target_list = target_actors
        self.evaluator.in_combat = in_combat
        if global_config:
            self.evaluator.global_config = global_config
        if global_effect_pool:
            self.evaluator.global_effect_pool = global_effect_pool
        
        # Parse nội dung macro
        try:
            ast = parse(macro_content)
        except Exception as e:
            logger.error(f"Parse macro '{macro_name}' thất bại: {e}\nNội dung: {macro_content}")
            return format_macro_result(actor.name, macro_name, f"Lỗi cú pháp: {str(e)}", error=True), False
        
        # Thực thi
        try:
            result = await self.evaluator.evaluate(ast)
            # Tạo message kết quả
            if not in_combat:
                # Ngoài combat: chỉ hiển thị kết quả tính toán
                if isinstance(result, (int, float, str)):
                    msg = str(result)
                else:
                    msg = "Macro đã được xử lý (không có side-effect)"
                return format_macro_result(actor.name, macro_name, msg, error=False), True
            else:
                # Trong combat: có thể có side-effect, nhưng ta chỉ hiển thị thông báo ngắn
                if result == 0:
                    msg = "Thực thi thành công."
                else:
                    msg = f"Kết quả: {result}"
                return format_macro_result(actor.name, macro_name, msg, error=False), True
        except Exception as e:
            logger.error(f"Lỗi thực thi macro '{macro_name}': {e}")
            return format_macro_result(actor.name, macro_name, f"Lỗi thực thi: {str(e)}", error=True), False
    
    async def execute_initiative(self, actor: Union[ActorRuntime, CombatActor]) -> int:
        """
        Macro initiative mặc định.
        Trả về giá trị initiative đã tính.
        """
        formula = self.evaluator.global_config.get("initiative_formula", "1d20")
        # Tạo evaluator tạm
        self.evaluator.reset_context()
        self.evaluator.current_actor = actor
        self.evaluator.in_combat = True
        try:
            ast = parse(formula)
            result = await self.evaluator.evaluate(ast)
            return int(result) if isinstance(result, (int, float)) else 1
        except:
            return 1
    
    async def execute_skip(self, actor: Union[ActorRuntime, CombatActor]) -> bool:
        """
        Macro skip mặc định: đặt action_limit về 0
        """
        if hasattr(actor, 'set_var'):
            actor.set_var("action_limit", 0)
            return True
        return False