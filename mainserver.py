import os
from dotenv import load_dotenv
import mysql.connector
from flask import Flask, request, jsonify
from flask_cors import CORS
import pandas as pd
import time 

# Load environment variables from the .env file
# Ensure you create a .env file with DB_USER, DB_PASSWORD, DB_HOST, and DB_NAME=etl_db
load_dotenv()

# --- Configuration ---
DB_CONFIG = {
    'user': os.environ.get('DB_USER', 'root'),
    'password': os.environ.get('DB_PASSWORD'),
    'host': os.environ.get('DB_HOST', 'localhost'),
    'database': os.environ.get('DB_NAME', 'etl_db')
}

# generates and sends in the JSON payload. They must match the non-primary-key 
# columns in the 'etl_data' table exactly.
DB_COLUMNS_FOR_INSERT = [
    'candidate_id', 
    'job_id', 
    'experience_years', 
    'job_role',
    'expected_salary', 
    'candidate_name', 
    'location', 
    'application_score'
]

# --- Application Setup ---
app = Flask('job_portal_server')
app.config['DEBUG'] = os.environ.get('FLASK_DEBUG') == '1'
CORS(app)

# --- Database Connection Management ---
def get_db_connection():
    """Establishes and returns a database connection."""
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        return conn
    except mysql.connector.Error as err:
        print(f"[{time.strftime('%I:%M:%S %p')}] Database connection failed: {err}")
        return None

def create_initial_dataframe_from_db():
    """Fetches ALL data from the etl_data table for OLAP operations."""
    conn = get_db_connection()
    if not conn:
        return None
    try:
        # Fetch all columns dynamically from the database
        cursor = conn.cursor()
        cursor.execute("SHOW COLUMNS FROM etl_data")
        db_cols = [col[0] for col in cursor.fetchall()] # type: ignore
        
        # Exclude the primary key (assuming it's named 'application_transaction_id' or similar)
        fetch_cols = [col for col in db_cols if col in DB_COLUMNS_FOR_INSERT] 
        
        query = f"SELECT {', '.join(fetch_cols)} FROM etl_data" # type: ignore
        df = pd.read_sql(query, conn)

        # Convert relevant new columns to numeric
        numeric_cols = ['experience_years', 'expected_salary', 'application_score']
        for col in numeric_cols:
            df[col] = pd.to_numeric(df[col], errors='coerce') 

        # Clean up whitespace in string columns
        for col in df.select_dtypes(include=['object']).columns:
            df[col] = df[col].astype(str).str.strip()
            
        return df
    except Exception as e:
        print(f"[{time.strftime('%I:%M:%S %p')}] Error fetching data for OLAP: {e}")
        return None
    finally:
        if conn and conn.is_connected():
            conn.close()

# ------------------------------------
# OLAP Operation: Drill-down (Location -> Candidate)
# ------------------------------------
@app.route('/api/olap/drilldown', methods=['POST'])
def olap_drilldown():
    """Drill-down: Location -> Candidate_Name."""
    payload = request.get_json()
    location = payload.get('location', 'London') 

    df = create_initial_dataframe_from_db()
    if df is None or df.empty:
        # Simplified error message for the frontend
        return jsonify({"status": "error", "message": "No ETL data found in database. Load data first."}), 500

    drill_df = df[df['location'].str.lower() == location.lower()]

    if drill_df.empty:
        drilldown_report = f"2. Drill-down Report for Location: {location}\nNo applications found for location: {location}."
    else:
        drilldown_result = drill_df.groupby('candidate_name')['application_score'].sum().reset_index()
        drilldown_report = f"2. Drill-down Report for Location: {location}\n"
        drilldown_report += "Total Application Score aggregated by Candidate Name:\n"
        drilldown_report += drilldown_result.sort_values(by='application_score', ascending=False).to_string(index=False)

    return jsonify({"status": "success", "report": drilldown_report})

# ------------------------------------
# OLAP Operation: Slice (Filter by Job Role)
# ------------------------------------
@app.route('/api/olap/slice', methods=['POST'])
def olap_slice():
    """Slice: Filter by a single job role."""
    payload = request.get_json()
    job_role = payload.get('job_role', 'Data Engineer')

    df = create_initial_dataframe_from_db()
    if df is None or df.empty:
        # Simplified error message for the frontend
        return jsonify({"status": "error", "message": "No ETL data found in database. Load data first."}), 500

    slice_df = df[df['job_role'].str.lower() == job_role.lower()]

    if slice_df.empty:
        slice_report = f"3. Slice Report for Job Role: {job_role}\nNo applications found for job role: {job_role}."
    else:
        slice_pivot = slice_df.pivot_table(
            index='location', 
            values='application_score', 
            aggfunc='sum',
            fill_value=0
        )

        slice_report = f"3. Slice Report for Job Role: {job_role}\n"
        slice_report += "Total Application Score aggregated by Location:\n"
        slice_report += slice_pivot.round(2).to_string()

    return jsonify({"status": "success", "report": slice_report})

