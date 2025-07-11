ZKTeco Attendance Dashboard
Dashboard แสดงข้อมูลการลงเวลาทำงานจากเครื่อง ZKTeco ผ่าน Streamlit

🚀 Features
Real-time Dashboard: แสดงข้อมูลการลงเวลาแบบ real-time
Employee Summary: สรุปจำนวนพนักงานที่มาทำงาน/ไม่มาทำงาน
Attendance Analytics: กราฟแสดงแนวโน้มการมาทำงาน
Time Distribution: การกระจายของเวลาเข้างาน
Employee Details: รายละเอียดการลงเวลาของพนักงานแต่ละคน
Auto Refresh: รีเฟรชข้อมูลอัตโนมัติทุก 5 นาที
📋 Requirements
Python 3.8+
ZKTeco device connected to network
Google Service Account with access to Google Sheets
Libraries ตาม requirements.txt
🛠️ Installation
Clone repository:
bash
git clone https://github.com/yourusername/zkteco-attendance-dashboard.git
cd zkteco-attendance-dashboard
Install dependencies:
bash
pip install -r requirements.txt
เตรียม Google Service Account:
สร้าง Service Account ใน Google Cloud Console
Download credentials.json
แชร์ Google Sheets ให้กับ Service Account email
🔧 Configuration
1. ZKTeco Device Settings
Device IP: IP address ของเครื่อง ZKTeco (default: 192.168.1.3)
Device Port: Port ของเครื่อง (default: 4370)
2. Google Sheets Settings
Spreadsheet Name: ชื่อ Google Sheets (default: "ZKTeco Attendance")
Worksheet Name: ชื่อ Sheet (default: "Attendance")
3. Credentials
Upload ไฟล์ credentials.json ผ่าน sidebar หรือวางไว้ในโฟลเดอร์เดียวกับ app
🚀 Running the Application
Local Development
bash
streamlit run streamlit_app.py
Deploy to Streamlit Cloud
Push code ไปที่ GitHub
ไปที่ share.streamlit.io
Deploy app จาก GitHub repository
Set secrets ใน Streamlit Cloud:
ไปที่ App settings > Secrets
เพิ่ม credentials.json content
Deploy to Docker
dockerfile
FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

EXPOSE 8501

CMD ["streamlit", "run", "streamlit_app.py", "--server.port=8501", "--server.address=0.0.0.0"]
Build และ run:

bash
docker build -t zkteco-dashboard .
docker run -p 8501:8501 zkteco-dashboard
📊 Dashboard Components
1. Summary Cards
Total Employees: จำนวนพนักงานทั้งหมด
Checked In Today: จำนวนที่ลงเวลาวันนี้
Not Checked In: จำนวนที่ยังไม่ลงเวลา
Attendance Rate: เปอร์เซ็นต์การมาทำงาน
2. Charts
Attendance Trend: กราฟเส้นแสดงแนวโน้ม 7 วันย้อนหลัง
Status Pie Chart: แผนภูมิวงกลมแสดงสถานะการมาทำงาน
Time Distribution: กราฟแท่งแสดงการกระจายเวลาเข้างาน
3. Tables
Employee Details: ตารางแสดงเวลาเข้า-ออกและชั่วโมงทำงาน
Recent Check-ins: แสดงการลงเวลาล่าสุด 10 รายการ
🔄 Sync Script (Optional)
ใช้สำหรับซิงค์ข้อมูลจากเครื่อง ZKTeco ไป Google Sheets อัตโนมัติ:

python
python zkteco_sync.py
หรือรันแบบ continuous sync:

python
python zkteco_sync.py --continuous
📱 Screenshots
Show Image

🐛 Troubleshooting
ปัญหาที่พบบ่อย
ไม่สามารถเชื่อมต่อเครื่อง ZKTeco
ตรวจสอบ IP address และ port
ตรวจสอบ firewall
ตรวจสอบว่าเครื่องอยู่ใน network เดียวกัน
Google Sheets API Error
ตรวจสอบ credentials.json
ตรวจสอบว่าแชร์ Sheet ให้ Service Account แล้ว
ตรวจสอบ quota limits
No data showing
ตรวจสอบชื่อ Spreadsheet และ Worksheet
ตรวจสอบว่ามีข้อมูลใน Google Sheets
📝 Data Structure
Google Sheets columns:

ID: Unique record ID
User ID: Employee ID
Name: Employee name
Timestamp: Full timestamp
Status: Check-in status
Punch: Punch type
Date: Date only
Time: Time only
Device IP: Source device IP
🤝 Contributing
Fork the repository
Create feature branch (git checkout -b feature/AmazingFeature)
Commit changes (git commit -m 'Add some AmazingFeature')
Push to branch (git push origin feature/AmazingFeature)
Open Pull Request
📄 License
This project is licensed under the MIT License - see the LICENSE file for details.

👥 Contact
Your Name - @yourusername
Project Link: https://github.com/yourusername/zkteco-attendance-dashboard
🙏 Acknowledgments
pyzk - ZKTeco Python library
Streamlit - Web app framework
Plotly - Interactive charts
