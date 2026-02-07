from openai import OpenAI
from datetime import datetime
import json

class AIEngine:
    def __init__(self, api_key):
        """
        Initialize OpenAI client
        api_key: Your OpenAI API key
        """
        self.client = OpenAI(api_key=api_key)
    
    def assign_tasks_to_workers(self, workers, tasks):
        """
        Use AI to intelligently assign tasks to workers
        Returns: Dictionary mapping worker names to assigned tasks
        """
        # Filter incomplete tasks
        incomplete_tasks = [t for t in tasks if not t['date_completed']]
        
        if not incomplete_tasks or not workers:
            return {}
        
        # Prepare data for AI
        workers_info = "\n".join([
            f"- {w['name']}: Role={w['job_title']}, Schedule={w['date_working']}"
            for w in workers
        ])
        
        tasks_info = "\n".join([
            f"- Task {t['row_number']}: Urgency={t['urgency']}/5, Description={t['description']}, Currently Assigned To={t['assigned_to'] or 'Unassigned'}"
            for t in incomplete_tasks
        ])
        
        current_date = datetime.now().strftime('%m/%d/%Y')
        
        prompt = f"""You are a factory work assignment AI. Today's date is {current_date}.

WORKERS:
{workers_info}

INCOMPLETE TASKS:
{tasks_info}

Instructions:
1. Assign tasks to workers based on:
   - Worker job title matching task requirements
   - Worker availability (date_working schedule)
   - Task urgency (1=low, 5=critical)
   - Balanced workload distribution
2. Prioritize urgent tasks (urgency 4-5)
3. Only assign tasks to workers who are scheduled to work today or soon
4. Each worker should get 1-3 tasks maximum for balanced distribution

Return a JSON object with this structure:
{{
  "worker_name": [task_row_number1, task_row_number2, ...],
  ...
}}

Only include workers who should receive task assignments. Return valid JSON only, no other text."""

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are a factory task assignment specialist. Return only valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=1000
            )
            
            result = response.choices[0].message.content.strip()
            
            # Clean up potential markdown code blocks
            if result.startswith("```json"):
                result = result[7:]
            if result.startswith("```"):
                result = result[3:]
            if result.endswith("```"):
                result = result[:-3]
            
            assignments = json.loads(result.strip())
            return assignments
            
        except Exception as e:
            print(f"AI Assignment Error: {e}")
            return {}
    
    def suggest_next_task(self, worker_name, worker_role, completed_task, available_tasks):
        """
        Suggest the next best task for a worker after completing one
        Returns: Task row number or None
        """
        if not available_tasks:
            return None
        
        tasks_info = "\n".join([
            f"- Task {t['row_number']}: Urgency={t['urgency']}/5, Description={t['description']}"
            for t in available_tasks
        ])
        
        prompt = f"""Worker "{worker_name}" (Role: {worker_role}) just completed: "{completed_task}"

Available tasks:
{tasks_info}

Suggest the SINGLE best next task for this worker based on:
1. Task urgency
2. Worker's role compatibility
3. Task similarity/continuity with completed work

Return ONLY the task row number as an integer, nothing else."""

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are a task recommendation specialist. Return only an integer."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=50
            )
            
            result = response.choices[0].message.content.strip()
            return int(result)
            
        except Exception as e:
            print(f"AI Suggestion Error: {e}")
            # Fallback: return highest urgency task
            if available_tasks:
                sorted_tasks = sorted(available_tasks, key=lambda x: x['urgency'], reverse=True)
                return sorted_tasks[0]['row_number']
            return None
