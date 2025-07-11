import os
import json
from datetime import datetime
from zk import ZK
import gspread
from google.oauth2.service_account import Credentials
import time
import logging

# ตั้งค่า logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class ZKTecoGoogleSheets:
    def __init__(self, device_ip, device_port=4370):
        self.device_ip = device_ip
        self.device_port = device_port
        self.zk = ZK(device_ip, port=device_port, timeout=30)
        self.conn = None
        self.gc = None
        self.worksheet = None

    def setup_google_sheets(self, credentials_file, spreadsheet_name, worksheet_name="Attendance"):
        try:
            scope = [
                "https://spreadsheets.google.com/feeds",
                "https://www.googleapis.com/auth/drive"
            ]
            credentials = Credentials.from_service_account_file(credentials_file, scopes=scope)
            self.gc = gspread.authorize(credentials)

            try:
                spreadsheet = self.gc.open(spreadsheet_name)
            except gspread.SpreadsheetNotFound:
                spreadsheet = self.gc.create(spreadsheet_name)
                logger.info(f"Created new spreadsheet: {spreadsheet_name}")

            try:
                self.worksheet = spreadsheet.worksheet(worksheet_name)
            except gspread.WorksheetNotFound:
                self.worksheet = spreadsheet.add_worksheet(
                    title=worksheet_name, rows="1000", cols="20"
                )
                logger.info(f"Created new worksheet: {worksheet_name}")

            self.setup_headers()
            logger.info("Google Sheets setup completed successfully")
            return True

        except Exception as e:
            logger.error(f"Error setting up Google Sheets: {str(e)}")
            return False

    def setup_headers(self):
        headers = [
            "ID", "User ID", "Name", "Timestamp", "Status", 
            "Punch", "Date", "Time", "Device IP"
        ]
        try:
            existing_headers = self.worksheet.row_values(1)
            if not existing_headers or existing_headers != headers:
                self.worksheet.update('A1:I1', [headers])
                logger.info("Headers updated in Google Sheets")
        except Exception as e:
            logger.error(f"Error setting up headers: {str(e)}")

    def connect_device(self):
        try:
            logger.info(f"Connecting to ZKTeco device at {self.device_ip}:{self.device_port}")
            self.conn = self.zk.connect()
            if self.conn:
                logger.info("Connected to ZKTeco device successfully")
                device_info = {
                    "firmware_version": self.conn.get_firmware_version(),
                    "device_name": self.conn.get_device_name(),
                    "platform": self.conn.get_platform(),
                    "face_version": self.conn.get_face_version(),
                    "fp_version": self.conn.get_fp_version(),
                }
                logger.info(f"Device info: {device_info}")
                return True
            else:
                logger.error("Failed to connect to ZKTeco device")
                return False
        except Exception as e:
            logger.error(f"Error connecting to device: {str(e)}")
            return False

    def get_users(self):
        try:
            users = self.conn.get_users()
            user_dict = {}
            for user in users:
                user_dict[user.uid] = {
                    'uid': user.uid,
                    'name': user.name,
                    'privilege': user.privilege,
                    'password': user.password,
                    'group_id': user.group_id,
                    'user_id': user.user_id
                }
            logger.info(f"Retrieved {len(user_dict)} users from device")
            return user_dict
        except Exception as e:
            logger.error(f"Error getting users: {str(e)}")
            return {}

    def get_attendance_logs(self):
        try:
            attendances = self.conn.get_attendance()
            attendance_list = []
            for attendance in attendances:
                attendance_data = {
                    'user_id': attendance.user_id,
                    'timestamp': attendance.timestamp,
                    'status': attendance.status,
                    'punch': attendance.punch,
                    'uid': attendance.uid
                }
                attendance_list.append(attendance_data)
            logger.info(f"Retrieved {len(attendance_list)} attendance records")
            return attendance_list
        except Exception as e:
            logger.error(f"Error getting attendance logs: {str(e)}")
            return []

    def sync_to_google_sheets(self):
        try:
            users = self.get_users()
            attendances = self.get_attendance_logs()
            if not attendances:
                logger.warning("No attendance records found")
                return False

            existing_data = self.worksheet.get_all_records()
            existing_ids = set(str(row.get('ID', '')) for row in existing_data)

            new_rows = []
            update_count = 0

            for attendance in attendances:
                record_id = f"{attendance['user_id']}_{attendance['timestamp'].strftime('%Y%m%d_%H%M%S')}"
                if record_id in existing_ids:
                    continue

                user_info = users.get(attendance['user_id'], {})
                user_name = user_info.get('name', 'Unknown')

                formatted_data = [
                    record_id,
                    attendance['user_id'],
                    user_name,
                    attendance['timestamp'].strftime('%Y-%m-%d %H:%M:%S'),
                    attendance['status'],
                    attendance['punch'],
                    attendance['timestamp'].strftime('%Y-%m-%d'),
                    attendance['timestamp'].strftime('%H:%M:%S'),
                    self.device_ip
                ]

                new_rows.append(formatted_data)
                update_count += 1

            if new_rows:
                current_row_count = self.worksheet.row_count
                needed_row_count = len(existing_data) + len(new_rows) + 1

                if needed_row_count > current_row_count:
                    extra_rows = needed_row_count - current_row_count
                    self.worksheet.add_rows(extra_rows)
                    logger.info(f"เพิ่ม {extra_rows} แถวใน Google Sheets อัตโนมัติ")

                last_row = len(existing_data) + 2
                range_name = f'A{last_row}:I{last_row + len(new_rows) - 1}'
                self.worksheet.update(range_name=range_name, values=new_rows)
                logger.info(f"Updated {update_count} new records to Google Sheets")
                return True
            else:
                logger.info("No new records to update")
                return True

        except Exception as e:
            logger.error(f"Error syncing to Google Sheets: {str(e)}")
            return False

    def disconnect_device(self):
        try:
            if self.conn:
                self.conn.disconnect()
                logger.info("Disconnected from ZKTeco device")
        except Exception as e:
            logger.error(f"Error disconnecting: {str(e)}")

    def run_sync(self, credentials_file, spreadsheet_name, worksheet_name="Attendance"):
        try:
            if not self.setup_google_sheets(credentials_file, spreadsheet_name, worksheet_name):
                return False
            if not self.connect_device():
                return False
            result = self.sync_to_google_sheets()
            self.disconnect_device()
            return result
        except Exception as e:
            logger.error(f"Error in run_sync: {str(e)}")
            self.disconnect_device()
            return False

