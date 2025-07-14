from fastapi import FastAPI, HTTPException, BackgroundTasks
from contextlib import asynccontextmanager
from typing import Optional
import logging
import os
import asyncio
import ipaddress
from pyzk import ZK
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import time
import json
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
import traceback

# === Logging Setup ===
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# === Config ===
DEVICE_PORT = int(os.getenv("DEVICE_PORT", 4370))
SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME", "ZKTeco Attendance")
WORKSHEET_NAME = os.getenv("WORKSHEET_NAME", "Attendance")
SYNC_INTERVAL_SECONDS = int(os.getenv("SYNC_INTERVAL_SECONDS", 5 * 60))  # 5 นาที
RETRY_ATTEMPTS = 3
RETRY_DELAY = 5  # ลดเวลา retry
DEVICE_IP = os.getenv("ZKTECO_IP")
ZKTECO_SUBNET = os.getenv("ZKTECO_SUBNET", "192.168.1.0/24")

# === Google Sheets Credentials Setup ===
def setup_credentials():
    credentials_data = os.getenv("CREDENTIALS_JSON")
    if credentials_data:
        try:
            # Parse JSON from environment variable
            creds_dict = json.loads(credentials_data)
            return Credentials.from_service_account_info(creds_dict, scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"
            ])
        except Exception as e:
            logger.error(f"Error parsing credentials from environment: {e}")
            return None
    else:
        credentials_file = os.getenv("CREDENTIALS_FILE", "/path/to/credentials.json")
        if os.path.exists(credentials_file):
            return Credentials.from_service_account_file(credentials_file, scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"
            ])
    return None

# === Global Variables ===
sync_running = False
last_sync_time = None
sync_status = "Not started"
executor = ThreadPoolExecutor(max_workers=2)

# === ฟังก์ชันค้นหา IP อุปกรณ์ ZKTeco ===
def find_zkteco_device(subnet=None, port=4370, timeout=3):
    """ค้นหาอุปกรณ์ ZKTeco ในเครือข่าย"""
    if DEVICE_IP:
        logger.info(f"ใช้ IP ที่กำหนดไว้: {DEVICE_IP}")
        return DEVICE_IP
    
    if not subnet:
        subnet = ZKTECO_SUBNET
    
    logger.info(f"🔍 กำลังค้นหาอุปกรณ์ ZKTeco ในเครือข่าย {subnet}...")
    
    try:
        network = ipaddress.IPv4Network(subnet, strict=False)
        # จำกัดการสแกนเฉพาะ IP ที่น่าจะเป็นได้
        common_ips = [f"{network.network_address.exploded[:-1]}1", 
                     f"{network.network_address.exploded[:-1]}100",
                     f"{network.network_address.exploded[:-1]}200"]
        
        for ip in common_ips:
            try:
                zk = ZK(ip, port=port, timeout=timeout)
                conn = zk.connect()
                if conn:
                    logger.info(f"✅ พบอุปกรณ์ ZKTeco ที่ IP: {ip}")
                    conn.disconnect()
                    return ip
            except Exception as e:
                continue
                
        # ถ้าไม่พบใน common IPs ให้สแกนเฉพาะ 50 IP แรก
        for ip in list(network.hosts())[:50]:
            try:
                zk = ZK(str(ip), port=port, timeout=timeout)
                conn = zk.connect()
                if conn:
                    logger.info(f"✅ พบอุปกรณ์ ZKTeco ที่ IP: {ip}")
                    conn.disconnect()
                    return str(ip)
            except Exception:
                continue
                
    except Exception as e:
        logger.error(f"Error in device discovery: {e}")
    
    logger.warning("❌ ไม่พบอุปกรณ์ ZKTeco ในเครือข่าย")
    return None

