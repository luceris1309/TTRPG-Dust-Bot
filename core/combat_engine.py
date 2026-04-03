# core/combat_engine.py
# Quản lý combat: phase, resolve effect, damage, shield, output, turn flow
# Tích hợp đầy đủ cơ chế action_limit, không còn placeholder

import asyncio
from typing import List, Dict, Optional, Tuple, Any, Set
from models.combat_models import (
    Combat, CombatActor, CombatPhase, PlayerState,
    EffectInstance, EffectMode, ShieldInstance, ShieldMode, Output
)
from core.macro_executor import MacroExecutor
from core.parser import parse
from core.evaluator import Evaluator
from utils.helpers import get_logger, eval_condition

logger = get_logger()

class CombatEngine:
    def __init__(self, macro_executor: MacroExecutor, evaluator: Evaluator):
        self.macro_executor = macro_executor
        self.evaluator = evaluator
        self.active_combats: Dict[int, Combat] = {}
    
    def get_combat(self, channel_id: int) -> Optional[Combat]:
        return self.active_combats.get(channel_id)
    
    async def create_combat(self, channel_id: int) -> Combat:
        if channel_id in self.active_combats:
            raise ValueError("Đã có combat trong kênh này")
        combat = Combat(channel_id=channel_id)
        self.active_combats[channel_id] = combat
        return combat
    
    async def remove_combat(self, channel_id: int):
        combat = self.active_combats.pop(channel_id, None)
        if combat:
            async with combat.lock:
                combat.phase = CombatPhase.COMBAT_END
                combat.actors.clear()
                combat.teams.clear()
                combat.initiative_queue.clear()
                combat.acted_in_turn.clear()
                combat.targeted_this_action.clear()
    
    async def add_actor(self, combat: Combat, actor: CombatActor, team: Optional[str] = None):
        async with combat.lock:
            if actor in combat.actors:
                return
            combat.actors.append(actor)
            if team:
                combat.add_actor_to_team(actor, team)
    
    async def remove_actor(self, combat: Combat, actor: CombatActor, is_flee: bool = False):
        async with combat.lock:
            combat.remove_actor(actor)
            if is_flee:
                actor.effect_state.clear()
                actor.shield_state.clear()
                actor.player_state = PlayerState.PENDING
            if len(combat.get_remaining_teams()) <= 1:
                await self.end_combat(combat)
    
    async def start_combat(self, combat: Combat):
        async with combat.lock:
            combat.phase = CombatPhase.TURN_READY
            combat.turn_count = 0
            combat.action_index = 0
            combat.acted_in_turn.clear()
            combat.targeted_this_action.clear()
            for actor in combat.actors:
                actor.player_state = PlayerState.PENDING
                base_limit = actor.base_vars.get("action_limit", 1)
                actor.action_limit_remaining = base_limit
                actor.initiative = 0
                actor.hp = actor.get_var("hp", None)
                actor.max_hp = actor.get_var("max_hp", None) or actor.hp
                actor.effect_state.clear()
                actor.shield_state.clear()
                actor.output.clear()
                actor.receive_input.clear()
                actor.input_queue.clear()
                # Lưu reduce_input dưới dạng công thức (string)
                actor.reduce_input = actor.base_vars.get("reduce_input", "0")
            logger.info(f"Combat bắt đầu tại channel {combat.channel_id}")
    
    async def next_phase(self, combat: Combat):
        async with combat.lock:
            if combat.phase == CombatPhase.TURN_READY:
                await self._phase_turn_ready(combat)
            elif combat.phase == CombatPhase.TURN_START:
                await self._phase_turn_start(combat)
            elif combat.phase == CombatPhase.ACTION:
                await self._phase_action(combat)
            elif combat.phase == CombatPhase.REACTION:
                await self._phase_reaction(combat)
            elif combat.phase == CombatPhase.END_PHASE:
                await self._phase_end_phase(combat)
            elif combat.phase == CombatPhase.TURN_END:
                await self._phase_turn_end(combat)
            elif combat.phase == CombatPhase.COMBAT_END:
                await self.end_combat(combat)
    
    # ========== PHASE TURN_READY ==========
    async def _phase_turn_ready(self, combat: Combat):
        ready = all(a.initiative > 0 for a in combat.actors if not a.is_ko())
        if not ready:
            return
        combat.initiative_queue = sorted(
            [a for a in combat.actors if not a.is_ko()],
            key=lambda a: (-a.initiative, a.name)
        )
        combat.action_index = 0
        combat.acted_in_turn.clear()
        combat.phase = CombatPhase.TURN_START
        await self.next_phase(combat)
    
    # ========== PHASE TURN_START ==========
    async def _phase_turn_start(self, combat: Combat):
        combat.turn_count += 1
        for actor in combat.actors:
            if not actor.is_ko():
                base_limit = actor.base_vars.get("action_limit", 1)
                actor.action_limit_remaining = base_limit
                actor.player_state = PlayerState.PENDING
                actor.hit = 0
                actor.miss = 0
                actor.priority = 0
                actor.output.clear()
                actor.receive_input.clear()
                actor.input_queue.clear()
        await self._resolve_effects(combat, "on_start", None)
        for actor in combat.actors:
            for shield in actor.shield_state:
                if shield.mode == ShieldMode.REFILL:
                    shield.value = shield.max_value
        combat.phase = CombatPhase.ACTION
        await self.next_phase(combat)
    
    # ========== PHASE ACTION ==========
    async def _phase_action(self, combat: Combat):
        await self._assign_next_action_group(combat)
        await self._resolve_effects(combat, "on_phase_start", PlayerState.ACTION)
        # Ở phase này, bot sẽ chờ lệnh macro từ các actor ACTION
        # Việc chuyển phase do execute_macro_in_combat hoặc skip_phase đảm nhiệm
    
    async def _assign_next_action_group(self, combat: Combat):
        next_actor = None
        for actor in combat.initiative_queue:
            if actor not in combat.acted_in_turn and not actor.is_ko():
                next_actor = actor
                break
        if not next_actor:
            return
        next_actor.player_state = PlayerState.ACTION
        combat.acted_in_turn.add(next_actor)
        team = combat.get_team_of(next_actor)
        if team:
            for actor in combat.actors:
                if actor != next_actor and combat.get_team_of(actor) == team and actor not in combat.acted_in_turn and not actor.is_ko():
                    actor.player_state = PlayerState.ACTION
                    combat.acted_in_turn.add(actor)
    
    # ========== PHASE REACTION ==========
    async def _phase_reaction(self, combat: Combat):
        for actor in combat.actors:
            if actor in combat.targeted_this_action:
                actor.player_state = PlayerState.BE_TARGETED
        combat.targeted_this_action.clear()
        # Không resolve on_phase_start ở đây
    
    # ========== PHASE END_PHASE ==========
    async def _phase_end_phase(self, combat: Combat):
        await self._resolve_effects(combat, "on_phase_end", PlayerState.ACTION)
        await self._resolve_output_intercept(combat)
        await self._resolve_hit_miss(combat)
        await self._resolve_damage(combat)
        await self._resolve_effects(combat, "on_hit", None)
        for actor in combat.actors:
            if actor.player_state in (PlayerState.ACTION, PlayerState.BE_TARGETED):
                actor.player_state = PlayerState.PENDING
        combat.phase = CombatPhase.TURN_END
        await self.next_phase(combat)
    
    # ========== PHASE TURN_END ==========
    async def _phase_turn_end(self, combat: Combat):
        await self._resolve_effects(combat, "on_ko", None)
        for actor in combat.actors:
            actor.effect_state = [e for e in actor.effect_state if e.duration_remaining != 0]
            actor.shield_state = [s for s in actor.shield_state if s.duration_remaining != 0]
        
        remaining = [a for a in combat.initiative_queue if a not in combat.acted_in_turn and not a.is_ko()]
        if remaining:
            combat.phase = CombatPhase.ACTION
            await self.next_phase(combat)
        else:
            combat.acted_in_turn.clear()
            combat.phase = CombatPhase.TURN_READY
            await self.next_phase(combat)
    
    # ========== CÁC HÀM RESOLVE ==========
    async def _resolve_effects(self, combat: Combat, hook: str, target_state: Optional[PlayerState]):
        for actor in combat.actors:
            if target_state is not None and actor.player_state != target_state:
                continue
            for effect in actor.effect_state[:]:
                if hook in effect.hooks and eval_condition(effect.condition, {}):
                    if effect.duration_remaining > 0:
                        effect.duration_remaining -= 1
                    if effect.duration_remaining == 0:
                        target_var = effect.target_var
                        parts = target_var.split('.')
                        var_name = parts[0]
                        var_index = parts[1] if len(parts) > 1 else None
                        current = actor.get_var(var_name, var_index)
                        if effect.mode == EffectMode.ADD:
                            actor.set_var(var_name, current - effect.delta, var_index)
                        elif effect.mode == EffectMode.MUL:
                            if effect.delta != 0:
                                actor.set_var(var_name, current / effect.delta, var_index)
                        elif effect.mode == EffectMode.OVERWRITE:
                            actor.set_var(var_name, effect.original_value, var_index)
                        actor.effect_state.remove(effect)
    
    async def _resolve_output_intercept(self, combat: Combat):
        all_outputs = []
        for actor in combat.actors:
            all_outputs.extend(actor.output)
            actor.output.clear()
        pair_map = {}
        for out in all_outputs:
            key = (out.source, out.target)
            if key not in pair_map:
                pair_map[key] = out
            else:
                pair_map[key].value += out.value
        processed = set()
        final_outputs = []
        for (src, tgt), out1 in pair_map.items():
            if (src, tgt) in processed:
                continue
            reverse_key = (tgt, src)
            if reverse_key in pair_map:
                out2 = pair_map[reverse_key]
                processed.add(reverse_key)
                if out1.value > out2.value:
                    out1.value -= out2.value
                    final_outputs.append(out1)
                elif out2.value > out1.value:
                    out2.value -= out1.value
                    final_outputs.append(out2)
            else:
                final_outputs.append(out1)
            processed.add((src, tgt))
        for actor in combat.actors:
            actor.receive_input = [out for out in final_outputs if out.target == actor]
    
    async def _resolve_hit_miss(self, combat: Combat):
        for actor in combat.actors:
            for inp in actor.receive_input[:]:
                target = inp.target
                if inp.hit > target.miss:
                    actor.input_queue.append(inp)
                    if inp.effect_output:
                        target.effect_state.extend(inp.effect_output)
                    if inp.shield_output:
                        target.shield_state.extend(inp.shield_output)
                elif inp.hit == target.miss:
                    if inp.priority > target.priority:
                        actor.input_queue.append(inp)
                        if inp.effect_output:
                            target.effect_state.extend(inp.effect_output)
                        if inp.shield_output:
                            target.shield_state.extend(inp.shield_output)
            actor.receive_input.clear()
    
    async def _resolve_damage(self, combat: Combat):
        for actor in combat.actors:
            total_damage = 0
            for inp in actor.input_queue:
                dmg = inp.value
                if actor.reduce_input and actor.reduce_input != "0":
                    try:
                        # Dùng evaluator để tính reduce_input trong context của target
                        ast = parse(str(actor.reduce_input))
                        # Tạo evaluator context tạm thời với actor là target
                        old_actor = self.evaluator.current_actor
                        self.evaluator.current_actor = actor
                        reduction = await self.evaluator.evaluate(ast)
                        self.evaluator.current_actor = old_actor
                        if isinstance(reduction, (int, float)):
                            dmg = max(0, dmg - reduction)
                    except Exception as e:
                        logger.warning(f"Lỗi tính reduce_input cho {actor.name}: {e}")
                total_damage += dmg
            actor.input_queue.clear()
            if total_damage <= 0:
                continue
            for shield in reversed(actor.shield_state):
                if total_damage <= 0:
                    break
                if shield.value >= total_damage:
                    shield.value -= total_damage
                    total_damage = 0
                else:
                    total_damage -= shield.value
                    shield.value = 0
                if shield.value <= 0 and shield.mode == ShieldMode.FIXED:
                    actor.shield_state.remove(shield)
            if total_damage > 0:
                actor.hp = max(0, actor.hp - total_damage)
                if actor.hp == 0:
                    actor.player_state = PlayerState.KO
                    actor.effect_state.clear()
                    actor.shield_state.clear()
    
    async def end_combat(self, combat: Combat):
        combat.phase = CombatPhase.COMBAT_END
        await self.remove_combat(combat.channel_id)
        logger.info(f"Kết thúc combat tại channel {combat.channel_id}")
    
    # ========== TƯƠNG TÁC VỚI MACRO ==========
    async def execute_macro_in_combat(
        self, combat: Combat, actor: CombatActor, macro_name: str, targets: List[CombatActor]
    ) -> Tuple[str, bool]:
        async with combat.lock:
            if combat.phase == CombatPhase.ACTION:
                if actor.player_state != PlayerState.ACTION:
                    return "Bạn không phải actor ACTION trong phase này", False
            elif combat.phase == CombatPhase.REACTION:
                if actor.player_state != PlayerState.BE_TARGETED:
                    return "Bạn không phải actor BE_TARGETED trong phase này", False
            else:
                return "Không thể dùng macro ở phase hiện tại", False
            
            if actor.action_limit_remaining <= 0:
                return f"{actor.name} đã hết action limit", False
            
            for t in targets:
                combat.targeted_this_action.add(t)
            
            msg, success = await self.macro_executor.execute(
                actor, macro_name, targets, in_combat=True
            )
            if success:
                actor.action_limit_remaining -= 1
                if combat.phase == CombatPhase.ACTION:
                    actors_in_phase = [a for a in combat.actors if a.player_state == PlayerState.ACTION]
                else:
                    actors_in_phase = [a for a in combat.actors if a.player_state == PlayerState.BE_TARGETED]
                all_done = all(a.action_limit_remaining <= 0 for a in actors_in_phase)
                if all_done:
                    if combat.phase == CombatPhase.ACTION:
                        combat.phase = CombatPhase.REACTION
                    elif combat.phase == CombatPhase.REACTION:
                        combat.phase = CombatPhase.END_PHASE
                    await self.next_phase(combat)
            return msg, success
    
    async def skip_phase(self, combat: Combat):
        async with combat.lock:
            if combat.phase == CombatPhase.TURN_READY:
                for actor in combat.actors:
                    if actor.initiative == 0:
                        actor.initiative = 1
            elif combat.phase == CombatPhase.ACTION:
                for actor in combat.actors:
                    if actor.player_state == PlayerState.ACTION:
                        actor.action_limit_remaining = 0
            elif combat.phase == CombatPhase.REACTION:
                for actor in combat.actors:
                    if actor.player_state == PlayerState.BE_TARGETED:
                        actor.action_limit_remaining = 0
            if combat.phase == CombatPhase.TURN_READY:
                combat.phase = CombatPhase.TURN_START
            elif combat.phase == CombatPhase.ACTION:
                combat.phase = CombatPhase.REACTION
            elif combat.phase == CombatPhase.REACTION:
                combat.phase = CombatPhase.END_PHASE
            elif combat.phase == CombatPhase.END_PHASE:
                combat.phase = CombatPhase.TURN_END
            elif combat.phase == CombatPhase.TURN_END:
                combat.phase = CombatPhase.TURN_READY
            await self.next_phase(combat)