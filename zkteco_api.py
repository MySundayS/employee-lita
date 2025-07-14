import sys
import logging
import os
import asyncio
import json
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from typing import Optional
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

# ลอง import pyzk ด้วยการ debug
try:
    from pyzk import ZK
    PYZK_AVAILABLE = True
    print(f"✅ pyzk library พร้อมใช้งาน (version: {ZK.__version__})")
except ImportError as e:
    PYZK_AVAILABLE = False
    print(f"❌ ไม่พบ pyzk library: {e}", file=sys.stderr)
    for path in sys.path:
        print(f"Python path: {path}", file=sys.stderr)

# ตัวแปรสถานะ
sync_running = False
last_sync_time = None
sync_status = "Not started"
sync_count = 0

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# === CONFIG ===
ZKTECO_IP = os.getenv("ZKTECO_IP", "192.168.1.2")
if not ZKTECO_IP:
    logger.warning("❌ ZKTECO_IP ไม่ได้ตั้งค่า - ใช้โหมด demo")
DEVICE_IP = ZKTECO_IP
DEVICE_PORT = int(os.getenv("ZKTECO_PORT", 4370))
SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME", "ZKTeco Attendance")
WORKSHEET_NAME = os.getenv("WORKSHEET_NAME", "Attendance")
SYNC_INTERVAL_SECONDS = int(os.getenv("SYNC_INTERVAL_SECONDS", 300))

# === SETUP CREDENTIALS ===
def setup_credentials():
    credentials_json = os.getenv("CREDENTIALS_JSON")
    if not credentials_json:
        logger.error("❌ CREDENTIALS_JSON ไม่ได้ตั้งค่าใน environment")
        return None
    try:
        credentials_dict = json.loads(credentials_json)
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        credentials = ServiceAccountCredentials.from_json_keyfile_dict(credentials_dict, scope)
        return credentials
    except Exception as e:
        logger.error(f"❌ การตั้งค่า credentials ล้มเหลว: {e}")
        return None

