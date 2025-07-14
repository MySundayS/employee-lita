from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import logging
import os
import asyncio
import ipaddress
import traceback
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
        print("❌ ไม่พบ ZKTeco library")

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
CREDENTIALS_FILE = os.getenv("CREDENTIALS_FILE", "credentials.json")
SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME", "ZKTeco Attendance")
WORKSHEET_NAME = os.getenv("WORKSHEET_NAME", "Attendance")
SYNC_INTERVAL_SECONDS = int(os.getenv("SYNC_INTERVAL_SECONDS", 300))  # 5 นาที

# === ฟังก์ชันค้นหา IP อุปกรณ์ ZKTeco ===
def find_zkteco_device(target_ip=None, port=4370, timeout=3):
    """ค้นหาอุปกรณ์ ZKTeco"""
    if not PYZK_AVAILABLE:
        logger.error("ZKTeco library ไม่พร้อมใช้งาน")
        return None
        
    # ถ้าระบุ IP ไว้แล้ว ให้ใช้ IP นั้น
    if target_ip:
        logger.info(f"🔍 ทดสอบการเชื่อมต่อ IP: {target_ip}")
        try:
            zk = ZK(target_ip, port=port, timeout=timeout)
            conn = zk.connect()
            if conn:
                logger.info(f"✅ พบอุปกรณ์ ZKTeco ที่ IP: {target_ip}")
                conn.disconnect()
                return target_ip
        except Exception as e:
            logger.warning(f"ไม่สามารถเชื่อมต่อ {target_ip}: {e}")
    
    # ถ้าไม่ได้ระบุหรือเชื่อมต่อไม่ได้ ให้ค้นหาในเครือข่าย
    logger.info("🔍 กำลังค้นหาอุปกรณ์ ZKTeco ในเครือข่าย...")
    
    # ลอง IP ที่น่าจะเป็นไปได้
    base_ip = "192.168.1."
    common_ips = [2, 100, 200, 1, 254, 50, 101, 102, 103]
    
    for last_octet in common_ips:
        ip = f"{base_ip}{last_octet}"
        try:
            zk = ZK(ip, port=port, timeout=timeout)
            conn = zk.connect()
            if conn:
                logger.info(f"✅ พบอุปกรณ์ ZKTeco ที่ IP: {ip}")
                conn.disconnect()
                return ip
        except Exception:
            continue
    
    logger.warning("❌ ไม่พบอุปกรณ์ ZKTeco ในเครือข่าย")
    return None