# === คลาสหลัก ===
class ZKTecoGoogleSheets:
    def __init__(self, device_ip, device_port=4370):
        self.device_ip = device_ip
        self.device_port = device_port

    def sync_attendance(self, credentials):
        """ซิงค์ข้อมูลการเข้าออกงาน"""
        try:
            # เชื่อมต่อ Google Sheets
            gc = gspread.authorize(credentials)
            sh = gc.open(SPREADSHEET_NAME)
            worksheet = sh.worksheet(WORKSHEET_NAME)

            # เชื่อมต่อ ZKTeco
            zk = ZK(self.device_ip, port=self.device_port, timeout=10)
            conn = zk.connect()
            if not conn:
                raise Exception("ไม่สามารถเชื่อมต่อกับ ZKTeco ได้")

            # ดึงข้อมูลการเข้าออกงาน
            attendances = conn.get_attendance()
            conn.disconnect()

            if not attendances:
                logger.info("ไม่พบข้อมูลการเข้าออกงานใหม่")
                return True

            # เตรียมข้อมูลสำหรับ Google Sheets
            data = []
            for att in attendances:
                if att.timestamp and att.timestamp >= datetime(2025, 1, 1):
                    row = [
                        "",  # Column A: Empty
                        str(att.user_id),  # Column B: User ID
                        "",  # Column C: Empty
                        att.timestamp.strftime('%Y-%m-%d %H:%M:%S'),  # Column D: Full timestamp
                        "",  # Column E: Empty
                        int(att.punch),  # Column F: Punch type
                        att.timestamp.strftime('%Y-%m-%d'),  # Column G: Date
                        att.timestamp.strftime('%H:%M:%S'),  # Column H: Time
                        self.device_ip  # Column I: Device IP
                    ]
                    data.append(row)

            if not data:
                logger.info("ไม่พบข้อมูลใหม่ที่ต้องซิงค์")
                return True

            # ตรวจสอบข้อมูลที่มีอยู่แล้ว
            existing_data = worksheet.get_all_values()
            existing_set = set()
            for row in existing_data[1:]:  # Skip header
                if len(row) > 7:
                    existing_set.add((row[1], row[6], row[7]))  # user_id, date, time

            # กรองข้อมูลใหม่
            new_rows = []
            for row in data:
                if (row[1], row[6], row[7]) not in existing_set:
                    new_rows.append(row)

            if new_rows:
                worksheet.append_rows(new_rows)
                logger.info(f"✅ เพิ่ม {len(new_rows)} รายการใหม่ลง Google Sheets")
            else:
                logger.info("ไม่พบข้อมูลใหม่ที่ต้องเพิ่ม")

            return True

        except Exception as e:
            logger.error(f"Sync error: {e}")
            logger.error(traceback.format_exc())
            return False

# === Background Sync Function ===
def run_background_sync():
    """รันการซิงค์ในพื้นหลัง"""
    global sync_running, last_sync_time, sync_status
    
    while True:
        try:
            if sync_running:
                time.sleep(30)  # รอถ้า sync กำลังทำงาน
                continue
                
            sync_running = True
            sync_status = "Running"
            logger.info("🔄 เริ่มซิงค์อัตโนมัติ...")
            
            # ค้นหาอุปกรณ์
            device_ip = find_zkteco_device()
            if not device_ip:
                sync_status = "Device not found"
                logger.warning("ไม่พบอุปกรณ์ ZKTeco")
                sync_running = False
                time.sleep(SYNC_INTERVAL_SECONDS)
                continue

            # ตั้งค่า credentials
            credentials = setup_credentials()
            if not credentials:
                sync_status = "Credentials error"
                logger.error("ไม่สามารถตั้งค่า Google Sheets credentials ได้")
                sync_running = False
                time.sleep(SYNC_INTERVAL_SECONDS)
                continue

            # ซิงค์ข้อมูล
            zk_sync = ZKTecoGoogleSheets(device_ip, DEVICE_PORT)
            success = zk_sync.sync_attendance(credentials)
            
            if success:
                sync_status = "Success"
                last_sync_time = datetime.now()
                logger.info("✅ ซิงค์อัตโนมัติสำเร็จ")
            else:
                sync_status = "Failed"
                logger.warning("❌ ซิงค์อัตโนมัติล้มเหลว")
                
        except Exception as e:
            sync_status = f"Error: {str(e)}"
            logger.error(f"Background sync error: {e}")
            logger.error(traceback.format_exc())
        finally:
            sync_running = False
            time.sleep(SYNC_INTERVAL_SECONDS)

# === FastAPI Lifespan ===
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("🚀 FastAPI เริ่มทำงาน")
    # เริ่ม background sync ใน thread แยก
    sync_thread = threading.Thread(target=run_background_sync, daemon=True)
    sync_thread.start()
    logger.info("🔄 Background sync เริ่มทำงาน")
    
    yield
    
    # Shutdown
    logger.info("🛑 FastAPI หยุดทำงาน")
    executor.shutdown(wait=True)

