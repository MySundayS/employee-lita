from fastapi import FastAPI, HTTPException
from typing import Optional
import logging
import os
import asyncio
import json
from contextlib import asynccontextmanager

# ลอง import pyzk ด้วยวิธีปลอดภัย
try:
    from pyzk import ZK
    PYZK_AVAILABLE = True
    print("✅ pyzk library พร้อมใช้งาน")
except ImportError:
    try:
        from zk import ZK
        PYZK_AVAILABLE = True
        print("✅ zk library พร้อมใช้งาน")
    except ImportError:
        PYZK_AVAILABLE = False
        print("❌ ไม่พบ ZKTeco library - ใช้โหมด demo")

import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import time

# === ตัวแปรสถานะ ===
sync_running = False
last_sync_time = None
sync_status = "Not started"
sync_count = 0

# === Logging Setup ===
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# === CONFIG ===
DEVICE_IP = os.getenv("ZKTECO_IP", "192.168.1.2")
DEVICE_PORT = int(os.getenv("DEVICE_PORT", 4370))
SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME", "ZKTeco Attendance")
WORKSHEET_NAME = os.getenv("WORKSHEET_NAME", "Attendance")
SYNC_INTERVAL_SECONDS = int(os.getenv("SYNC_INTERVAL_SECONDS", 300))

# === Setup Credentials ===
def setup_credentials():
    """ตั้งค่า Google Sheets credentials"""
    credentials_json = os.getenv("CREDENTIALS_JSON")
    
    if credentials_json:
        try:
            # Parse JSON from environment variable
            creds_dict = json.loads(credentials_json)
            return Credentials.from_service_account_info(creds_dict, scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"
            ])
        except Exception as e:
            logger.error(f"Error parsing credentials from environment: {e}")
            return None
    else:
        # Fallback to file (for local development)
        credentials_file = "credentials.json"
        if os.path.exists(credentials_file):
            return Credentials.from_service_account_file(credentials_file, scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"
            ])
        
    logger.error("ไม่พบ credentials")
    return None