# === คลาสหลัก ===
class ZKTecoGoogleSheets:
    def __init__(self, device_ip, device_port=4370):
        self.device_ip = device_ip
        self.device_port = device_port

    def setup_google_sheets(self, credentials_file, spreadsheet_name, worksheet_name):
        """ตั้งค่า Google Sheets"""
        try:
            creds = Credentials.from_service_account_file(credentials_file, scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"
            ])
            gc = gspread.authorize(creds)
            
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

    def get_users_info(self, conn):
        """ดึงข้อมูลผู้ใช้"""
        try:
            users = conn.get_users()
            user_dict = {}
            for user in users:
                user_dict[str(user.uid)] = {
                    'name': getattr(user, 'name', f'User_{user.uid}'),
                    'uid': user.uid
                }
            logger.info(f"ดึงข้อมูลผู้ใช้ได้ {len(user_dict)} คน")
            return user_dict
        except Exception as e:
            logger.warning(f"ไม่สามารถดึงข้อมูลผู้ใช้: {e}")
            return {}

    def run_sync(self, credentials_file, spreadsheet_name, worksheet_name):
        """ซิงค์ข้อมูลหลัก"""
        try:
            # ตั้งค่า Google Sheets
            worksheet = self.setup_google_sheets(credentials_file, spreadsheet_name, worksheet_name)
            if not worksheet:
                return False

            # เชื่อมต่อ ZKTeco
            logger.info(f"กำลังเชื่อมต่อ ZKTeco ที่ {self.device_ip}:{self.device_port}")
            zk = ZK(self.device_ip, port=self.device_port, timeout=30)
            conn = zk.connect()
            if not conn:
                raise Exception("เชื่อมต่อเครื่อง ZKTeco ไม่สำเร็จ")

            logger.info("✅ เชื่อมต่อ ZKTeco สำเร็จ")

            # ดึงข้อมูลผู้ใช้
            users_info = self.get_users_info(conn)

            # ดึงข้อมูลการเข้าออกงาน
            logger.info("กำลังดึงข้อมูลการเข้าออกงาน...")
            attendances = conn.get_attendance()
            conn.disconnect()

            if not attendances:
                logger.info("ไม่พบข้อมูลการเข้าออกงาน")
                return True

            # กรองข้อมูลปี 2025
            filtered_data = []
            for att in attendances:
                if att.timestamp and att.timestamp >= datetime(2025, 1, 1):
                    user_info = users_info.get(str(att.user_id), {})
                    user_name = user_info.get('name', f'User_{att.user_id}')
                    
                    row = [
                        f"{att.user_id}_{att.timestamp.strftime('%Y%m%d_%H%M%S')}",  # Unique ID
                        str(att.user_id),
                        user_name,
                        att.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                        getattr(att, 'status', 0),
                        int(att.punch),
                        att.timestamp.strftime('%Y-%m-%d'),
                        att.timestamp.strftime('%H:%M:%S'),
                        self.device_ip
                    ]
                    filtered_data.append(row)

            logger.info(f"กรองข้อมูลได้ {len(filtered_data)} รายการ (ปี 2025)")

            if not filtered_data:
                logger.info("ไม่พบข้อมูลปี 2025")
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
            for row in filtered_data:
                key = (row[1], row[6], row[7])  # user_id, date, time
                if key not in existing_set:
                    new_rows.append(row)

            if new_rows:
                # เพิ่มข้อมูลใหม่
                worksheet.append_rows(new_rows)
                logger.info(f"✅ เพิ่มข้อมูลใหม่ {len(new_rows)} รายการ")
            else:
                logger.info("ไม่มีข้อมูลใหม่ที่ต้องเพิ่ม")

            return True

        except Exception as e:
            logger.error(f"เกิดข้อผิดพลาดในการซิงค์: {e}")
            logger.error(traceback.format_exc())
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
            logger.info("🔄 เริ่มซิงค์อัตโนมัติ...")
            
            # ค้นหาอุปกรณ์
            device_ip = find_zkteco_device(DEVICE_IP)
            if not device_ip:
                sync_status = "Device not found"
                logger.warning("ไม่พบเครื่อง ZKTeco")
                sync_running = False
                await asyncio.sleep(SYNC_INTERVAL_SECONDS)
                continue

            # ซิงค์ข้อมูล
            zk_sync = ZKTecoGoogleSheets(device_ip, DEVICE_PORT)
            success = zk_sync.run_sync(CREDENTIALS_FILE, SPREADSHEET_NAME, WORKSHEET_NAME)
            
            if success:
                sync_status = "Success"
                sync_count += 1
                last_sync_time = datetime.now()
                logger.info("✅ ซิงค์อัตโนมัติสำเร็จ")
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
    logger.info("🚀 FastAPI เริ่มทำงาน")
    if PYZK_AVAILABLE:
        # เริ่ม background sync
        asyncio.create_task(background_sync_loop())
        logger.info("🔄 Background sync เริ่มทำงาน")
    else:
        logger.warning("⚠️ ZKTeco library ไม่พร้อมใช้งาน - ไม่สามารถทำ auto sync ได้")
    
    yield
    
    # Shutdown
    logger.info("🛑 FastAPI หยุดทำงาน")

# === FASTAPI APP ===
app = FastAPI(lifespan=lifespan, title="ZKTeco API", version="1.0.0")

# === ENDPOINT: Root ===
@app.get("/")
def read_root():
    return {
        "message": "ZKTeco FastAPI is running ✅",
        "pyzk_available": PYZK_AVAILABLE,
        "sync_status": sync_status,
        "sync_count": sync_count,
        "last_sync": last_sync_time.isoformat() if last_sync_time else None,
        "sync_running": sync_running,
        "device_ip": DEVICE_IP
    }

