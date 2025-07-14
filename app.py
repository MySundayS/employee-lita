import os
import json
import asyncio
import logging
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
import gspread
from google.oauth2.service_account import Credentials

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global variables
sync_running = False
last_sync_time = None
sync_status = "Not started"
sync_count = 0

# Configuration
DEVICE_IP = os.getenv("ZKTECO_IP", "192.168.1.2")
DEVICE_PORT = int(os.getenv("DEVICE_PORT", 4370))
SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME", "ZKTeco Attendance")
WORKSHEET_NAME = os.getenv("WORKSHEET_NAME", "Attendance")
SYNC_INTERVAL_SECONDS = int(os.getenv("SYNC_INTERVAL_SECONDS", 300))

def setup_credentials():
    """Setup Google Sheets credentials"""
    credentials_json = os.getenv("CREDENTIALS_JSON")
    
    if credentials_json:
        try:
            creds_dict = json.loads(credentials_json)
            return Credentials.from_service_account_info(creds_dict, scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"
            ])
        except Exception as e:
            logger.error(f"Error parsing credentials: {e}")
            return None
    else:
        logger.error("No credentials found")
        return None

def get_demo_data():
    """Generate demo attendance data"""
    now = datetime.now()
    demo_data = []
    
    # Generate data for last 3 days
    for days_ago in range(3):
        date = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_ago)
        
        # 5 employees
        for user_id in ['001', '002', '003', '004', '005']:
            # Morning check-in
            checkin_time = date.replace(
                hour=8 + (int(user_id) % 2),
                minute=30 + (int(user_id) * 5) % 30
            )
            demo_data.append({
                'user_id': user_id,
                'name': f'Employee {user_id}',
                'timestamp': checkin_time,
                'status': 1,
                'punch': 1
            })
            
            # Lunch break - out
            lunch_out = date.replace(hour=12, minute=0)
            demo_data.append({
                'user_id': user_id,
                'name': f'Employee {user_id}',
                'timestamp': lunch_out,
                'status': 0,
                'punch': 0
            })
            
            # Lunch break - back
            lunch_in = date.replace(hour=13, minute=0)
            demo_data.append({
                'user_id': user_id,
                'name': f'Employee {user_id}',
                'timestamp': lunch_in,
                'status': 1,
                'punch': 1
            })
            
            # Evening check-out
            checkout_time = date.replace(
                hour=17 + (int(user_id) % 2),
                minute=30 - (int(user_id) * 3) % 30
            )
            demo_data.append({
                'user_id': user_id,
                'name': f'Employee {user_id}',
                'timestamp': checkout_time,
                'status': 0,
                'punch': 0
            })
    
    return demo_data

def sync_attendance():
    """Sync attendance data to Google Sheets"""
    try:
        credentials = setup_credentials()
        if not credentials:
            return False

        # Setup Google Sheets
        gc = gspread.authorize(credentials)
        
        try:
            sh = gc.open(SPREADSHEET_NAME)
        except gspread.SpreadsheetNotFound:
            sh = gc.create(SPREADSHEET_NAME)
            logger.info(f"Created new spreadsheet: {SPREADSHEET_NAME}")
        
        try:
            worksheet = sh.worksheet(WORKSHEET_NAME)
        except gspread.WorksheetNotFound:
            worksheet = sh.add_worksheet(title=WORKSHEET_NAME, rows="1000", cols="20")
            logger.info(f"Created new worksheet: {WORKSHEET_NAME}")
        
        # Setup headers
        headers = ["ID", "User ID", "Name", "Timestamp", "Status", "Punch", "Date", "Time", "Device IP"]
        try:
            existing_headers = worksheet.row_values(1)
            if not existing_headers or existing_headers != headers:
                worksheet.update('A1:I1', [headers])
                logger.info("Updated headers")
        except Exception as e:
            logger.warning(f"Could not update headers: {e}")

        # Get demo data (Cloud mode)
        logger.info("🌐 Cloud Mode: Using demo data")
        demo_data = get_demo_data()

        if not demo_data:
            logger.info("No demo data")
            return True

        # Check existing data
        try:
            existing_data = worksheet.get_all_values()
            existing_set = set()
            for row in existing_data[1:]:  # Skip header
                if len(row) >= 8:
                    existing_set.add((row[1], row[6], row[7]))  # user_id, date, time
        except Exception as e:
            logger.warning(f"Could not get existing data: {e}")
            existing_set = set()

        # Filter new data
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
                    DEVICE_IP + " (Cloud Demo)"
                ]
                new_rows.append(row)

        if new_rows:
            worksheet.append_rows(new_rows)
            logger.info(f"✅ Added {len(new_rows)} new records (Cloud Demo)")
        else:
            logger.info("No new data to add")

        return True

    except Exception as e:
        logger.error(f"Sync error: {e}")
        return False

