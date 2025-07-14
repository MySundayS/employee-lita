from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import logging
import os
import asyncio
import ipaddress
from zk import ZK
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import time

# === Logging Setup ===
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# === Config ===
DEVICE_PORT = int(os.getenv("DEVICE_PORT", 4370))
CREDENTIALS_FILE = os.getenv("CREDENTIALS_FILE", "credentials.json")
SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME", "ZKTeco Attendance")
WORKSHEET_NAME = os.getenv("WORKSHEET_NAME", "Attendance")
SYNC_INTERVAL_SECONDS = int(os.getenv("SYNC_INTERVAL_SECONDS", 5 * 60))  # 5 นาที
RETRY_ATTEMPTS = 3
RETRY_DELAY = 10  # วินาที

# === ฟังก์ชันค้นหา IP อุปกรณ์ ZKTeco ===
def find_zkteco_device(subnet="192.168.1.2", port=4370):
    logger.info("🔍 กำลังค้นหาอุปกรณ์ ZKTeco ในเครือข่าย...")
    for ip in ipaddress.IPv4Network(subnet):
        ip = str(ip)
        for attempt in range(RETRY_ATTEMPTS):
            try:
                zk = ZK(ip, port=port, timeout=2)
                conn = zk.connect()
                if conn:
                    logger.info(f"✅ พบอุปกรณ์ ZKTeco ที่ IP: {ip}")
                    conn.disconnect()
                    return ip
                break
            except Exception as e:
                logger.warning(f"ลองครั้งที่ {attempt+1} ล้มเหลวสำหรับ IP {ip}: {e}")
                time.sleep(RETRY_DELAY)
    logger.error("❌ ไม่พบอุปกรณ์ ZKTeco ในเครือข่าย")
    return None

# === คลาสหลัก ===
class ZKTecoGoogleSheets:
    def __init__(self, device_ip, device_port=4370, late_threshold_hour=8, late_threshold_minute=0):
        self.device_ip = device_ip
        self.device_port = device_port
        self.late_threshold_hour = late_threshold_hour
        self.late_threshold_minute = late_threshold_minute

    def run_sync(self, credentials_file, spreadsheet_name, worksheet_name):
        for attempt in range(RETRY_ATTEMPTS):
            try:
                creds = Credentials.from_service_account_file(credentials_file, scopes=[
                    "https://www.googleapis.com/auth/spreadsheets",
                    "https://www.googleapis.com/auth/drive"
                ])
                gc = gspread.authorize(creds)
                sh = gc.open(spreadsheet_name)
                worksheet = sh.worksheet(worksheet_name)

                if not self.device_ip:
                    raise Exception("ไม่พบ IP อุปกรณ์ ZKTeco")

                zk = ZK(self.device_ip, port=self.device_port, timeout=30)
                conn = zk.connect()
                if not conn:
                    raise Exception("เชื่อมต่อเครื่อง ZKTeco ไม่สำเร็จ")

                attendances = conn.get_attendance()
                data = []
                for att in attendances:
                    if att.timestamp and att.timestamp >= datetime(2025, 1, 1):
                        row = [
                            "", str(att.user_id), "", att.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                            "", int(att.punch), att.timestamp.strftime('%Y-%m-%d'),
                            att.timestamp.strftime('%H:%M:%S'), self.device_ip
                        ]
                        data.append(row)

                existing_data = worksheet.get_all_values()
                existing_set = set((r[1], r[6], r[7]) for r in existing_data[1:])

                new_rows = [row for row in data if (row[1], row[6], row[7]) not in existing_set]
                if new_rows:
                    worksheet.append_rows(new_rows)
                    logger.info(f"✅ เพิ่ม {len(new_rows)} รายการใหม่ลง Google Sheets")

                conn.disconnect()
                return True

            except Exception as e:
                logger.error(f"[❌ SYNC ERROR] ลองครั้งที่ {attempt+1}: {e}")
                if attempt < RETRY_ATTEMPTS - 1:
                    time.sleep(RETRY_DELAY)
                continue
        return False

# === FastAPI App ===
app = FastAPI()

