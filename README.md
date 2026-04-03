# TTRPG-Dust-Bot# README.md

## Bot Discord TTRPG với Google Sheets

Bot hỗ trợ roleplay TTRPG, quản lý biến từ Google Sheets, macro, hiệu ứng và combat.

### Yêu cầu

- Python 3.10+
- Discord Bot Token
- Google Service Account credentials (file JSON)

### Cài đặt

1. Clone repository
2. Tạo virtual environment: `python -m venv venv`
3. Kích hoạt: `source venv/bin/activate` (Linux/Mac) hoặc `venv\Scripts\activate` (Windows)
4. Cài đặt thư viện: `pip install -r requirements.txt`
5. Đặt file credentials JSON vào thư mục gốc (mặc định `credentials.json`)
6. Tạo file `.env` hoặc set biến môi trường:
   - `DISCORD_BOT_TOKEN` = token của bot
   - `GOOGLE_CREDENTIALS_FILE` = đường dẫn file JSON (mặc định credentials.json)
   - `TTRPG_SPREADSHEET_ID` = ID của Google Spreadsheet chính

### Chạy bot

```bash
python bot.py