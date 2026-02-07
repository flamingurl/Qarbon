from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from excel_handler import ExcelHandler
from ai_engine import AIEngine
import os
from dotenv import load_dotenv

# 1. Load Environment Variables
# Locally this uses .env; on Render it uses the dashboard settings
load_dotenv()

app = Flask(__name__, static_folder='static')
CORS(app)

# 2. Configuration & Initialization
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Initialize our custom classes
excel_handler = ExcelHandler()
ai_engine = AIEngine(OPENAI_API_KEY)

# --- FRONTEND UI ROUTES ---

@app.route('/')
def route_worker_interface():
    """Serves the main worker page (index.html)"""
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/manager')
def route_manager_interface():
    """Serves the manager dashboard (manager.html)"""
    return send_from_directory(app.static_folder, 'manager.html')

# --- BACKEND API ROUTES ---

@app.route('/api/workers', methods=['GET'])
def api_get_workers():
    return jsonify(excel_handler.read_workers())

@app.route('/api/tasks', methods=['GET'])
def api_get_tasks():
    return jsonify(excel_handler.read_tasks())

@app.route('/api/add-worker', methods=['POST'])
def api_add_worker():
    data = request.json
    excel_handler.add_worker(
        data.get('name'), 
        data.get('job_title'), 
        data.get('date_working')
    )
    return jsonify({'success': True})

@app.route('/api/add-task', methods=['POST'])
def api_add_task():
    data = request.json
    excel_handler.add_task(
        data.get('urgency', 3), 
        data.get('description')
    )
    return jsonify({'success': True})

@app.route('/api/complete-task', methods=['POST'])
def api_complete_task():
    data = request.json
    row_number = data.get('row_number')
    timestamp = excel_handler.update_task_completion(row_number)
    return jsonify({'success': True, 'timestamp': timestamp})

@app.route('/api/assign-tasks', methods=['POST'])
def api_assign_tasks():
    workers = excel_handler.read_workers()
    tasks = excel_handler.read_tasks()
    
    # Trigger AI engine to create mapping
    assignments = ai_engine.assign_tasks_to_workers(workers, tasks)
    
    # Save assignments into the Excel sheet
    for worker_name, row_numbers in assignments.items():
        for row_num in row_numbers:
            excel_handler.assign_task_to_worker(row_num, worker_name)
            
    return jsonify({'success': True, 'assignments': assignments})

# Helper for worker-specific views
@app.route('/api/worker-tasks/<worker_name>', methods=['GET'])
def api_get_specific_worker_tasks(worker_name):
    tasks = excel_handler.read_tasks()
    worker_tasks = [t for t in tasks if t['assigned_to'] == worker_name and not t['date_completed']]
    return jsonify(worker_tasks)

# --- SERVER STARTUP ---

if __name__ == '__main__':
    # PORT is provided by Render's environment
    port = int(os.environ.get("PORT", 5000))
    # host='0.0.0.0' is required to make the server accessible publicly
    app.run(host='0.0.0.0', port=port)
