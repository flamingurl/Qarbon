from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from excel_handler import ExcelHandler
from ai_engine import AIEngine
import os
from dotenv import load_dotenv

# Load local .env file if it exists (for local testing)
load_dotenv()

app = Flask(__name__, static_folder='static')
CORS(app)

# --- CONFIGURATION ---
# Render will use the Environment Variable; locally it uses your .env
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

excel_handler = ExcelHandler()
ai_engine = AIEngine(OPENAI_API_KEY)

# --- FRONTEND ROUTES ---

@app.route('/')
def serve_index():
    """Serves the Worker Interface"""
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/manager')
def serve_manager():
    """Serves the Manager Dashboard"""
    return send_from_directory(app.static_folder, 'manager.html')

# --- API ROUTES ---

@app.route('/api/workers', methods=['GET'])
def get_workers():
    workers = excel_handler.read_workers()
    return jsonify(workers)

@app.route('/api/tasks', methods=['GET'])
def get_tasks():
    tasks = excel_handler.read_tasks()
    return jsonify(tasks)

@app.route('/api/add-worker', methods=['POST'])
def add_worker():
    data = request.json
    excel_handler.add_worker(
        data.get('name'),
        data.get('job_title'),
        data.get('date_working')
    )
    return jsonify({'success': True})

@app.route('/api/add-task', methods=['POST'])
def add_task():
    data = request.json
    excel_handler.add_task(
        data.get('urgency', 3),
        data.get('description')
    )
    return jsonify({'success': True})

@app.route('/api/complete-task', methods=['POST'])
def complete_task():
    data = request.json
    row_number = data.get('row_number')
    # Mark task complete in Excel
    timestamp = excel_handler.update_task_completion(row_number)
    return jsonify({'success': True, 'timestamp': timestamp})

@app.route('/api/assign-tasks', methods=['POST'])
def assign_tasks():
    """AI-powered task assignment for all workers"""
    workers = excel_handler.read_workers()
    tasks = excel_handler.read_tasks()
    
    # Get assignments from AI
    assignments = ai_engine.assign_tasks_to_workers(workers, tasks)
    
    # Update Excel with these assignments
    for worker_name, task_rows in assignments.items():
        for row_num in task_rows:
            excel_handler.assign_task_to_worker(row_num, worker_name)
            
    return jsonify({
        'success': True,
        'assignments': assignments
    })

# Helper route for workers to see only their assigned tasks
@app.route('/api/worker-tasks/<worker_name>', methods=['GET'])
def get_worker_tasks(worker_name):
    tasks = excel_handler.read_tasks()
    worker_tasks = [t for t in tasks if t['assigned_to'] == worker_name and not t['date_completed']]
    return jsonify(worker_tasks)

if __name__ == '__main__':
    # Render provides a PORT environment variable
    port = int(os.environ.get("PORT", 5000))
    # host='0.0.0.0' is required for public access
    app.run(host='0.0.0.0', port=port)
    
@app.route('/')
def serve_index():
    return send_from_directory('static', 'index.html')

@app.route('/manager')
def serve_manager():
    return send_from_directory('static', 'manager.html')
