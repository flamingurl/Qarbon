from openai import OpenAI
import json

class AIEngine:
    def __init__(self, api_key):
        # http_client=None resolves potential proxy issues on some cloud hosts
        self.client = OpenAI(api_key=api_key, http_client=None)

    def assign_tasks_one_per_person(self, workers, tasks):
        """Bulk matching logic ensuring Job Title fits Task Description."""
        busy_workers = {t['assigned_to'] for t in tasks if t['assigned_to'] and not t['date_completed']}
        available_workers = [w for w in workers if w['name'] not in busy_workers]
        available_tasks = [t for t in tasks if not t['assigned_to'] and not t['date_completed']]

        if not available_tasks or not available_workers:
            return {}

        prompt = f"""
        Assign ONE task to each worker. 
        MANDATORY: Matching worker 'job_title' to 'description'. 
        Example: Maintenance roles get repairs; Machinists get lathe work.
        WORKERS: {json.dumps(available_workers)}
        TASKS: {json.dumps(available_tasks)}
        Return JSON ONLY: {{"WorkerName": [TaskRowNumber]}}
        """
        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"}
            )
            return json.loads(response.choices[0].message.content)
        except:
            return {}

    def get_single_qualified_assignment(self, worker, tasks):
        """AI validation for individual 'Assign' button clicks."""
        prompt = f"""
        Find the single best-qualified task for this specific worker.
        WORKER: {worker['name']} ({worker['job_title']})
        TASKS: {json.dumps(tasks)}
        Logic: Match role to task nature. If no task fits their title, return row_number as null.
        Return JSON ONLY: {{"row_number": 123}}
        """
        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"}
            )
            return json.loads(response.choices[0].message.content)
        except:
            return None