# === ENDPOINT: Manual sync ===
@app.get("/sync")
def sync_attendance():
    """ซิงค์ข้อมูลทันที"""
    if not PYZK_AVAILABLE:
        raise HTTPException(status_code=500, detail="ZKTeco library ไม่พร้อมใช้งาน")
        
    if sync_running:
        raise HTTPException(status_code=409, detail="การซิงค์กำลังทำงานอยู่")
    
    try:
        device_ip = find_zkteco_device(DEVICE_IP)
        if not device_ip:
            raise HTTPException(status_code=500, detail="ไม่พบอุปกรณ์ ZKTeco")
        
        zk_sync = ZKTecoGoogleSheets(device_ip, DEVICE_PORT)
        success = zk_sync.run_sync(CREDENTIALS_FILE, SPREADSHEET_NAME, WORKSHEET_NAME)
        
        if success:
            return {
                "status": "success", 
                "message": "ซิงค์ข้อมูลสำเร็จ ✅",
                "device_ip": device_ip,
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
        "pyzk_available": PYZK_AVAILABLE,
        "sync_status": sync_status,
        "sync_count": sync_count,
        "sync_running": sync_running,
        "last_sync": last_sync_time.isoformat() if last_sync_time else None,
        "device_ip": DEVICE_IP,
        "sync_interval": SYNC_INTERVAL_SECONDS,
        "timestamp": datetime.now().isoformat()
    }

# === ENDPOINT: Test device connection ===
@app.get("/test/device")
def test_device_connection():
    """ทดสอบการเชื่อมต่อกับอุปกรณ์"""
    if not PYZK_AVAILABLE:
        raise HTTPException(status_code=500, detail="ZKTeco library ไม่พร้อมใช้งาน")
        
    device_ip = find_zkteco_device(DEVICE_IP)
    if not device_ip:
        raise HTTPException(status_code=500, detail="ไม่พบอุปกรณ์ ZKTeco")

    try:
        zk = ZK(device_ip, port=DEVICE_PORT, timeout=30)
        conn = zk.connect()
        if conn:
            try:
                info = {
                    "device_ip": device_ip,
                    "device_name": getattr(conn, 'get_device_name', lambda: 'Unknown')(),
                    "firmware": getattr(conn, 'get_firmware_version', lambda: 'Unknown')(),
                    "platform": getattr(conn, 'get_platform', lambda: 'Unknown')(),
                    "connection_status": "Connected ✅"
                }
                
                # ลองดึงข้อมูลผู้ใช้และการเข้าออก
                try:
                    users = conn.get_users()
                    info["users_count"] = len(users) if users else 0
                except:
                    info["users_count"] = "Unable to fetch"
                
                try:
                    attendances = conn.get_attendance()
                    info["attendance_count"] = len(attendances) if attendances else 0
                except:
                    info["attendance_count"] = "Unable to fetch"
                
                conn.disconnect()
                return {"status": "success", "device_info": info}
                
            except Exception as e:
                conn.disconnect()
                raise HTTPException(status_code=500, detail=f"ไม่สามารถดึงข้อมูลอุปกรณ์: {str(e)}")
        else:
            raise HTTPException(status_code=500, detail="เชื่อมต่อไม่สำเร็จ")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"เกิดข้อผิดพลาด: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# === ENDPOINT: Test Google Sheets ===
@app.get("/test/sheets")
def test_sheets_connection():
    """ทดสอบการเชื่อมต่อกับ Google Sheets"""
    try:
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ])
        gc = gspread.authorize(creds)
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
            "connection_status": "Connected to Google Sheets ✅"
        }
        
    except Exception as e:
        logger.error(f"เกิดข้อผิดพลาด: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# === Health Check ===
@app.get("/health")
def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy", 
        "timestamp": datetime.now().isoformat(),
        "pyzk_available": PYZK_AVAILABLE
    }

if __name__ == "__main__":
    import uvicorn
    print("🚀 Starting ZKTeco FastAPI server...")
    uvicorn.run(app, host="0.0.0.0", port=8000)

    # ใน zkteco_fastapi.py เพิ่มส่วนนี้
CREDENTIALS_JSON = os.getenv("CREDENTIALS_JSON")
if CREDENTIALS_JSON:
    import tempfile
    import json
    # สร้างไฟล์ temp สำหรับ credentials
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        f.write(CREDENTIALS_JSON)
        CREDENTIALS_FILE = f.name
