from typing import Dict, List, Optional, Any, Union, Set
from dataclasses import dataclass, field
import asyncio
from enum import Enum

# -------------------- HIỆU ỨNG (EFFECT) --------------------
class EffectMode(Enum):
    ADD = "add"
    MUL = "mul"
    OVERWRITE = "o"
    DOT = "dot"

@dataclass
class EffectDefinition:
    name: str
    tag: str
    raw_content: str

@dataclass
class EffectInstance:
    name: str
    source_id: str
    tag: str
    mode: EffectMode
    delta: Union[int, float, str]
    original_value: Any
    duration_remaining: int
    hooks: List[str]
    condition: str
    persist: bool = False
    target_var: str = ""

# -------------------- KHIÊN (SHIELD) --------------------
class ShieldMode(Enum):
    FIXED = "fixed"
    REFILL = "refill"

@dataclass
class ShieldInstance:
    name: str
    source_id: str
    value: int
    max_value: int
    mode: ShieldMode
    duration_remaining: int
    stack: int = 1

# -------------------- OUTPUT (ĐÒN TẤN CÔNG) --------------------
@dataclass
class Output:
    source: "CombatActor"
    target: "CombatActor"
    value: int
    hit: int
    priority: int
    effect_output: List[EffectInstance] = field(default_factory=list)
    shield_output: List[ShieldInstance] = field(default_factory=list)

# -------------------- ACTOR TRONG COMBAT --------------------
class PlayerState(Enum):
    PENDING = "pending"
    ACTION = "action"
    BE_TARGETED = "be_targeted"
    KO = "ko"

@dataclass
class CombatActor:
    actor_id: str
    name: str
    discord_user_id: int
    base_vars: Dict[str, Any] = field(default_factory=dict)
    
    # Combat stats
    hp: int = 0
    max_hp: int = 0
    effect_state: List[EffectInstance] = field(default_factory=list)
    shield_state: List[ShieldInstance] = field(default_factory=list)
    player_state: PlayerState = PlayerState.PENDING
    action_limit_remaining: int = 1
    initiative: int = 0
    hit: int = 0
    miss: int = 0
    priority: int = 0
    
    # Hàng đợi combat
    output: List[Output] = field(default_factory=list)
    receive_input: List[Output] = field(default_factory=list)
    input_queue: List[Output] = field(default_factory=list)
    reduce_input: Any = 0  # công thức giảm sát thương
    
    macro_pool: Dict[str, str] = field(default_factory=dict)
    passive_pool: Dict[str, str] = field(default_factory=dict)
    
    def get_var(self, var_name: str, index: Optional[str] = None) -> Any:
        key = f"{var_name}.{index}" if index else var_name
        if key == "hp":
            return self.hp
        if key == "max_hp":
            return self.max_hp
        if key == "action_limit":
            return self.action_limit_remaining
        if key == "player_state":
            return self.player_state.value
        if key == "initiative":
            return self.initiative
        return self.base_vars.get(key, 0)
    
    def set_var(self, var_name: str, value: Any, index: Optional[str] = None) -> None:
        key = f"{var_name}.{index}" if index else var_name
        if key == "hp":
            self.hp = max(0, min(value, self.max_hp))
        elif key == "max_hp":
            self.max_hp = value
            self.hp = min(self.hp, self.max_hp)
        elif key == "action_limit":
            self.action_limit_remaining = value
        elif key == "initiative":
            self.initiative = value
        else:
            self.base_vars[key] = value
    
    def add_effect(self, effect: EffectInstance) -> None:
        self.effect_state.append(effect)
    
    def add_shield(self, shield: ShieldInstance) -> None:
        self.shield_state.append(shield)
    
    def reset_for_new_turn(self) -> None:
        self.action_limit_remaining = self.base_vars.get("action_limit", 1)
        self.hit = 0
        self.miss = 0
        self.priority = 0
        self.output.clear()
        self.receive_input.clear()
        self.input_queue.clear()
        self.reduce_input = 0
    
    def is_ko(self) -> bool:
        return self.player_state == PlayerState.KO

# -------------------- COMBAT STATE --------------------
class CombatPhase(Enum):
    TURN_READY = "TURN READY"
    TURN_START = "TURN START"
    ACTION = "ACTION"
    REACTION = "REACTION"
    END_PHASE = "END PHASE"
    TURN_END = "TURN END"
    COMBAT_END = "COMBAT END"

@dataclass
class Combat:
    channel_id: int
    actors: List[CombatActor] = field(default_factory=list)
    teams: Dict[str, List[CombatActor]] = field(default_factory=dict)
    phase: CombatPhase = CombatPhase.TURN_READY
    turn_count: int = 0
    initiative_queue: List[CombatActor] = field(default_factory=list)
    sticky_message_id: Optional[int] = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    acted_in_turn: Set[CombatActor] = field(default_factory=set)
    targeted_this_action: Set[CombatActor] = field(default_factory=set)
    action_index: int = 0  # chỉ số actor đã action gần nhất trong queue
    
    def get_actor_by_user_id(self, user_id: int) -> Optional[CombatActor]:
        for a in self.actors:
            if a.discord_user_id == user_id:
                return a
        return None
    
    def get_team_of(self, actor: CombatActor) -> Optional[str]:
        for team_name, members in self.teams.items():
            if actor in members:
                return team_name
        return None
    
    def add_actor_to_team(self, actor: CombatActor, team_name: str) -> None:
        old_team = self.get_team_of(actor)
        if old_team:
            self.teams[old_team].remove(actor)
        if team_name not in self.teams:
            self.teams[team_name] = []
        self.teams[team_name].append(actor)
    
    def remove_actor(self, actor: CombatActor) -> None:
        if actor in self.actors:
            self.actors.remove(actor)
        for team in self.teams.values():
            if actor in team:
                team.remove(actor)
        if actor in self.initiative_queue:
            self.initiative_queue.remove(actor)
        if actor in self.acted_in_turn:
            self.acted_in_turn.remove(actor)
        if actor in self.targeted_this_action:
            self.targeted_this_action.remove(actor)
    
    def get_remaining_teams(self) -> List[Optional[str]]:
        alive_teams = set()
        for actor in self.actors:
            if not actor.is_ko():
                team = self.get_team_of(actor)
                alive_teams.add(team)
        return list(alive_teams)