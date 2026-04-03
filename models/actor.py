from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from utils.helpers import get_logger

logger = get_logger()

@dataclass
class ActorRuntime:
    """Actor gốc, lưu dữ liệu từ Google Sheets (persistent)"""
    actor_id: str                     # Tên sheet (hoặc ID)
    discord_user_id: int              # Chủ nhân
    name: str                         # Tên hiển thị
    runtime_vars: Dict[str, Any] = field(default_factory=dict)  # { "var": value, "var.index": value }
    macro_pool: Dict[str, str] = field(default_factory=dict)    # { macro_name: content }
    passive_pool: Dict[str, str] = field(default_factory=dict)  # macro tag P
    
    def get_var(self, var_name: str, index: Optional[str] = None) -> Any:
        """Lấy giá trị biến (có hoặc không index) từ runtime"""
        key = f"{var_name}.{index}" if index else var_name
        return self.runtime_vars.get(key, 0)
    
    def set_var(self, var_name: str, value: Any, index: Optional[str] = None) -> None:
        """Ghi đè giá trị biến trong runtime"""
        key = f"{var_name}.{index}" if index else var_name
        self.runtime_vars[key] = value
    
    def has_var(self, var_name: str, index: Optional[str] = None) -> bool:
        key = f"{var_name}.{index}" if index else var_name
        return key in self.runtime_vars
    
    def delete_var(self, var_name: str, index: Optional[str] = None) -> None:
        key = f"{var_name}.{index}" if index else var_name
        self.runtime_vars.pop(key, None)
    
    def get_macro(self, macro_name: str) -> Optional[str]:
        return self.macro_pool.get(macro_name)
    
    def add_macro(self, macro_name: str, content: str, tag: str) -> None:
        if tag == 'P':
            self.passive_pool[macro_name] = content
        else:
            self.macro_pool[macro_name] = content
    
    def to_dict(self) -> dict:
        return {
            "actor_id": self.actor_id,
            "discord_user_id": self.discord_user_id,
            "name": self.name,
            "runtime_vars": self.runtime_vars.copy(),
            "macro_pool": self.macro_pool.copy(),
            "passive_pool": self.passive_pool.copy(),
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "ActorRuntime":
        return cls(
            actor_id=data["actor_id"],
            discord_user_id=data["discord_user_id"],
            name=data["name"],
            runtime_vars=data.get("runtime_vars", {}).copy(),
            macro_pool=data.get("macro_pool", {}).copy(),
            passive_pool=data.get("passive_pool", {}).copy(),
        )