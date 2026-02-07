from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from excel_handler import ExcelHandler
from ai_engine import AIEngine
import os
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__, static_folder='static')
CORS(app)

excel_handler = ExcelHandler()
ai_engine = AIEngine(os.getenv("OPENAI_API_KEY"))

@app.route('/')
def route_index(): return send_from_directory(app.static_folder, 'index.html')

@app.route('/manager')
def route_manager(): return send_from_directory(app.static_folder, 'manager.html')

@app.route('/api/tasks', methods=['GET'])
def get_tasks(): return jsonify(excel_handler.read_tasks())

@app.route('/api/workers', methods=['GET'])
def get_workers(): return jsonify(excel_handler.read_workers())

@app.route('/api/add-worker', methods=['POST'])
def add_worker():
    d = request.json
    excel_handler.add_worker(d['name'], d['job_title'], d['date_working'])
    return jsonify({'success': True})

@app.route('/api/add-task', methods=['POST'])
def add_task():
    d = request.json
    excel_handler.add_task(d['urgency'], d['description'])
    return jsonify({'success': True})

@app.route('/api/complete-task', methods=['POST'])
def complete_task():
    excel_handler.update_task_completion(request.json['row_number'])
    return jsonify({'success': True})

@app.route('/api/delete-task/<int:row>', methods=['DELETE'])
def delete_task(row):
    excel_handler.delete_task(row)
    return jsonify({'success': True})

@app.route('/api/delete-worker/<name>', methods=['DELETE'])
def delete_worker(name):
    excel_handler.delete_worker(name)
    return jsonify({'success': True})

@app.route('/api/assign-tasks', methods=['POST'])
def assign_bulk():
    assignments = ai_engine.assign_tasks_one_per_person(excel_handler.read_workers(), excel_handler.read_tasks())
    for name, rows in assignments.items():
        if rows: excel_handler.assign_task_to_worker(rows[0], name)
    return jsonify({'success': True})

@app.route('/api/assign-self', methods=['POST'])
def assign_self():
    name = request.json['worker_name']
    tasks = [t for t in excel_handler.read_tasks() if not t['assigned_to'] and not t['date_completed']]
    if tasks:
        best = sorted(tasks, key=lambda x: x['urgency'], reverse=True)[0]
        excel_handler.assign_task_to_worker(best['row_number'], name)
        return jsonify({'success': True})
    return jsonify({'success': False})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
