import random
import math
import asyncio
from typing import List, Any, Optional, Dict, Callable, Awaitable
from utils.helpers import roll_dice_sum, safe_divide, eval_condition, get_logger, current_timestamp
from models.ast_nodes import ASTNode, VariableNode
from models.combat_models import EffectMode, ShieldMode, EffectInstance, ShieldInstance, PlayerState, CombatActor

logger = get_logger()

AsyncFunction = Callable[..., Awaitable[Any]]

class FunctionRegistry:
    """Lưu trữ và gọi các hàm có sẵn"""
    
    def __init__(self):
        self._functions: Dict[str, AsyncFunction] = {}
        self._cooldown_tracker: Dict[str, Dict[str, float]] = {}  # actor_id -> {macro_name: next_available_timestamp}
        self._wait_counter: Dict[str, Dict[str, int]] = {}  # actor_id -> {macro_name: remaining_wait_count}
        self._register_builtins()
    
    def _register_builtins(self):
        """Đăng ký tất cả hàm"""
        self._functions["affect"] = self._affect
        self._functions["append"] = self._append
        self._functions["shield"] = self._shield
        self._functions["repeat"] = self._repeat
        self._functions["wait"] = self._wait
        self._functions["if"] = self._if
        self._functions["and"] = self._and
        self._functions["or"] = self._or
        self._functions["not"] = self._not
        self._functions["contain"] = self._contain
        self._functions["roll"] = self._roll
        self._functions["round"] = self._round
        self._functions["floor"] = self._floor
        self._functions["ceil"] = self._ceil
        self._functions["max"] = self._max
        self._functions["min"] = self._min
        self._functions["pick"] = self._pick
        self._functions["abs"] = self._abs
        self._functions["set"] = self._set
        self._functions["get"] = self._get
        self._functions["call"] = self._call
    
    def get(self, name: str) -> Optional[AsyncFunction]:
        return self._functions.get(name.lower())
    
    def reset_cooldown_for_actor(self, actor_id: str):
        """Xóa toàn bộ cooldown và wait counter của actor khi combat kết thúc"""
        self._cooldown_tracker.pop(actor_id, None)
        self._wait_counter.pop(actor_id, None)
    
    # -------------------- HÀM CỐT LÕI --------------------
    async def _affect(self, evaluator, args: List[ASTNode]) -> int:
        """
        affect({biến}, mode, giá_trị, điều_kiện, duration/persist)
        Trả về 0 (side-effect)
        """
        if len(args) < 4:
            logger.warning(f"affect cần ít nhất 4 tham số, nhận {len(args)}")
            return 0
        target_var_node = args[0]
        mode_node = args[1]
        value_node = args[2]
        condition_node = args[3]
        duration_persist_node = args[4] if len(args) > 4 else None
        
        if not isinstance(target_var_node, VariableNode):
            logger.warning("affect: tham số đầu phải là biến {var} hoặc {var.index}")
            return 0
        var_name = target_var_node.name
        var_index = target_var_node.index
        
        mode = await evaluator.evaluate(mode_node)
        value = await evaluator.evaluate(value_node)
        condition = await evaluator.evaluate(condition_node)
        
        duration = None
        persist = False
        if duration_persist_node:
            raw = await evaluator.evaluate(duration_persist_node)
            if isinstance(raw, str) and raw.lower() == "persist":
                persist = True
            else:
                try:
                    duration = int(raw)
                except:
                    duration = None
        
        if not eval_condition(condition, {}):
            return 0
        
        actor = evaluator.current_actor
        if var_name.startswith("target."):
            if evaluator.target_list:
                actor = evaluator.target_list[0]
                var_name = var_name[7:]
            else:
                logger.warning("affect có target nhưng không có target nào")
                return 0
        elif var_name.startswith("self."):
            var_name = var_name[5:]
        
        current_val = actor.get_var(var_name, var_index)
        original_val = current_val
        
        delta = None
        mode_str = str(mode).lower()
        if mode_str == "add":
            delta = value
            new_val = current_val + value
        elif mode_str == "mul":
            delta = value
            new_val = current_val * value
        elif mode_str == "o":
            delta = value
            new_val = value
        elif mode_str == "dot":
            new_val = current_val - value
            delta = None
        else:
            return 0
        
        actor.set_var(var_name, new_val, var_index)
        
        if duration is not None and duration > 0 and mode_str != "dot":
            effect = EffectInstance(
                name=f"{var_name}.{var_index}" if var_index else var_name,
                source_id=actor.actor_id,
                tag="",
                mode=EffectMode(mode_str),
                delta=delta,
                original_value=original_val,
                duration_remaining=duration,
                hooks=[],
                condition="true",
                persist=persist,
                target_var=f"{var_name}.{var_index}" if var_index else var_name
            )
            actor.add_effect(effect)
        
        if persist and hasattr(actor, 'persist_to_sheet'):
            await actor.persist_to_sheet(var_name, new_val, var_index)
        
        return 0
    
    async def _append(self, evaluator, args: List[ASTNode]) -> int:
        if len(args) < 3:
            return 0
        target_scope = await evaluator.evaluate(args[0])
        ratio = float(await evaluator.evaluate(args[1]) or 0)
        effect_name = await evaluator.evaluate(args[2])
        stack = int(await evaluator.evaluate(args[3])) if len(args) > 3 else 1
        max_stack = int(await evaluator.evaluate(args[4])) if len(args) > 4 else 999
        
        if random.randint(1, 100) > ratio:
            return 0
        
        effect_def = evaluator.global_effect_pool.get(effect_name)
        if not effect_def:
            logger.warning(f"Effect {effect_name} không tồn tại")
            return 0
        
        effect = EffectInstance(
            name=effect_name,
            source_id=evaluator.current_actor.actor_id,
            tag=effect_def.tag,
            mode=EffectMode.DOT,
            delta=0,
            original_value=0,
            duration_remaining=3,
            hooks=[],
            condition="true",
            persist=False
        )
        
        if target_scope == "self":
            evaluator.current_actor.add_effect(effect)
        elif target_scope == "target" and evaluator.target_list:
            for target in evaluator.target_list[:stack]:
                target.add_effect(effect)
        return 0
    
    async def _shield(self, evaluator, args: List[ASTNode]) -> int:
        if len(args) < 3:
            return 0
        target = await evaluator.evaluate(args[0])
        name = await evaluator.evaluate(args[1])
        value = int(await evaluator.evaluate(args[2]) or 0)
        mode = await evaluator.evaluate(args[3]) if len(args) > 3 else "fixed"
        duration = int(await evaluator.evaluate(args[4])) if len(args) > 4 else -1
        stack = int(await evaluator.evaluate(args[5])) if len(args) > 5 else 1
        max_stack = int(await evaluator.evaluate(args[6])) if len(args) > 6 else 999
        
        shield = ShieldInstance(
            name=name,
            source_id=evaluator.current_actor.actor_id,
            value=value,
            max_value=value,
            mode=ShieldMode(mode),
            duration_remaining=duration,
            stack=stack
        )
        
        actor = evaluator.current_actor if target == "self" else (evaluator.target_list[0] if evaluator.target_list else None)
        if actor:
            actor.add_shield(shield)
        return 0
    
    async def _repeat(self, evaluator, args: List[ASTNode]) -> int:
        if len(args) < 2:
            return 0
        count = int(await evaluator.evaluate(args[0]) or 0)
        func_node = args[1]
        target_limit = int(await evaluator.evaluate(args[2])) if len(args) > 2 else 999
        mode = await evaluator.evaluate(args[3]) if len(args) > 3 else "all"
        
        original_targets = evaluator.target_list[:]
        for i in range(count):
            if mode == "rotate":
                evaluator.target_list = original_targets[i % len(original_targets):] + original_targets[:i % len(original_targets)]
                evaluator.target_list = evaluator.target_list[:target_limit]
            elif mode == "random":
                shuffled = original_targets[:]
                random.shuffle(shuffled)
                evaluator.target_list = shuffled[:target_limit]
            else:
                evaluator.target_list = original_targets[:target_limit]
            await evaluator.evaluate(func_node)
        evaluator.target_list = original_targets
        return 0
    
    async def _wait(self, evaluator, args: List[ASTNode]) -> int:
        """
        wait(giá_trị, mode)
        mode: 'chanel' (đếm số lần gọi macro) hoặc 'cd' (cooldown dựa trên turn_count)
        Trả về 1 nếu wait đã thỏa mãn, 0 nếu chưa (macro sẽ bị dừng)
        """
        if len(args) < 2:
            return 1
        value = int(await evaluator.evaluate(args[0]) or 1)
        mode = str(await evaluator.evaluate(args[1]) or "chanel").lower()
        
        actor_id = evaluator.current_actor.actor_id
        macro_name = evaluator.current_macro_name if hasattr(evaluator, 'current_macro_name') else "default"
        
        if mode == "cd":
            if actor_id not in self._cooldown_tracker:
                self._cooldown_tracker[actor_id] = {}
            key = f"{macro_name}_cd"
            next_available = self._cooldown_tracker[actor_id].get(key, 0)
            current_turn = getattr(evaluator, 'turn_count', 0)
            if current_turn < next_available:
                logger.debug(f"Cooldown: {macro_name} chưa sẵn sàng (cần {next_available}, hiện {current_turn})")
                return 0
            self._cooldown_tracker[actor_id][key] = current_turn + value
            return 1
        
        else:
            if actor_id not in self._wait_counter:
                self._wait_counter[actor_id] = {}
            key = f"{macro_name}_chanel"
            remaining = self._wait_counter[actor_id].get(key, 0)
            if remaining > 0:
                self._wait_counter[actor_id][key] = remaining - 1
                logger.debug(f"Wait chanel: {macro_name} còn {remaining-1} lần")
                return 0
            self._wait_counter[actor_id][key] = value
            return 1
    
    async def _if(self, evaluator, args: List[ASTNode]) -> Any:
        if len(args) < 2:
            return 0
        cond = await evaluator.evaluate(args[0])
        if eval_condition(cond, {}):
            return await evaluator.evaluate(args[1])
        elif len(args) > 2:
            return await evaluator.evaluate(args[2])
        return 0
    
    async def _and(self, evaluator, args: List[ASTNode]) -> bool:
        for arg in args:
            val = await evaluator.evaluate(arg)
            if not eval_condition(val, {}):
                return False
        return True
    
    async def _or(self, evaluator, args: List[ASTNode]) -> bool:
        for arg in args:
            val = await evaluator.evaluate(arg)
            if eval_condition(val, {}):
                return True
        return False
    
    async def _not(self, evaluator, args: List[ASTNode]) -> bool:
        if not args:
            return True
        val = await evaluator.evaluate(args[0])
        return not eval_condition(val, {})
    
    async def _contain(self, evaluator, args: List[ASTNode]) -> bool:
        if len(args) < 2:
            return False
        scope = await evaluator.evaluate(args[0])
        text = await evaluator.evaluate(args[1])
        actor = evaluator.current_actor
        for effect in actor.effect_state:
            if text in effect.name:
                return True
        return False
    
    async def _roll(self, evaluator, args: List[ASTNode]) -> int:
        if not args:
            return 0
        expr = await evaluator.evaluate(args[0])
        try:
            return roll_dice_sum(str(expr))
        except:
            return 0
    
    async def _round(self, evaluator, args: List[ASTNode]) -> float:
        val = float(await evaluator.evaluate(args[0]) or 0)
        ndigits = int(await evaluator.evaluate(args[1])) if len(args) > 1 else 0
        return round(val, ndigits)
    
    async def _floor(self, evaluator, args: List[ASTNode]) -> int:
        val = float(await evaluator.evaluate(args[0]) or 0)
        return math.floor(val)
    
    async def _ceil(self, evaluator, args: List[ASTNode]) -> int:
        val = float(await evaluator.evaluate(args[0]) or 0)
        return math.ceil(val)
    
    async def _max(self, evaluator, args: List[ASTNode]) -> float:
        values = [float(await evaluator.evaluate(arg) or 0) for arg in args]
        return max(values) if values else 0
    
    async def _min(self, evaluator, args: List[ASTNode]) -> float:
        values = [float(await evaluator.evaluate(arg) or 0) for arg in args]
        return min(values) if values else 0
    
    async def _pick(self, evaluator, args: List[ASTNode]) -> Any:
        if not args:
            return None
        items = [await evaluator.evaluate(arg) for arg in args]
        return random.choice(items) if items else None
    
    async def _abs(self, evaluator, args: List[ASTNode]) -> float:
        if not args:
            return 0
        val = float(await evaluator.evaluate(args[0]) or 0)
        return abs(val)
    
    async def _set(self, evaluator, args: List[ASTNode]) -> None:
        if len(args) < 2:
            return
        value = await evaluator.evaluate(args[0])
        name = await evaluator.evaluate(args[1])
        evaluator.local_vars[name] = value
    
    async def _get(self, evaluator, args: List[ASTNode]) -> Any:
        if not args:
            return None
        name = await evaluator.evaluate(args[0])
        return evaluator.local_vars.get(name, 0)
    
    async def _call(self, evaluator, args: List[ASTNode]) -> int:
        """
        call(source, số_lượng, "tên", duration)
        Triệu hồi actor mới từ sheet source (có thể là sheet_id hoặc 'self' hoặc 'target')
        Trả về số lượng đã triệu hồi thành công (0 nếu thất bại)
        """
        if len(args) < 1:
            logger.warning("call cần ít nhất source")
            return 0
        
        source = await evaluator.evaluate(args[0])
        quantity = int(await evaluator.evaluate(args[1])) if len(args) > 1 else 1
        rename = await evaluator.evaluate(args[2]) if len(args) > 2 else None
        duration = int(await evaluator.evaluate(args[3])) if len(args) > 3 else -1
        
        sheet_id = None
        if source == "self":
            sheet_id = evaluator.current_actor.actor_id
        elif source == "target" and evaluator.target_list:
            sheet_id = evaluator.target_list[0].actor_id
        elif isinstance(source, str):
            sheet_id = source
        else:
            logger.warning(f"call: source không hợp lệ {source}")
            return 0
        
        gsheet = evaluator.gsheet_client
        if not gsheet:
            logger.warning("call: không có gsheet_client")
            return 0
        
        summoned_count = 0
        for i in range(quantity):
            actor_name = rename if rename else sheet_id
            if quantity > 1:
                actor_name = f"{actor_name}_{i+1}"
            new_actor = CombatActor(
                actor_id=f"summoned_{sheet_id}_{i}_{current_timestamp()}",
                name=actor_name,
                discord_user_id=0,
                base_vars={},
                hp=10,
                max_hp=10,
                action_limit_remaining=1
            )

            try:
                data = await gsheet.batch_load_by_load_column(sheet_id)
                for var, col_map in data.items():
                    for idx, val in col_map.items():
                        new_actor.base_vars[f"{var}.{idx}"] = val
                new_actor.hp = int(new_actor.base_vars.get("hp.a", 10))
                new_actor.max_hp = int(new_actor.base_vars.get("max_hp.a", 10))
                new_actor.action_limit_remaining = int(new_actor.base_vars.get("action_limit.a", 1))
            except Exception as e:
                logger.error(f"Lỗi load sheet {sheet_id} cho call: {e}")
                continue
            
            if evaluator.combat:
                evaluator.combat.actors.append(new_actor)
                evaluator.combat.add_actor_to_team(new_actor, "")
            summoned_count += 1
        
        return summoned_count