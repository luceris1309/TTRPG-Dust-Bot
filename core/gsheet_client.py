# core/gsheet_client.py
# Quản lý kết nối Google Sheets, cache, batch load theo cột load

import asyncio
from typing import Dict, Any, Optional, List, Tuple
import gspread_asyncio
from google.oauth2.service_account import Credentials
from config import SPREADSHEET_ID, TTL_SECONDS, MAX_CONCURRENT_GAPI_REQUESTS
from utils.helpers import get_logger, RateLimiter, current_timestamp

logger = get_logger()

class GoogleSheetsClient:
    """Client quản lý Google Sheets với cache TTL và rate limiter"""
    
    def __init__(self, credentials_file: str):
        self.credentials_file = credentials_file
        self.client = None
        self.spreadsheet = None
        self._cache: Dict[str, Tuple[Any, float]] = {}  # key -> (value, timestamp)
        self._rate_limiter = RateLimiter(MAX_CONCURRENT_GAPI_REQUESTS)
        self._lock = asyncio.Lock()
    
    async def initialize(self):
        """Khởi tạo kết nối đến Google Sheets"""
        creds = Credentials.from_service_account_file(
            self.credentials_file,
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        self.client = gspread_asyncio.AsyncioGspreadClient(creds)
        self.spreadsheet = await self.client.open_by_key(SPREADSHEET_ID)
        logger.info(f"Đã kết nối đến spreadsheet: {SPREADSHEET_ID}")
    
    def _make_cache_key(self, sheet_name: str, var: str, index: str) -> str:
        return f"{sheet_name}!{var}.{index}"
    
    async def get_cell(self, sheet_name: str, var: str, index: str, force_reload: bool = False) -> Any:
        """
        Lấy giá trị ô (var, index) từ sheet, có cache TTL.
        Nếu force_reload=True, bỏ qua cache.
        """
        key = self._make_cache_key(sheet_name, var, index)
        
        if not force_reload and key in self._cache:
            value, timestamp = self._cache[key]
            if current_timestamp() - timestamp < TTL_SECONDS:
                return value
            # Hết hạn, xóa cache cũ
            del self._cache[key]
        
        async with self._rate_limiter:
            try:
                sheet = await self.spreadsheet.worksheet(sheet_name)
                # Tìm hàng có var = var
                cell_list = await sheet.findall(var, in_column=1)
                if not cell_list:
                    logger.warning(f"Không tìm thấy var '{var}' trong sheet '{sheet_name}'")
                    return 0
                row_num = cell_list[0].row
                # Lấy cột index (theo tên cột)
                all_headers = await sheet.row_values(1)
                if index not in all_headers:
                    logger.warning(f"Cột '{index}' không tồn tại trong sheet '{sheet_name}'")
                    return 0
                col_num = all_headers.index(index) + 1
                cell_value = await sheet.cell(row_num, col_num)
                value = cell_value.value
                # Lưu cache
                self._cache[key] = (value, current_timestamp())
                return value
            except Exception as e:
                logger.error(f"Lỗi khi đọc ô ({var}.{index}) từ sheet '{sheet_name}': {e}")
                return 0
    
    async def batch_load_by_load_column(self, sheet_name: str) -> Dict[str, Dict[str, Any]]:
        """
        Batch load toàn bộ sheet: với mỗi hàng, đọc cột 'load' để biết cột nào cần load.
        Trả về dict: { var: { index: value } }
        """
        result = {}
        try:
            async with self._rate_limiter:
                sheet = await self.spreadsheet.worksheet(sheet_name)
                all_rows = await sheet.get_all_values()
                if len(all_rows) < 2:
                    return result
                headers = all_rows[0]
                if 'var' not in headers or 'load' not in headers:
                    logger.warning(f"Sheet '{sheet_name}' thiếu cột 'var' hoặc 'load'")
                    return result
                var_col_idx = headers.index('var')
                load_col_idx = headers.index('load')
                
                for row_idx, row in enumerate(all_rows[1:], start=2):
                    if len(row) <= var_col_idx or not row[var_col_idx]:
                        continue
                    var_name = row[var_col_idx].strip()
                    if not var_name:
                        continue
                    load_col_name = row[load_col_idx].strip() if len(row) > load_col_idx else ''
                    if not load_col_name:
                        continue  # Không có cột load thì bỏ qua hàng này
                    if load_col_name not in headers:
                        logger.warning(f"Cột load '{load_col_name}' không tồn tại trong sheet '{sheet_name}', bỏ qua hàng {var_name}")
                        continue
                    col_idx = headers.index(load_col_name)
                    value = row[col_idx] if len(row) > col_idx else ''
                    if var_name not in result:
                        result[var_name] = {}
                    result[var_name][load_col_name] = value
                    # Lưu vào cache luôn
                    cache_key = self._make_cache_key(sheet_name, var_name, load_col_name)
                    self._cache[cache_key] = (value, current_timestamp())
                return result
        except Exception as e:
            logger.error(f"Lỗi batch load sheet '{sheet_name}': {e}")
            return result
    
    async def duplicate_sheet(self, source_sheet_name: str, new_sheet_name: str) -> bool:
        """Tạo bản sao của sheet template"""
        try:
            async with self._rate_limiter:
                source = await self.spreadsheet.worksheet(source_sheet_name)
                # Gspread không có duplicate trực tiếp, dùng API copy
                new_sheet = await source.duplicate(new_sheet_name=new_sheet_name)
                logger.info(f"Đã tạo sheet mới '{new_sheet_name}' từ '{source_sheet_name}'")
                return True
        except Exception as e:
            logger.error(f"Lỗi duplicate sheet: {e}")
            return False
    
    async def update_cell(self, sheet_name: str, var: str, index: str, value: str) -> bool:
        """Ghi đè giá trị ô (dùng cho persist)"""
        try:
            async with self._rate_limiter:
                sheet = await self.spreadsheet.worksheet(sheet_name)
                cell_list = await sheet.findall(var, in_column=1)
                if not cell_list:
                    return False
                row_num = cell_list[0].row
                headers = await sheet.row_values(1)
                if index not in headers:
                    return False
                col_num = headers.index(index) + 1
                await sheet.update_cell(row_num, col_num, str(value))
                # Xóa cache cũ
                cache_key = self._make_cache_key(sheet_name, var, index)
                if cache_key in self._cache:
                    del self._cache[cache_key]
                return True
        except Exception as e:
            logger.error(f"Lỗi update cell: {e}")
            return False
    
    async def sheet_exists(self, sheet_name: str) -> bool:
        try:
            await self.spreadsheet.worksheet(sheet_name)
            return True
        except:
            return False
    
    async def create_sheet(self, sheet_name: str, headers: List[str]) -> bool:
        """Tạo sheet mới với hàng tiêu đề"""
        try:
            async with self._rate_limiter:
                worksheet = await self.spreadsheet.add_worksheet(title=sheet_name, rows=100, cols=20)
                await worksheet.update([headers], range_name='A1')
                return True
        except Exception as e:
            logger.error(f"Lỗi tạo sheet '{sheet_name}': {e}")
            return False
    
    def clear_cache(self):
        """Xóa toàn bộ cache"""
        self._cache.clear()
        logger.info("Đã xóa cache GGS")
    
    async def get_all_sheets(self) -> List[str]:
        """Lấy danh sách tất cả sheet trong spreadsheet"""
        try:
            worksheets = await self.spreadsheet.worksheets()
            return [ws.title for ws in worksheets]
        except Exception as e:
            logger.error(f"Lỗi lấy danh sách sheet: {e}")
            return []