# === คลาสหลัก ===
class ZKTecoGoogleSheets:
    def __init__(self, device_ip, device_port=4370):
        self.device_ip = device_ip
        self.device_port = device_port
        self.zk_client = None

    def setup_google_sheets(self, credentials, spreadsheet_name, worksheet_name):
        try:
            gc = gspread.authorize(credentials)
            sh = gc.open(spreadsheet_name)
            worksheet = sh.worksheet(worksheet_name)
            logger.info(f"✅ เชื่อมต่อ Google Sheets: {spreadsheet_name}/{worksheet_name}")
            return worksheet
        except Exception as e:
            logger.error(f"❌ การเชื่อมต่อ Google Sheets ล้มเหลว: {e}")
            return None

    def connect_zkteco(self):
        """เชื่อมต่อกับ ZKTeco device"""
        if not PYZK_AVAILABLE or not self.device_ip:
            logger.error("ไม่สามารถเชื่อมต่อ ZKTeco: pyzk ไม่พร้อมใช้งาน หรือ ZKTECO_IP ไม่ได้ตั้งค่า")
            return False
        try:
            self.zk_client = ZK(self.device_ip, port=self.device_port)
            conn = self.zk_client.connect()
            if conn:
                logger.info(f"✅ เชื่อมต่อกับ ZKTeco ที่ {self.device_ip}:{self.device_port} สำเร็จ")
                return True
            else:
                logger.error(f"❌ ไม่สามารถเชื่อมต่อ ZKTeco ที่ {self.device_ip}:{self.device_port}")
                return False
        except Exception as e:
            logger.error(f"เกิดข้อผิดพลาดในการเชื่อมต่อ ZKTeco: {e}")
            return False

    def disconnect_zkteco(self):
        """ตัดการเชื่อมต่อจาก ZKTeco device"""
        if self.zk_client:
            self.zk_client.disconnect()
            logger.info("✅ ตัดการเชื่อมต่อจาก ZKTeco")

    def get_zkteco_attendance(self):
        """ดึงข้อมูล attendance จาก ZKTeco"""
        if not self.connect_zkteco():
            return []
        try:
            attendance = self.zk_client.get_attendance()
            logger.info(f"✅ ดึงข้อมูล attendance {len(attendance)} รายการจาก ZKTeco")
            return attendance
        except Exception as e:
            logger.error(f"เกิดข้อผิดพลาดในการดึง attendance: {e}")
            return []
        finally:
            self.disconnect_zkteco()

    def get_demo_data(self):
        """สร้างข้อมูล demo สำหรับทดสอบ"""
        logger.info("สร้างข้อมูล demo สำหรับทดสอบ (Cloud mode)")
        now = datetime.now()
        demo_data = []
        for days_ago in range(3):
            date = now.replace(hour=0, minute=0, second=0, microsecond=0) - datetime.timedelta(days=days_ago)
            for user_id in ['001', '002', '003', '004', '005']:
                checkin_time = date.replace(hour=8 + (int(user_id) % 2), minute=30 + (int(user_id) * 5) % 30)
                demo_data.append({
                    'user_id': user_id,
                    'name': f'พนักงาน {user_id}',
                    'timestamp': checkin_time,
                    'status': 1,
                    'punch': 1
                })
        return demo_data

    def get_data(self):
        """ดึงข้อมูลจาก ZKTeco หรือใช้ demo ถ้าไม่สำเร็จ"""
        if PYZK_AVAILABLE and self.device_ip:
            logger.info(f"🌐 โหมด Device: พยายามดึงข้อมูลจาก ZKTeco ที่ {self.device_ip}")
            attendance = self.get_zkteco_attendance()
            if attendance:
                return attendance
            else:
                logger.warning("❌ การเชื่อมต่อ ZKTeco ล้มเหลว - ใช้ข้อมูล demo")
        logger.info("🌐 Cloud Mode: ใช้ข้อมูล demo เนื่องจาก pyzk ไม่พร้อมใช้งานหรือ ZKTECO_IP ไม่ได้ตั้งค่า")
        return self.get_demo_data()

    def run_sync(self, credentials, spreadsheet_name, worksheet_name):
        """ซิงค์ข้อมูลหลัก"""
        try:
            worksheet = self.setup_google_sheets(credentials, spreadsheet_name, worksheet_name)
            if not worksheet:
                return False

            attendance_data = self.get_data()
            if not attendance_data:
                logger.info("ไม่มีข้อมูลที่ต้องซิงค์")
                return True

            existing_data = worksheet.get_all_values()
            existing_set = set()
            for row in existing_data[1:]:  # Skip header
                if len(row) >= 8:
                    existing_set.add((row[1], row[6], row[7]))  # user_id, date, time

            new_rows = []
            for data in attendance_data:
                if PYZK_AVAILABLE and hasattr(data, 'uid'):  # ตรวจสอบว่าเป็นข้อมูลจาก ZKTeco
                    record_id = f"{data.uid}_{data.timestamp.strftime('%Y%m%d_%H%M%S')}"
                    user_id = str(data.uid)
                    user_name = "Unknown"  # อาจต้องดึงชื่อจาก device ถ้ามี
                    timestamp = datetime.fromtimestamp(data.timestamp)
                    status = 1 if data.status == "Check In" else 0
                    punch = 1 if data.punch == "In" else 0
                else:  # Demo data
                    record_id = f"{data['user_id']}_{data['timestamp'].strftime('%Y%m%d_%H%M%S')}"
                    user_id = data['user_id']
                    user_name = data['name']
                    timestamp = data['timestamp']
                    status = data['status']
                    punch = data['punch']

                key = (user_id, timestamp.strftime('%Y-%m-%d'), timestamp.strftime('%H:%M:%S'))
                if key not in existing_set:
                    row = [
                        record_id,
                        user_id,
                        user_name,
                        timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                        status,
                        punch,
                        timestamp.strftime('%Y-%m-%d'),
                        timestamp.strftime('%H:%M:%S'),
                        self.device_ip + (" (Device)" if PYZK_AVAILABLE and self.device_ip else " (Cloud Demo)")
                    ]
                    new_rows.append(row)

            if new_rows:
                worksheet.append_rows(new_rows)
                logger.info(f"✅ เพิ่มข้อมูลใหม่ {len(new_rows)} รายการ {'(Device)' if PYZK_AVAILABLE and self.device_ip else '(Cloud Demo)'}")
            else:
                logger.info("ไม่มีข้อมูลใหม่ที่ต้องเพิ่ม")

            return True

        except Exception as e:
            logger.error(f"เกิดข้อผิดพลาดในการซิงค์: {e}")
            return False