def main():
    DEVICE_IP = "192.168.1.3"
    DEVICE_PORT = 4370
    CREDENTIALS_FILE = "C:/Users/Arsuae01/Desktop/Employee/credentials.json"
    SPREADSHEET_NAME = "ZKTeco Attendance"
    WORKSHEET_NAME = "Attendance"

    zk_sync = ZKTecoGoogleSheets(DEVICE_IP, DEVICE_PORT)
    success = zk_sync.run_sync(CREDENTIALS_FILE, SPREADSHEET_NAME, WORKSHEET_NAME)

    if success:
        print("✅ Data sync completed successfully!")
    else:
        print("❌ Data sync failed!")

def run_continuous_sync():
    DEVICE_IP = "192.168.1.3"
    DEVICE_PORT = 4370
    CREDENTIALS_FILE = "C:/Users/Arsuae01/Desktop/Employee/credentials.json"
    SPREADSHEET_NAME = "ZKTeco Attendance"
    WORKSHEET_NAME = "Attendance"
    SYNC_INTERVAL = 300

    zk_sync = ZKTecoGoogleSheets(DEVICE_IP, DEVICE_PORT)

    logger.info("Starting continuous sync...")

    while True:
        try:
            logger.info("Starting sync cycle...")
            success = zk_sync.run_sync(CREDENTIALS_FILE, SPREADSHEET_NAME, WORKSHEET_NAME)

            if success:
                logger.info("✅ Sync cycle completed successfully")
            else:
                logger.error("❌ Sync cycle failed")

            logger.info(f"Waiting {SYNC_INTERVAL} seconds before next sync...")
            time.sleep(SYNC_INTERVAL)

        except KeyboardInterrupt:
            logger.info("Stopping continuous sync...")
            break
        except Exception as e:
            logger.error(f"Error in continuous sync: {str(e)}")
            time.sleep(60)

def test_connection():
    from zk import ZK
    zk = ZK("192.168.1.3", port=4370, timeout=30)
    try:
        conn = zk.connect()
        if conn:
            print("✅ เชื่อมต่อเครื่อง ZKTeco สำเร็จ!")
            print(f"Device info: {conn.get_device_name()}")
            print(f"Firmware version: {conn.get_firmware_version()}")
            conn.disconnect()
            return True
        else:
            print("❌ ไม่สามารถเชื่อมต่อเครื่อง ZKTeco")
            return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

if __name__ == "__main__":
    print("=== ทดสอบการเชื่อมต่อ ===")
    test_connection()
    print("\n=== เริ่มซิงค์ข้อมูล ===")
    main()
    # run_continuous_sync()  # ใช้หากต้องการให้ซิงค์ต่อเนื่อง