# === Background Sync Task ===
async def background_sync_loop():
    while True:
        try:
            logger.info("[⏱️ SYNC] เริ่มซิงค์อัตโนมัติ")
            device_ip = find_zkteco_device()
            if not device_ip:
                logger.warning("[⚠️ SYNC] ไม่พบเครื่อง ZKTeco")
                await asyncio.sleep(SYNC_INTERVAL_SECONDS)
                continue

            zk_sync = ZKTecoGoogleSheets(
                device_ip, DEVICE_PORT, late_threshold_hour=8, late_threshold_minute=0
            )
            success = zk_sync.run_sync(CREDENTIALS_FILE, SPREADSHEET_NAME, WORKSHEET_NAME)
            if success:
                logger.info("[✅ SYNC] ซิงค์ข้อมูลอัตโนมัติสำเร็จ")
            else:
                logger.warning("[⚠️ SYNC] ซิงค์อัตโนมัติล้มเหลว")
        except Exception as e:
            logger.error(f"[❌ SYNC ERROR] {e}")
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)

@app.on_event("startup")
async def startup_event():
    logger.info("🚀 FastAPI เริ่มทำงาน")
    asyncio.create_task(background_sync_loop())

# === Endpoints ===
@app.get("/")
def read_root():
    return {"message": "ZKTeco FastAPI is running ✅"}

@app.get("/sync")
def sync_attendance():
    try:
        device_ip = find_zkteco_device()
        if not device_ip:
            raise HTTPException(status_code=500, detail="ไม่พบอุปกรณ์ ZKTeco")
        
        zk_sync = ZKTecoGoogleSheets(device_ip, DEVICE_PORT, late_threshold_hour=8, late_threshold_minute=0)
        success = zk_sync.run_sync(CREDENTIALS_FILE, SPREADSHEET_NAME, WORKSHEET_NAME)
        if success:
            return {"status": "success", "message": "ซิงค์ข้อมูลสำเร็จ ✅"}
        else:
            raise HTTPException(status_code=500, detail="ซิงค์ไม่สำเร็จ ❌")
    except Exception as e:
        logger.error(f"เกิดข้อผิดพลาด: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/status")
def get_status():
    try:
        logger.info("[📊 STATUS] ตรวจสอบสถานะระบบ")
        device_ip = find_zkteco_device()
        if not device_ip:
            raise HTTPException(status_code=500, detail="ไม่พบอุปกรณ์ ZKTeco")
        
        zk_sync = ZKTecoGoogleSheets(device_ip, DEVICE_PORT, late_threshold_hour=8, late_threshold_minute=0)
        success = zk_sync.run_sync(CREDENTIALS_FILE, SPREADSHEET_NAME, WORKSHEET_NAME)
        if success:
            return {"status": "success", "message": "ระบบทำงานปกติ ✅", "last_sync": datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        else:
            raise HTTPException(status_code=500, detail="ซิงค์ไม่สำเร็จ ❌")
    except Exception as e:
        logger.error(f"[❌ STATUS ERROR] {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/test/device")
def test_device_connection():
    device_ip = find_zkteco_device()
    if not device_ip:
        raise HTTPException(status_code=500, detail="ไม่พบอุปกรณ์ ZKTeco")

    zk = ZK(device_ip, port=DEVICE_PORT, timeout=30)
    try:
        conn = zk.connect()
        if conn:
            info = {
                "device_name": conn.get_device_name(),
                "firmware": conn.get_firmware_version(),
                "platform": conn.get_platform(),
            }
            conn.disconnect()
            return {"status": "success", "device_info": info}


        async def background_sync_loop():
    while True:
        try:
            logger.info("[⏱️ SYNC] เริ่มซิงค์อัตโนมัติ")
            device_ip = find_zkteco_device()
            if not device_ip:
                logger.warning("[⚠️ SYNC] ไม่พบเครื่อง ZKTeco")
                await asyncio.sleep(SYNC_INTERVAL_SECONDS)
                continue
            zk_sync = ZKTecoGoogleSheets(device_ip, DEVICE_PORT, late_threshold_hour=8, late_threshold_minute=0)
            success = zk_sync.run_sync(CREDENTIALS_FILE, SPREADSHEET_NAME, WORKSHEET_NAME)
            if success:
                logger.info("[✅ SYNC] ซิงค์ข้อมูลอัตโนมัติสำเร็จ")
            else:
                logger.warning("[⚠️ SYNC] ซิงค์อัตโนมัติล้มเหลว")
        except Exception as e:
            logger.error(f"[❌ SYNC ERROR] {e}")
            await asyncio.sleep(SYNC_INTERVAL_SECONDS)  # รอก่อนรอบถัดไปแม้มี error
        else:
            raise HTTPException(status_code=500, detail="เชื่อมต่อไม่สำเร็จ")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