# === BACKGROUND SYNC ===
async def background_sync_loop():
    global sync_running, last_sync_time, sync_status, sync_count
    while True:
        try:
            if sync_running:
                await asyncio.sleep(30)
                continue
                
            sync_running = True
            sync_status = "Running"
            logger.info(f"🔄 เริ่มซิงค์อัตโนมัติ {'(Device)' if PYZK_AVAILABLE and ZKTECO_IP else '(Cloud Mode)'}...")
            
            credentials = setup_credentials()
            if not credentials:
                sync_status = "Credentials error"
                logger.error("ไม่สามารถตั้งค่า Google Sheets credentials ได้")
                sync_running = False
                await asyncio.sleep(SYNC_INTERVAL_SECONDS)
                continue

            zk_sync = ZKTecoGoogleSheets(DEVICE_IP, DEVICE_PORT)
            success = zk_sync.run_sync(credentials, SPREADSHEET_NAME, WORKSHEET_NAME)
            
            if success:
                sync_status = "Success"
                sync_count += 1
                last_sync_time = datetime.now()
                logger.info(f"✅ ซิงค์อัตโนมัติสำเร็จ {'(Device)' if PYZK_AVAILABLE and ZKTECO_IP else '(Cloud Demo)'}")
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
    logger.info(f"🚀 FastAPI เริ่มทำงาน {'(Device Mode)' if PYZK_AVAILABLE and ZKTECO_IP else '(Cloud Mode)'}")
    asyncio.create_task(background_sync_loop())
    logger.info("🔄 Background sync เริ่มทำงาน")
    yield
    logger.info("🛑 FastAPI หยุดทำงาน")

# === FASTAPI APP ===
app = FastAPI(lifespan=lifespan, title="ZKTeco Cloud API", version="1.0.0")

# === ENDPOINT: Root ===
@app.get("/")
def read_root():
    return {
        "message": "ZKTeco FastAPI is running ✅",
        "mode": "Device" if PYZK_AVAILABLE and ZKTECO_IP else "Cloud Demo",
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
    if sync_running:
        raise HTTPException(status_code=409, detail="การซิงค์กำลังทำงานอยู่")
    
    try:
        credentials = setup_credentials()
        if not credentials:
            raise HTTPException(status_code=500, detail="ไม่สามารถตั้งค่า Google Sheets credentials ได้")
        
        zk_sync = ZKTecoGoogleSheets(DEVICE_IP, DEVICE_PORT)
        success = zk_sync.run_sync(credentials, SPREADSHEET_NAME, WORKSHEET_NAME)
        
        if success:
            return {
                "status": "success",
                "message": f"ซิงค์ข้อมูลสำเร็จ ✅ {'(Device)' if PYZK_AVAILABLE and ZKTECO_IP else '(Cloud Demo)'}",
                "mode": "Device" if PYZK_AVAILABLE and ZKTECO_IP else "Cloud Demo",
                "device_ip": DEVICE_IP,
                "timestamp": datetime.now().isoformat()
            }
        else:
            raise HTTPException(status_code500, detail="ซิงค์ไม่สำเร็จ ❌")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"เกิดข้อผิดพลาด: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# === ENDPOINT: Status ===
@app.get("/status")
def get_status():
    return {
        "status": "running",
        "mode": "Device" if PYZK_AVAILABLE and ZKTECO_IP else "Cloud Demo",
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

# === ENDPOINT: Test ZKTeco ===
@app.get("/test/zkteco")
def test_zkteco():
    """ทดสอบการเชื่อมต่อกับ ZKTeco device"""
    if not PYZK_AVAILABLE or not ZKTECO_IP:
        return {"detail": "pyzk library ไม่พร้อมใช้งาน หรือ ZKTECO_IP ไม่ได้ตั้งค่า - ใช้โหมด demo"}
    
    zk_sync = ZKTecoGoogleSheets(ZKTECO_IP, DEVICE_PORT)
    if zk_sync.connect_zkteco():
        attendance = zk_sync.get_zkteco_attendance()
        zk_sync.disconnect_zkteco()
        return {
            "status": "success ✅",
            "device_ip": ZKTECO_IP,
            "connection_status": "Connected to ZKTeco device ✅",
            "records": len(attendance),
            "sample_data": attendance[:5] if attendance else [],
            "mode": "Device"
        }
    else:
        return {"detail": "ไม่สามารถเชื่อมต่อ ZKTeco device ได้"}

# === ENDPOINT: Test Google Sheets ===
@app.get("/test/sheets")
def test_sheets_connection():
    try:
        credentials = setup_credentials()
        if not credentials:
            raise HTTPException(status_code=500, detail="ไม่สามารถตั้งค่า credentials ได้")
        
        gc = gspread.authorize(credentials)
        sh = gc.open(SPREADSHEET_NAME)
        worksheet = sh.worksheet(WORKSHEET_NAME)
        all_records = worksheet.get_all_values()
        
        return {
            "status": "success ✅",
            "spreadsheet_name": SPREADSHEET_NAME,
            "worksheet_name": WORKSHEET_NAME,
            "total_rows": len(all_records),
            "headers": all_records[0] if all_records else [],
            "connection_status": "Connected to Google Sheets ✅",
            "mode": "Device" if PYZK_AVAILABLE and ZKTECO_IP else "Cloud"
        }
        
    except Exception as e:
        logger.error(f"เกิดข้อผิดพลาด: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# === Health Check ===
@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "mode": "Device" if PYZK_AVAILABLE and ZKTECO_IP else "Cloud Demo"
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
