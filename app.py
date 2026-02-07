from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from excel_handler import ExcelHandler
from ai_engine import AIEngine
import os

app = Flask(__name__, static_folder='static') # Direct Flask to your HTML folder
CORS(app)

# Use Environment Variables for security (Set this in Render/Railway dashboard)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "your-fallback-key")

excel_handler = ExcelHandler()
ai_engine = AIEngine(OPENAI_API_KEY)

# --- ROUTES TO SERVE THE FRONTEND ---
@app.route('/')
def index():
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/manager')
def manager():
    return send_from_directory(app.static_folder, 'manager.html')

# --- API ROUTES ---
@app.route('/api/workers', methods=['GET'])
def get_workers():
    return jsonify(excel_handler.read_workers())

# ... Keep all your existing /api routes here ...

if __name__ == '__main__':
    # Use port assigned by the host, default to 5000
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
    
@app.route('/')
def serve_index():
    return send_from_directory('static', 'index.html')

@app.route('/manager')
def serve_manager():
    return send_from_directory('static', 'manager.html')
