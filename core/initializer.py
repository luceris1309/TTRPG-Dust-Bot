from typing import Dict
from core.gsheet_client import GoogleSheetsClient
from config import (
    SHEET_INDEX, SHEET_CONFIG, SHEET_EFFECT, SHEET_TEMPLATE,
    DEFAULT_CONFIG_ROWS
)
from utils.helpers import get_logger

logger = get_logger()

async def init_system(client: GoogleSheetsClient) -> None:
    """Kiểm tra sự tồn tại của các sheet hệ thống. Nếu thiếu, tạo mới với cấu trúc mặc định"""
    existing_sheets = await client.get_all_sheets()
    
    if SHEET_INDEX not in existing_sheets:
        logger.info(f"Tạo sheet {SHEET_INDEX}")
        await client.create_sheet(SHEET_INDEX, ["discord_id", "sheet_id", "actor_name", "created_at"])
    else:
        logger.info(f"Sheet {SHEET_INDEX} đã tồn tại")
    
    if SHEET_CONFIG not in existing_sheets:
        logger.info(f"Tạo sheet {SHEET_CONFIG}")
        await client.create_sheet(SHEET_CONFIG, ["var", "a", "load"])
        sheet = await client.spreadsheet.worksheet(SHEET_CONFIG)
        rows = [[var, formula, "a"] for var, formula in DEFAULT_CONFIG_ROWS.items()]
        if rows:
            await sheet.append_rows(rows, value_input_option="USER_ENTERED")
        logger.info(f"Đã thêm {len(rows)} dòng mặc định vào CONFIG")
    else:
        logger.info(f"Sheet {SHEET_CONFIG} đã tồn tại")
    
    if SHEET_EFFECT not in existing_sheets:
        logger.info(f"Tạo sheet {SHEET_EFFECT}")
        await client.create_sheet(SHEET_EFFECT, ["var", "a", "load"])
        sheet = await client.spreadsheet.worksheet(SHEET_EFFECT)
        await sheet.append_row(["[example]B#affect({target.hp}, dot, -5, true, 3)", "", "a"])
        logger.info(f"Sheet {SHEET_EFFECT} đã tạo với dòng mẫu")
    else:
        logger.info(f"Sheet {SHEET_EFFECT} đã tồn tại")
    
    if SHEET_TEMPLATE not in existing_sheets:
        logger.info(f"Tạo sheet {SHEET_TEMPLATE}")
        await client.create_sheet(SHEET_TEMPLATE, ["var", "a", "load"])
        sheet = await client.spreadsheet.worksheet(SHEET_TEMPLATE)
        template_rows = [
            ["name", '"Nhân vật mới"', "a"],
            ["hp", "10", "a"],
            ["max_hp", "10", "a"],
            ["mp", "5", "a"],
            ["atk", "5", "a"],
            ["def", "3", "a"],
            ["agi", "3", "a"],
            ["action_limit", "1", "a"],
            ["initiative_formula", "1d20 + {agi}", "a"],
            ["skill_list", '["Đánh"]', "a"],
            ["macro_attack", "A//affect({target.hp}, add, -{atk}, true, 1)", "a"],
        ]
        await sheet.append_rows(template_rows, value_input_option="USER_ENTERED")
        logger.info(f"Sheet {SHEET_TEMPLATE} đã tạo với dữ liệu mẫu")
    else:
        logger.info(f"Sheet {SHEET_TEMPLATE} đã tồn tại")

async def load_global_config(client: GoogleSheetsClient) -> Dict[str, str]:
    """Load dữ liệu từ sheet CONFIG, trả về dict { var: value } theo cột load"""
    config_data = await client.batch_load_by_load_column(SHEET_CONFIG)
    result = {}
    for var, col_map in config_data.items():
        for value in col_map.values():
            result[var] = value
            break
    logger.info(f"Đã load {len(result)} dòng từ CONFIG")
    return result

async def load_effect_pool(client: GoogleSheetsClient) -> Dict[str, str]:
    """Load effect_pool từ sheet Effect, trả về dict { effect_name: raw_content }"""
    effect_data = await client.batch_load_by_load_column(SHEET_EFFECT)
    result = {}
    for var, col_map in effect_data.items():
        for content in col_map.values():
            if content and content.strip():
                result[var] = content
            break
    logger.info(f"Đã load {len(result)} effect từ Effect sheet")
    return result