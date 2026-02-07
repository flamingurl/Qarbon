from openai import OpenAI
import json

class AIEngine:
    def __init__(self, api_key):
        self.client = OpenAI(api_key=api_key, http_client=None)

    def assign_tasks_one_per_person(self, workers, tasks):
        # Filter: Only workers with no current active tasks
        busy_workers = {t['assigned_to'] for t in tasks if t['assigned_to'] and not t['date_completed']}
        available_workers = [w for w in workers if w['name'] not in busy_workers]
        available_tasks = [t for t in tasks if not t['assigned_to'] and not t['date_completed']]

        if not available_tasks or not available_workers:
            return {}

        prompt = f"""
        Assign ONE task to each available worker. 
        WORKERS: {json.dumps(available_workers)}
        TASKS: {json.dumps(available_tasks)}
        Return JSON: {{"WorkerName": [TaskRowNumber]}}
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
