# server.py (Basic Flask setup for receiving JSON)
from flask import Flask, request, jsonify
from flask_cors import CORS 

app = Flask(__name__)
# Allows requests from any origin (*). IMPORTANT: Change to your domain (e.g., 'https://mytool.com') in production.
CORS(app) 

@app.route('/api/load', methods=['POST'])
def load_data():
    if not request.json:
        return jsonify({"status": "error", "message": "No JSON data received."}), 400

    transformed_records = request.json

    #  YOUR DATABASE LOGIC GOES HERE
    # You need code to connect to your database (PostgreSQL, MySQL, etc.) 
    # and execute an INSERT statement for the data in 'transformed_records'.

    try:
        # Example: Simulate database insertion delay
        # time.sleep(0.5) 

        # Replace with actual DB insertion code
        print(f" Data received: {len(transformed_records)} records ready for database insertion.")

        return jsonify({
            "status": "success", 
            "message": f"Successfully loaded {len(transformed_records)} records into the database."
        }), 200

    except Exception as e:
        # Handle database connection or insert errors
        print(f" Database error: {e}")
        return jsonify({"status": "error", "message": f"Database insertion failed: {str(e)}"}), 500


if __name__ == '__main__':
    print("Starting server on http://localhost:5000...")
    # Use debug=False and a more robust server (like Gunicorn) for production
    app.run(debug=True, port=5000)