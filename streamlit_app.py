import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
import gspread
from google.oauth2.service_account import Credentials
import json
from zk import ZK
import time

# Page config
st.set_page_config(
    page_title="ZKTeco Attendance Dashboard",
    page_icon="🕐",
    layout="wide"
)

# Custom CSS
st.markdown("""
<style>
    .metric-card {
        background-color: #f0f2f6;
        padding: 20px;
        border-radius: 10px;
        text-align: center;
    }
    .big-number {
        font-size: 48px;
        font-weight: bold;
        color: #1f77b4;
    }
    .status-online {
        color: #00cc00;
        font-weight: bold;
    }
    .status-offline {
        color: #cc0000;
        font-weight: bold;
    }
</style>
""", unsafe_allow_html=True)

class AttendanceDashboard:
    def __init__(self):
        self.setup_session_state()
        
    def setup_session_state(self):
        if 'last_update' not in st.session_state:
            st.session_state.last_update = None
        if 'auto_refresh' not in st.session_state:
            st.session_state.auto_refresh = False
            
    @st.cache_data(ttl=300)
    def load_google_sheets_data(_self, credentials_dict, spreadsheet_name):
        """Load data from Google Sheets"""
        try:
            scope = [
                "https://spreadsheets.google.com/feeds",
                "https://www.googleapis.com/auth/drive"
            ]
            credentials = Credentials.from_service_account_info(credentials_dict, scopes=scope)
            gc = gspread.authorize(credentials)
            spreadsheet = gc.open(spreadsheet_name)
            worksheet = spreadsheet.worksheet("Attendance")
            
            data = worksheet.get_all_records()
            df = pd.DataFrame(data)
            
            # Convert timestamp to datetime
            if not df.empty and 'Timestamp' in df.columns:
                df['Timestamp'] = pd.to_datetime(df['Timestamp'])
                df['Date'] = pd.to_datetime(df['Date'])
                df['Time'] = pd.to_datetime(df['Time'], format='%H:%M:%S').dt.time
                
            return df
        except Exception as e:
            st.error(f"Error loading data: {str(e)}")
            return pd.DataFrame()
    
    def check_device_status(self, device_ip, device_port=4370):
        """Check if ZKTeco device is online"""
        try:
            zk = ZK(device_ip, port=device_port, timeout=5)
            conn = zk.connect()
            if conn:
                device_info = {
                    'status': 'Online',
                    'name': conn.get_device_name(),
                    'firmware': conn.get_firmware_version()
                }
                conn.disconnect()
                return device_info
            return {'status': 'Offline'}
        except:
            return {'status': 'Offline'}
    
    def get_today_summary(self, df):
        """Get today's attendance summary"""
        today = pd.Timestamp.now().date()
        today_df = df[df['Date'].dt.date == today] if not df.empty else pd.DataFrame()
        
        total_employees = df['User ID'].nunique() if not df.empty else 0
        checked_in_today = today_df['User ID'].nunique() if not today_df.empty else 0
        
        # Get first and last check-in times for each employee today
        if not today_df.empty:
            employee_times = today_df.groupby(['User ID', 'Name'])['Timestamp'].agg(['min', 'max'])
            employee_times = employee_times.reset_index()
            employee_times.columns = ['User ID', 'Name', 'First Check-in', 'Last Check-out']
            employee_times['Working Hours'] = (
                employee_times['Last Check-out'] - employee_times['First Check-in']
            ).dt.total_seconds() / 3600
        else:
            employee_times = pd.DataFrame()
        
        return {
            'total_employees': total_employees,
            'checked_in_today': checked_in_today,
            'not_checked_in': total_employees - checked_in_today,
            'attendance_rate': (checked_in_today / total_employees * 100) if total_employees > 0 else 0,
            'employee_times': employee_times
        }
    
    def create_attendance_chart(self, df, days=7):
        """Create attendance trend chart"""
        end_date = pd.Timestamp.now().date()
        start_date = end_date - timedelta(days=days-1)
        
        date_range = pd.date_range(start=start_date, end=end_date)
        daily_attendance = []
        
        for date in date_range:
            day_df = df[df['Date'].dt.date == date.date()] if not df.empty else pd.DataFrame()
            count = day_df['User ID'].nunique() if not day_df.empty else 0
            daily_attendance.append({
                'Date': date.strftime('%Y-%m-%d'),
                'Employees': count
            })
        
        trend_df = pd.DataFrame(daily_attendance)
        
        fig = px.line(trend_df, x='Date', y='Employees', 
                     title=f'Attendance Trend (Last {days} Days)',
                     markers=True)
        fig.update_layout(
            xaxis_title="Date",
            yaxis_title="Number of Employees",
            hovermode='x unified'
        )
        
        return fig
    
    def create_punch_time_distribution(self, df):
        """Create punch time distribution chart"""
        today = pd.Timestamp.now().date()
        today_df = df[df['Date'].dt.date == today] if not df.empty else pd.DataFrame()
        
        if today_df.empty:
            return None
            
        today_df['Hour'] = pd.to_datetime(today_df['Time'], format='%H:%M:%S').dt.hour
        hourly_dist = today_df.groupby('Hour').size().reset_index(name='Count')
        
        fig = px.bar(hourly_dist, x='Hour', y='Count',
                    title="Today's Check-in Distribution by Hour")
        fig.update_xaxis(title="Hour of Day", dtick=1)
        fig.update_yaxis(title="Number of Check-ins")
        
        return fig
    
    def create_employee_status_pie(self, summary):
        """Create employee status pie chart"""
        data = {
            'Status': ['Checked In', 'Not Checked In'],
            'Count': [summary['checked_in_today'], summary['not_checked_in']]
        }
        
        fig = px.pie(pd.DataFrame(data), values='Count', names='Status',
                    title="Today's Attendance Status",
                    color_discrete_map={'Checked In': '#00cc00', 'Not Checked In': '#ff6666'})
        
        return fig

