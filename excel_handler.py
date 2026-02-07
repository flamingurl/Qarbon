import openpyxl
from openpyxl import load_workbook, Workbook
from datetime import datetime
import os

class ExcelHandler:
    def __init__(self, workers_file='data/workers.xlsx', tasks_file='data/tasks.xlsx'):
        self.workers_file = workers_file
        self.tasks_file = tasks_file
        self._ensure_files_exist()

    def _ensure_files_exist(self):
        os.makedirs('data', exist_ok=True)
        if not os.path.exists(self.workers_file):
            wb = Workbook()
            ws = wb.active
            ws.title = "Workers"
            ws.append(['Name', 'Job Title', 'Date Working'])
            wb.save(self.workers_file)
        
        if not os.path.exists(self.tasks_file):
            wb = Workbook()
            ws = wb.active
            ws.title = "Tasks"
            ws.append(['Urgency', 'Task Description', 'Date Assigned', 'Date Completed', 'Assigned To'])
            wb.save(self.tasks_file)

    def read_workers(self):
        wb = load_workbook(self.workers_file)
        ws = wb.active
        workers = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row[0]:
                workers.append({'name': row[0], 'job_title': row[1], 'date_working': row[2]})
        return workers

    def read_tasks(self):
        wb = load_workbook(self.tasks_file)
        ws = wb.active
        tasks = []
        for idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            if row[1]:
                tasks.append({
                    'row_number': idx,
                    'urgency': row[0],
                    'description': row[1],
                    'date_assigned': row[2],
                    'date_completed': row[3],
                    'assigned_to': row[4]
                })
        return tasks

    def update_task_completion(self, row_number):
        wb = load_workbook(self.tasks_file)
        ws = wb.active
        timestamp = datetime.now().strftime('%m/%d/%Y %I:%M %p')
        ws.cell(row=row_number, column=4, value=timestamp) # Column 4 is Date Completed
        wb.save(self.tasks_file)
        return timestamp

    def assign_task_to_worker(self, row_number, worker_name):
        wb = load_workbook(self.tasks_file)
        ws = wb.active
        ws.cell(row=row_number, column=5, value=worker_name) # Column 5 is Assigned To
        wb.save(self.tasks_file)

    def add_worker(self, name, job_title, date_working):
        wb = load_workbook(self.workers_file)
        ws = wb.active
        ws.append([name, job_title, date_working])
        wb.save(self.workers_file)

    def add_task(self, urgency, description):
        wb = load_workbook(self.tasks_file)
        ws = wb.active
        date_assigned = datetime.now().strftime('%m/%d/%Y')
        ws.append([urgency, description, date_assigned, '', ''])
        wb.save(self.tasks_file)