# ------------------------------------
# OLAP Operation: Dice (Location & Experience Threshold)
# ------------------------------------
@app.route('/api/olap/dice', methods=['POST'])
def olap_dice():
    """Dice: Filter by multiple locations AND an experience threshold."""
    locations = ['SF', 'NY']
    min_experience_years = 3 

    df = create_initial_dataframe_from_db()
    if df is None or df.empty:
        # Simplified error message for the frontend
        return jsonify({"status": "error", "message": "No ETL data found in database. Load data first."}), 500

    dice_df = df[
        (df['location'].isin(locations)) &
        (df['experience_years'] >= min_experience_years)
    ]

    if dice_df.empty:
        dice_report = f"4. Dice Report: Total Application Score for Locations ({', '.join(locations)}) with Experience >= {min_experience_years} years\nNo high-experience applications found for the specified criteria."
    else:
        dice_pivot = dice_df.pivot_table(
            index='location', 
            columns='job_role', 
            values='application_score', 
            aggfunc='sum',
            fill_value=0
        )

        dice_report = f"4. Dice Report: Total Application Score for Locations ({', '.join(locations)}) with Experience >= {min_experience_years} years\n"
        dice_report += dice_pivot.round(2).to_string()

    return jsonify({"status": "success", "report": dice_report})

# -----------------------------------
# ETL Load Operation
# -----------------------------------
@app.route('/api/load', methods=['POST'])
def api_load():
    """Handles the actual insertion of transformed data into the MySQL database."""
    conn = None
    cursor = None
    try:
        # 1. Get data from the frontend
        records = request.get_json()
        if not records or not isinstance(records, list) or len(records) == 0:
            # Simple log for server console
            print(f"[{time.strftime('%I:%M:%S %p')}] DB Load: No data received.")
            return jsonify({"status": "error", "message": "No data or invalid data format received."}), 400

        # 2. Establish connection
        conn = get_db_connection()
        if not conn:
            return jsonify({"status": "error", "message": "Could not connect to the database."}), 500

        cursor = conn.cursor()

        # Truncate/Delete existing data before loading new data
        cursor.execute("TRUNCATE TABLE etl_data")
        print(f"[{time.strftime('%I:%M:%S %p')}] DB Load: Truncated existing data.")

        # 3. Prepare the SQL INSERT statement
        table_name = "etl_data"
        # FIX: Uses the list of 8 data columns, excluding the AUTO_INCREMENT primary key
        columns_sql = ", ".join(DB_COLUMNS_FOR_INSERT)
        placeholders = ", ".join(["%s"] * len(DB_COLUMNS_FOR_INSERT))

        sql = f"INSERT INTO {table_name} ({columns_sql}) VALUES ({placeholders})"

        # 4. Format data for batch insertion (Ensuring column order is respected)
        data_to_insert = [tuple(record.get(col) for col in DB_COLUMNS_FOR_INSERT) for record in records]

        # 5. Execute batch insertion
        cursor.executemany(sql, data_to_insert)
        rows_inserted = cursor.rowcount
        conn.commit()

        # Simple log for server console
        print(f"[{time.strftime('%I:%M:%S %p')}] DB Load SUCCESS: Inserted {rows_inserted} rows.")

        return jsonify({
            "status": "success",
            "message": f"Successfully loaded {len(records)} records. {rows_inserted} rows inserted."
        }), 200

    except mysql.connector.Error as err:
        if conn and conn.is_connected():
            conn.rollback()
        # Detailed error log for server console
        print(f"[{time.strftime('%I:%M:%S %p')}] DB Load FAILED: {err.msg}")
        # Frontend error message
        return jsonify({"status": "error", "message": f"LOAD FAILED. Database Error: Check your database schema. {err.msg}"}), 500

    except Exception as e:
        error_message = f"An unexpected error occurred: {e}"
        print(f"[{time.strftime('%I:%M:%S %p')}] DB Load FAILED: {error_message}")
        return jsonify({"status": "error", "message": "An unexpected server error occurred."}), 500

    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()
            print(f"[{time.strftime('%I:%M:%S %p')}] MySQL connection closed.")

# --- Flask Run ---
if __name__ == '__main__':
    print("Starting Flask server...")
    app.run(host='0.0.0.0', port=5000)