# === คลาสหลัก ===
class ZKTecoGoogleSheets:
    def __init__(self, device_ip, device_port=4370):
        self.device_ip = device_ip
        self.device_port = device_port

    def setup_google_sheets(self, credentials, spreadsheet_name, worksheet_name):
        """ตั้งค่า Google Sheets"""
        try:
            gc = gspread.authorize(credentials)
            
            # เปิด spreadsheet
            try:
                sh = gc.open(spreadsheet_name)
            except gspread.SpreadsheetNotFound:
                sh = gc.create(spreadsheet_name)
                logger.info(f"สร้าง spreadsheet ใหม่: {spreadsheet_name}")
            
            # เปิด worksheet
            try:
                worksheet = sh.worksheet(worksheet_name)
            except gspread.WorksheetNotFound:
                worksheet = sh.add_worksheet(title=worksheet_name, rows="1000", cols="20")
                logger.info(f"สร้าง worksheet ใหม่: {worksheet_name}")
            
            # ตั้งค่า headers
            headers = ["ID", "User ID", "Name", "Timestamp", "Status", "Punch", "Date", "Time", "Device IP"]
            try:
                existing_headers = worksheet.row_values(1)
                if not existing_headers or existing_headers != headers:
                    worksheet.update('A1:I1', [headers])
                    logger.info("อัปเดท headers แล้ว")
            except Exception as e:
                logger.warning(f"ไม่สามารถอัปเดท headers: {e}")
            
            return worksheet
            
        except Exception as e:
            logger.error(f"ไม่สามารถตั้งค่า Google Sheets: {e}")
            return None

    def get_demo_data(self):
        """สร้างข้อมูล demo สำหรับทดสอบ"""
        logger.info("สร้างข้อมูล demo สำหรับทดสอบ (Cloud mode)")
        now = datetime.now()
        demo_data = []
        
        # สร้างข้อมูล 3 วันที่ผ่านมา
        for days_ago in range(3):
            date = now.replace(hour=0, minute=0, second=0, microsecond=0) - datetime.timedelta(days=days_ago)
            
            # พนักงาน 5 คน
            for user_id in ['001', '002', '003', '004', '005']:
                # เข้างาน
                checkin_time = date.replace(
                    hour=8 + (int(user_id) % 2),
                    minute=30 + (int(user_id) * 5) % 30
                )
                demo_data.append({
                    'user_id': user_id,
                    'name': f'พนักงาน {user_id}',
                    'timestamp': checkin_time,
                    'status': 1,
                    'punch': 1
                })
                
                # พักเที่ยง - ออก
                lunch_out = date.replace(hour=12, minute=0)
                demo_data.append({
                    'user_id': user_id,
                    'name': f'พนักงาน {user_id}',
                    'timestamp': lunch_out,
                    'status': 0,
                    'punch': 0
                })
                
                # พักเที่ยง - กลับ
                lunch_in = date.replace(hour=13, minute=0)
                demo_data.append({
                    'user_id': user_id,
                    'name': f'พนักงาน {user_id}',
                    'timestamp': lunch_in,
                    'status': 1,
                    'punch': 1
                })
                
                # เลิกงาน
                checkout_time = date.replace(
                    hour=17 + (int(user_id) % 2),
                    minute=30 - (int(user_id) * 3) % 30
                )
                demo_data.append({
                    'user_id': user_id,
                    'name': f'พนักงาน {user_id}',
                    'timestamp': checkout_time,
                    'status': 0,
                    'punch': 0
                })
        
        return demo_data

    def run_sync(self, credentials, spreadsheet_name, worksheet_name):
        """ซิงค์ข้อมูลหลัก"""
        try:
            # ตั้งค่า Google Sheets
            worksheet = self.setup_google_sheets(credentials, spreadsheet_name, worksheet_name)
            if not worksheet:
                return False

            # ในโหมด Cloud ใช้ demo data เสมอ (เพราะไม่สามารถเชื่อมต่อ ZKTeco ได้)
            logger.info("🌐 Cloud Mode: ใช้ข้อมูล demo")
            demo_data = self.get_demo_data()

            if not demo_data:
                logger.info("ไม่มีข้อมูล demo")
                return True

            # ตรวจสอบข้อมูลที่มีอยู่แล้ว
            try:
                existing_data = worksheet.get_all_values()
                existing_set = set()
                for row in existing_data[1:]:  # Skip header
                    if len(row) >= 8:
                        existing_set.add((row[1], row[6], row[7]))  # user_id, date, time
            except Exception as e:
                logger.warning(f"ไม่สามารถดึงข้อมูลที่มีอยู่: {e}")
                existing_set = set()

            # กรองเฉพาะข้อมูลใหม่
            new_rows = []
            for data in demo_data:
                record_id = f"{data['user_id']}_{data['timestamp'].strftime('%Y%m%d_%H%M%S')}"
                user_id = data['user_id']
                user_name = data['name']
                timestamp = data['timestamp']
                
                key = (user_id, timestamp.strftime('%Y-%m-%d'), timestamp.strftime('%H:%M:%S'))
                
                if key not in existing_set:
                    row = [
                        record_id,
                        user_id,
                        user_name,
                        timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                        data['status'],
                        data['punch'],
                        timestamp.strftime('%Y-%m-%d'),
                        timestamp.strftime('%H:%M:%S'),
                        self.device_ip + " (Cloud Demo)"
                    ]
                    new_rows.append(row)

            if new_rows:
                # เพิ่มข้อมูลใหม่
                worksheet.append_rows(new_rows)
                logger.info(f"✅ เพิ่มข้อมูลใหม่ {len(new_rows)} รายการ (Cloud Demo)")
            else:
                logger.info("ไม่มีข้อมูลใหม่ที่ต้องเพิ่ม")

            return True

        except Exception as e:
            logger.error(f"เกิดข้อผิดพลาดในการซิงค์: {e}")
            return False

# === BACKGROUND SYNC ===
async def background_sync_loop():
    """ลูปซิงค์ในพื้นหลัง"""
    global sync_running, last_sync_time, sync_status, sync_count
    
    while True:
        try:
            if sync_running:
                await asyncio.sleep(30)
                continue
                
            sync_running = True
            sync_status = "Running"
            logger.info("🔄 เริ่มซิงค์อัตโนมัติ (Cloud Mode)...")
            
            # ตั้งค่า credentials
            credentials = setup_credentials()
            if not credentials:
                sync_status = "Credentials error"
                logger.error("ไม่สามารถตั้งค่า Google Sheets credentials ได้")
                sync_running = False
                await asyncio.sleep(SYNC_INTERVAL_SECONDS)
                continue

            # ซิงค์ข้อมูล
            zk_sync = ZKTecoGoogleSheets(DEVICE_IP, DEVICE_PORT)
            success = zk_sync.run_sync(credentials, SPREADSHEET_NAME, WORKSHEET_NAME)
            
            if success:
                sync_status = "Success"
                sync_count += 1
                last_sync_time = datetime.now()
                logger.info("✅ ซิงค์อัตโนมัติสำเร็จ (Cloud Demo)")
            else:
                sync_status = "Failed"
                logger.warning("❌ ซิงค์อัตโนมัติล้มเหลว")
                
        except Exception as e:
            sync_status = f"Error: {str(e)}"
            logger.error(f"เกิดข้อผิดพลาดในซิงค์อัตโนมัติ: {e}")
        finally:
            sync_running = False
            await asyncio.sleep(SYNC_INTERVAL_SECONDS)

