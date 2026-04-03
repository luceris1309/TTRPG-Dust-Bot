import os
from typing import Dict, Any

# -------------------- GOOGLE SHEETS --------------------
# Tên các sheet bắt buộc
SHEET_INDEX = "INDEX"
SHEET_CONFIG = "CONFIG"
SHEET_EFFECT = "Effect"
SHEET_TEMPLATE = "Template"

# ID của spreadsheet chính (lấy từ biến môi trường hoặc hardcode)
SPREADSHEET_ID = os.getenv("TTRPG_SPREADSHEET_ID", "")

# -------------------- CACHE & TTL --------------------
TTL_SECONDS = 900  # giây
MAX_RECURSION_DEPTH = 10

# -------------------- RATE LIMIT --------------------
MAX_CONCURRENT_GAPI_REQUESTS = 5

# -------------------- DISCORD --------------------
# Màu sắc cho embed (HEX)
COLOR_INFO = 0x3498db
COLOR_SUCCESS = 0x2ecc71
COLOR_WARNING = 0xe67e22
COLOR_ERROR = 0xe74c3c

# Ký tự marker mặc định cho combat UI
COMBAT_ACTION_MARKER = "⚔️"
COMBAT_TARGETED_MARKER = "🎯"
COMBAT_KO_MARKER = "💀"

# -------------------- CÁC UI MẶC ĐỊNH CHO CONFIG --------------------
DEFAULT_CONFIG_ROWS: Dict[str, str] = {
    "profile_ui": '"**Tên:** " & {name} & "\\n**HP:** " & {hp} & "/" & {max_hp} & "\\n**MP:** " & {mp}',
    "stat_ui": '""',
    "inventory_ui": '"Chưa có vật phẩm."',
    "equipment_ui": '"Chưa có trang bị."',
    "skill_ui": '"")',
    "initiative_formula": "1d20",
    "default_hitpoint": "0",
}

# -------------------- HÀM TIỆN ÍCH --------------------
def get_required_env(var_name: str) -> str:
    """Lấy biến môi trường, ném lỗi nếu thiếu"""
    value = os.getenv(var_name)
    if not value:
        raise RuntimeError(f"Thiếu biến môi trường: {var_name}")
    return value