def main():
    st.title("🕐 ZKTeco Attendance Dashboard")
    
    dashboard = AttendanceDashboard()
    
    # Sidebar configuration
    with st.sidebar:
        st.header("⚙️ Configuration")
        
        # Device settings
        device_ip = st.text_input("Device IP", value="192.168.1.3")
        device_port = st.number_input("Device Port", value=4370, min_value=1, max_value=65535)
        
        # Google Sheets settings
        spreadsheet_name = st.text_input("Spreadsheet Name", value="ZKTeco Attendance")
        
        # Credentials upload
        st.subheader("📄 Google Credentials")
        uploaded_file = st.file_uploader("Upload credentials.json", type=['json'])
        
        # Auto refresh
        auto_refresh = st.checkbox("Auto Refresh (5 min)", value=st.session_state.auto_refresh)
        st.session_state.auto_refresh = auto_refresh
        
        if st.button("🔄 Refresh Now"):
            st.cache_data.clear()
            st.rerun()
    
    # Load credentials
    credentials_dict = None
    if uploaded_file is not None:
        credentials_dict = json.load(uploaded_file)
    else:
        # Try to use Streamlit secrets first
        try:
            credentials_dict = dict(st.secrets["gcp_service_account"])
        except:
            # Use default credentials if available
            try:
                with open("credentials.json", 'r') as f:
                    credentials_dict = json.load(f)
            except:
                st.warning("Please upload credentials.json file in the sidebar or configure secrets")
                return
    
    # Auto refresh logic
    if st.session_state.auto_refresh:
        time.sleep(300)  # 5 minutes
        st.rerun()
    
    # Device status
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        device_info = dashboard.check_device_status(device_ip, device_port)
        if device_info['status'] == 'Online':
            st.success(f"🟢 Device Status: **{device_info['status']}** | "
                      f"Name: {device_info.get('name', 'N/A')} | "
                      f"Firmware: {device_info.get('firmware', 'N/A')}")
        else:
            st.error("🔴 Device Status: **Offline**")
    
    # Load data
    df = dashboard.load_google_sheets_data(credentials_dict, spreadsheet_name)
    
    if df.empty:
        st.info("No attendance data available")
        return
    
    # Get summary
    summary = dashboard.get_today_summary(df)
    
    # Display metrics
    st.markdown("---")
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.markdown('<div class="metric-card">', unsafe_allow_html=True)
        st.markdown(f'<div class="big-number">{summary["total_employees"]}</div>', unsafe_allow_html=True)
        st.markdown("**Total Employees**", unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)
    
    with col2:
        st.markdown('<div class="metric-card">', unsafe_allow_html=True)
        st.markdown(f'<div class="big-number" style="color: #00cc00">{summary["checked_in_today"]}</div>', 
                   unsafe_allow_html=True)
        st.markdown("**Checked In Today**", unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)
    
    with col3:
        st.markdown('<div class="metric-card">', unsafe_allow_html=True)
        st.markdown(f'<div class="big-number" style="color: #ff6666">{summary["not_checked_in"]}</div>', 
                   unsafe_allow_html=True)
        st.markdown("**Not Checked In**", unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)
    
    with col4:
        st.markdown('<div class="metric-card">', unsafe_allow_html=True)
        st.markdown(f'<div class="big-number" style="color: #ff9900">{summary["attendance_rate"]:.1f}%</div>', 
                   unsafe_allow_html=True)
        st.markdown("**Attendance Rate**", unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)
    
    # Charts
    st.markdown("---")
    col1, col2 = st.columns(2)
    
    with col1:
        # Attendance trend
        trend_fig = dashboard.create_attendance_chart(df)
        st.plotly_chart(trend_fig, use_container_width=True)
    
    with col2:
        # Employee status pie
        pie_fig = dashboard.create_employee_status_pie(summary)
        st.plotly_chart(pie_fig, use_container_width=True)
    
    # Punch time distribution
    punch_fig = dashboard.create_punch_time_distribution(df)
    if punch_fig:
        st.plotly_chart(punch_fig, use_container_width=True)
    
    # Employee details table
    st.markdown("---")
    st.subheader("📋 Today's Employee Attendance Details")
    
    if not summary['employee_times'].empty:
        # Format the dataframe for display
        display_df = summary['employee_times'].copy()
        display_df['First Check-in'] = display_df['First Check-in'].dt.strftime('%H:%M:%S')
        display_df['Last Check-out'] = display_df['Last Check-out'].dt.strftime('%H:%M:%S')
        display_df['Working Hours'] = display_df['Working Hours'].round(2)
        
        # Add status column
        display_df['Status'] = display_df['Working Hours'].apply(
            lambda x: '✅ Complete' if x >= 8 else '⚠️ Incomplete'
        )
        
        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "User ID": st.column_config.TextColumn("ID"),
                "Name": st.column_config.TextColumn("Employee Name"),
                "First Check-in": st.column_config.TextColumn("First Check-in"),
                "Last Check-out": st.column_config.TextColumn("Last Check-out"),
                "Working Hours": st.column_config.NumberColumn("Hours", format="%.2f"),
                "Status": st.column_config.TextColumn("Status")
            }
        )
    else:
        st.info("No attendance records for today")
    
    # Recent activities
    st.markdown("---")
    st.subheader("🕐 Recent Check-ins (Last 10)")
    
    recent_df = df.nlargest(10, 'Timestamp')[['User ID', 'Name', 'Timestamp', 'Status']]
    recent_df['Timestamp'] = recent_df['Timestamp'].dt.strftime('%Y-%m-%d %H:%M:%S')
    
    st.dataframe(recent_df, use_container_width=True, hide_index=True)
    
    # Last update time
    st.markdown("---")
    st.caption(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

if __name__ == "__main__":
    main()