# === FastAPI Lifespan ===
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("🚀 FastAPI เริ่มทำงาน (Cloud Mode)")
    
    # เริ่ม background sync
    asyncio.create_task(background_sync_loop())
    logger.info("🔄 Background sync เริ่มทำงาน")
    
    yield
    
    # Shutdown
    logger.info("🛑 FastAPI หยุดทำงาน")

# === FASTAPI APP ===
app = FastAPI(lifespan=lifespan, title="ZKTeco Cloud API", version="1.0.0")

# === ENDPOINT: Root ===
@app.get("/")
def read_root():
    return {
        "message": "ZKTeco FastAPI is running ✅ (Cloud Mode)",
        "mode": "Cloud Demo",
        "pyzk_available": PYZK_AVAILABLE,
        "sync_status": sync_status,
        "sync_count": sync_count,
        "last_sync": last_sync_time.isoformat() if last_sync_time else None,
        "sync_running": sync_running,
        "device_ip": DEVICE_IP,
        "environment": "Cloud"
    }

# === ENDPOINT: Manual sync ===
@app.get("/sync")
def sync_attendance():
    """ซิงค์ข้อมูลทันที"""
    if sync_running:
        raise HTTPException(status_code=409, detail="การซิงค์กำลังทำงานอยู่")
    
    try:
        # ตั้งค่า credentials
        credentials = setup_credentials()
        if not credentials:
            raise HTTPException(status_code=500, detail="ไม่สามารถตั้งค่า Google Sheets credentials ได้")
        
        zk_sync = ZKTecoGoogleSheets(DEVICE_IP, DEVICE_PORT)
        success = zk_sync.run_sync(credentials, SPREADSHEET_NAME, WORKSHEET_NAME)
        
        if success:
            return {
                "status": "success", 
                "message": "ซิงค์ข้อมูลสำเร็จ ✅ (Cloud Demo)",
                "mode": "Cloud Demo",
                "device_ip": DEVICE_IP,
                "timestamp": datetime.now().isoformat()
            }
        else:
            raise HTTPException(status_code=500, detail="ซิงค์ไม่สำเร็จ ❌")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"เกิดข้อผิดพลาด: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# === ENDPOINT: Status ===
@app.get("/status")
def get_status():
    """ตรวจสอบสถานะระบบ"""
    return {
        "status": "running",
        "mode": "Cloud Demo",
        "pyzk_available": PYZK_AVAILABLE,
        "sync_status": sync_status,
        "sync_count": sync_count,
        "sync_running": sync_running,
        "last_sync": last_sync_time.isoformat() if last_sync_time else None,
        "device_ip": DEVICE_IP,
        "sync_interval": SYNC_INTERVAL_SECONDS,
        "timestamp": datetime.now().isoformat(),
        "environment": "Cloud"
    }

# === ENDPOINT: Test Google Sheets ===
@app.get("/test/sheets")
def test_sheets_connection():
    """ทดสอบการเชื่อมต่อกับ Google Sheets"""
    try:
        credentials = setup_credentials()
        if not credentials:
            raise HTTPException(status_code=500, detail="ไม่สามารถตั้งค่า credentials ได้")
        
        gc = gspread.authorize(credentials)
        sh = gc.open(SPREADSHEET_NAME)
        worksheet = sh.worksheet(WORKSHEET_NAME)
        
        # ดึงข้อมูลจำนวนแถว
        all_records = worksheet.get_all_values()
        
        return {
            "status": "success ✅",
            "spreadsheet_name": SPREADSHEET_NAME,
            "worksheet_name": WORKSHEET_NAME,
            "total_rows": len(all_records),
            "headers": all_records[0] if all_records else [],
            "connection_status": "Connected to Google Sheets ✅",
            "mode": "Cloud"
        }
        
    except Exception as e:
        logger.error(f"เกิดข้อผิดพลาด: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# === Health Check ===
@app.get("/health")
def health_check():
    """Health check endpoint สำหรับ Render"""
    return {
        "status": "healthy", 
        "timestamp": datetime.now().isoformat(),
        "mode": "Cloud Demo"
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