# === FastAPI App ===
app = FastAPI(lifespan=lifespan)

# === Endpoints ===
@app.get("/")
def read_root():
    return {
        "message": "ZKTeco FastAPI is running ✅",
        "status": sync_status,
        "last_sync": last_sync_time.isoformat() if last_sync_time else None,
        "sync_running": sync_running
    }

@app.get("/sync")
async def sync_attendance():
    """ซิงค์ข้อมูลทันที"""
    try:
        # ป้องกัน concurrent sync
        if sync_running:
            raise HTTPException(status_code=409, detail="การซิงค์กำลังทำงานอยู่")
        
        # ค้นหาอุปกรณ์
        device_ip = find_zkteco_device()
        if not device_ip:
            raise HTTPException(status_code=500, detail="ไม่พบอุปกรณ์ ZKTeco")
        
        # ตั้งค่า credentials
        credentials = setup_credentials()
        if not credentials:
            raise HTTPException(status_code=500, detail="ไม่สามารถตั้งค่า Google Sheets credentials ได้")
        
        # ซิงค์ข้อมูล
        zk_sync = ZKTecoGoogleSheets(device_ip, DEVICE_PORT)
        success = zk_sync.sync_attendance(credentials)
        
        if success:
            return {
                "status": "success", 
                "message": "ซิงค์ข้อมูลสำเร็จ ✅",
                "device_ip": device_ip,
                "timestamp": datetime.now().isoformat()
            }
        else:
            raise HTTPException(status_code=500, detail="ซิงค์ข้อมูลล้มเหลว ❌")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Sync endpoint error: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"เกิดข้อผิดพลาด: {str(e)}")

@app.get("/status")
def get_status():
    """ตรวจสอบสถานะระบบ"""
    return {
        "status": "running",
        "sync_status": sync_status,
        "sync_running": sync_running,
        "last_sync": last_sync_time.isoformat() if last_sync_time else None,
        "device_ip": DEVICE_IP,
        "sync_interval": SYNC_INTERVAL_SECONDS,
        "timestamp": datetime.now().isoformat()
    }

@app.get("/test/device")
async def test_device_connection():
    """ทดสอบการเชื่อมต่อกับอุปกรณ์"""
    try:
        device_ip = find_zkteco_device()
        if not device_ip:
            raise HTTPException(status_code=500, detail="ไม่พบอุปกรณ์ ZKTeco")

        zk = ZK(device_ip, port=DEVICE_PORT, timeout=10)
        conn = zk.connect()
        if conn:
            try:
                info = {
                    "device_ip": device_ip,
                    "device_name": conn.get_device_name() or "Unknown",
                    "firmware": conn.get_firmware_version() or "Unknown",
                    "platform": conn.get_platform() or "Unknown",
                    "users_count": len(conn.get_users()) if conn.get_users() else 0,
                    "attendance_count": len(conn.get_attendance()) if conn.get_attendance() else 0
                }
                conn.disconnect()
                return {"status": "success", "device_info": info}
            except Exception as e:
                conn.disconnect()
                raise HTTPException(status_code=500, detail=f"ไม่สามารถดึงข้อมูลอุปกรณ์ได้: {str(e)}")
        else:
            raise HTTPException(status_code=500, detail="เชื่อมต่ออุปกรณ์ไม่สำเร็จ")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Device test error: {e}")
        raise HTTPException(status_code=500, detail=f"เกิดข้อผิดพลาด: {str(e)}")

@app.get("/test/sheets")
async def test_sheets_connection():
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
            "status": "success",
            "spreadsheet_name": SPREADSHEET_NAME,
            "worksheet_name": WORKSHEET_NAME,
            "total_rows": len(all_records),
            "headers": all_records[0] if all_records else []
        }
        
    except Exception as e:
        logger.error(f"Sheets test error: {e}")
        raise HTTPException(status_code=500, detail=f"เกิดข้อผิดพลาด: {str(e)}")

# === Health Check ===
@app.get("/health")
def health_check():
    """Health check endpoint สำหรับ Render"""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
