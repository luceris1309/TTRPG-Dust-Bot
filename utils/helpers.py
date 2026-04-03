import asyncio
import logging
import re
import random
import time
from typing import Any, List, Optional, Union

# -------------------- LOGGER --------------------
_logger = None

def setup_logger(name: str = "ttrpg_bot", level: int = logging.INFO) -> logging.Logger:
    """Tạo và cấu hình logger ghi ra console và file"""
    global _logger
    if _logger:
        return _logger
    logger = logging.getLogger(name)
    logger.setLevel(level)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    fh = logging.FileHandler('bot.log', encoding='utf-8')
    fh.setFormatter(formatter)
    logger.addHandler(fh)
    _logger = logger
    return logger

def get_logger() -> logging.Logger:
    return _logger if _logger else setup_logger()

# -------------------- RATE LIMITER --------------------
class RateLimiter:
    """Giới hạn số lượng request đồng thời đến Google Sheets API"""
    def __init__(self, max_concurrent: int = 5):
        self._semaphore = asyncio.Semaphore(max_concurrent)
    
    async def acquire(self):
        await self._semaphore.acquire()
    
    def release(self):
        self._semaphore.release()
    
    async def __aenter__(self):
        await self.acquire()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.release()

# -------------------- DICE ROLLER --------------------
DICE_PATTERN = re.compile(
    r'^(\d+)d(\d+)(?:(!|=[eE]?=?|!=une))?(?:\(?(kh|kl)\)?)?$',
    re.IGNORECASE
)

def roll_dice(expression: str) -> List[int]:
    """
    Roll dice theo cú pháp: nDf[!|=e!|!=une][kh|kl]
    Trả về danh sách kết quả các viên (sau khi lọc nếu có keep)
    """
    expr = expression.strip().lower()
    m = DICE_PATTERN.match(expr)
    if not m:
        raise ValueError(f"Cú pháp dice không hợp lệ: {expression}")
    num = int(m.group(1))
    faces = int(m.group(2))
    explode_mode = m.group(3) or ''
    keep_mode = m.group(4) or ''
    
    results = []
    for _ in range(num):
        roll = random.randint(1, faces)
        results.append(roll)
        if explode_mode:
            if explode_mode == '!':
                while roll == faces:
                    roll = random.randint(1, faces)
                    results.append(roll)
            elif explode_mode.startswith('='):
                target = int(explode_mode[1:]) if explode_mode[1:].isdigit() else faces
                while roll == target:
                    roll = random.randint(1, faces)
                    results.append(roll)
            elif explode_mode == '!=une':
                while roll != 1:
                    roll = random.randint(1, faces)
                    results.append(roll)
    
    if keep_mode == 'kh':
        results = [max(results)] if results else []
    elif keep_mode == 'kl':
        results = [min(results)] if results else []
    
    return results

def roll_dice_sum(expression: str) -> int:
    """Roll dice và trả về tổng"""
    return sum(roll_dice(expression))

# -------------------- HELPER FUNCTIONS --------------------
def safe_divide(a: Union[int, float], b: Union[int, float]) -> float:
    """Chia an toàn, tránh chia cho 0"""
    if b == 0:
        return 0.0
    return a / b

def deep_copy_dict(original: dict) -> dict:
    """Sao chép sâu dictionary đơn giản (chỉ hỗ trợ các giá trị có thể copy bằng dict())"""
    return {k: (v.copy() if hasattr(v, 'copy') else v) for k, v in original.items()}

def parse_mention(mention: str) -> Optional[str]:
    """Trích xuất user_id từ mention dạng <@123456789> hoặc @tên"""
    if mention.startswith('<@') and mention.endswith('>'):
        return mention[2:-1]
    return None

def truncate_string(s: str, max_len: int = 2000) -> str:
    """Cắt chuỗi nếu quá dài, thêm ..."""
    if len(s) <= max_len:
        return s
    return s[:max_len-3] + "..."

def format_macro_result(actor_name: str, macro_name: str, result_text: str, error: bool = False) -> str:
    """Định dạng kết quả macro để gửi Discord"""
    icon = "⚠️" if error else "🎭"
    return f"{icon} **{actor_name}** sử dụng `{macro_name}`:\n{truncate_string(result_text)}"

# -------------------- THỜI GIAN --------------------
def current_timestamp() -> float:
    return time.time()

# -------------------- KIỂM TRA ĐIỀU KIỆN --------------------
def eval_condition(cond: Any, context: dict) -> bool:
    """
    Đánh giá điều kiện đơn giản (có thể là bool, int, hoặc chuỗi logic)
    context chứa các biến cần thiết. Tạm thời chỉ xử lý giá trị truthy.
    """
    if isinstance(cond, bool):
        return cond
    if isinstance(cond, (int, float)):
        return cond != 0
    if isinstance(cond, str):
        lower = cond.lower()
        if lower == "true":
            return True
        if lower == "false":
            return False
        return bool(cond)
    return bool(cond)