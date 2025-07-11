import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
import json

st.title("🔧 Test Google Sheets Connection")

# Load credentials
credentials_dict = None
uploaded_file = st.file_uploader("Upload credentials.json", type=['json'])

if uploaded_file is not None:
    credentials_dict = json.load(uploaded_file)
else:
    try:
        credentials_dict = dict(st.secrets["gcp_service_account"])
        st.success("✅ Found credentials in secrets")
    except:
        st.warning("⚠️ No credentials found")

if credentials_dict:
    spreadsheet_name = st.text_input("Spreadsheet Name", value="ZKTeco Attendance")
    
    if st.button("Test Connection"):
        try:
            # Connect to Google Sheets
            scope = [
                "https://spreadsheets.google.com/feeds",
                "https://www.googleapis.com/auth/drive"
            ]
            credentials = Credentials.from_service_account_info(credentials_dict, scopes=scope)
            gc = gspread.authorize(credentials)
            
            st.success("✅ Connected to Google Sheets API")
            
            # Open spreadsheet
            spreadsheet = gc.open(spreadsheet_name)
            st.success(f"✅ Opened spreadsheet: {spreadsheet_name}")
            
            # Get worksheet
            worksheet = spreadsheet.worksheet("Attendance")
            st.success("✅ Found Attendance worksheet")
            
            # Get data
            data = worksheet.get_all_records()
            st.info(f"📊 Found {len(data)} records")
            
            if data:
                df = pd.DataFrame(data)
                st.write("**Columns found:**", df.columns.tolist())
                st.write("**First 5 rows:**")
                st.dataframe(df.head())
                
                # Check data types
                st.write("**Data types:**")
                for col in df.columns:
                    st.write(f"- {col}: {df[col].dtype}")
                    if col in ['Timestamp', 'Date']:
                        st.write(f"  Sample: {df[col].iloc[0] if len(df) > 0 else 'No data'}")
            else:
                st.warning("No data found in the worksheet")
                
        except Exception as e:
            st.error(f"❌ Error: {str(e)}")
            st.write("**Full error details:**")
            st.exception(e)