async def background_sync_loop():
    """Background sync loop"""
    global sync_running, last_sync_time, sync_status, sync_count
    
    while True:
        try:
            if sync_running:
                await asyncio.sleep(30)
                continue
                
            sync_running = True
            sync_status = "Running"
            logger.info("🔄 Starting auto sync (Cloud Mode)...")
            
            success = sync_attendance()
            
            if success:
                sync_status = "Success"
                sync_count += 1
                last_sync_time = datetime.now()
                logger.info("✅ Auto sync successful (Cloud Demo)")
            else:
                sync_status = "Failed"
                logger.warning("❌ Auto sync failed")
                
        except Exception as e:
            sync_status = f"Error: {str(e)}"
            logger.error(f"Auto sync error: {e}")
        finally:
            sync_running = False
            await asyncio.sleep(SYNC_INTERVAL_SECONDS)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("🚀 FastAPI starting (Cloud Mode)")
    
    # Start background sync
    asyncio.create_task(background_sync_loop())
    logger.info("🔄 Background sync started")
    
    yield
    
    # Shutdown
    logger.info("🛑 FastAPI stopping")

# FastAPI app
app = FastAPI(lifespan=lifespan, title="ZKTeco Cloud API", version="1.0.0")

@app.get("/")
def read_root():
    return {
        "message": "ZKTeco FastAPI is running ✅ (Cloud Mode)",
        "mode": "Cloud Demo",
        "sync_status": sync_status,
        "sync_count": sync_count,
        "last_sync": last_sync_time.isoformat() if last_sync_time else None,
        "sync_running": sync_running,
        "device_ip": DEVICE_IP,
        "environment": "Cloud"
    }

@app.get("/sync")
def sync_now():
    """Manual sync"""
    if sync_running:
        raise HTTPException(status_code=409, detail="Sync is already running")
    
    try:
        success = sync_attendance()
        
        if success:
            return {
                "status": "success", 
                "message": "Sync successful ✅ (Cloud Demo)",
                "mode": "Cloud Demo",
                "device_ip": DEVICE_IP,
                "timestamp": datetime.now().isoformat()
            }
        else:
            raise HTTPException(status_code=500, detail="Sync failed ❌")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Sync error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/status")
def get_status():
    """System status"""
    return {
        "status": "running",
        "mode": "Cloud Demo",
        "sync_status": sync_status,
        "sync_count": sync_count,
        "sync_running": sync_running,
        "last_sync": last_sync_time.isoformat() if last_sync_time else None,
        "device_ip": DEVICE_IP,
        "sync_interval": SYNC_INTERVAL_SECONDS,
        "timestamp": datetime.now().isoformat(),
        "environment": "Cloud"
    }

@app.get("/test/sheets")
def test_sheets():
    """Test Google Sheets connection"""
    try:
        credentials = setup_credentials()
        if not credentials:
            raise HTTPException(status_code=500, detail="Could not setup credentials")
        
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
            "mode": "Cloud"
        }
        
    except Exception as e:
        logger.error(f"Sheets test error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
def health_check():
    """Health check for Render"""
    return {
        "status": "healthy", 
        "timestamp": datetime.now().isoformat(),
        "mode": "Cloud Demo"
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
