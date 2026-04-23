# agents/content_agent.py

from app.utils.llm import call_llm
from app.prompts.execution_prompt import EXECUTION_PROMPT   
from app.prompts.twitter_prompt import TWITTER_PROMPT  
from app.prompts.tiktok_prompt import TIKTOK_PROMPT  
from app.agents.critic_agent import critic_agent

import json

def parse_llm_output(response):
    try:
        return json.loads(response)
    except Exception:
        return {
            "error": "invalid_json",
            "raw": response
        }

def generate_content(strategy):
    return call_llm(EXECUTION_PROMPT, strategy)

def execute_task(task):
    platform = task["platform"]

    user_prompt = f"""
INPUT: {task["input"]}
GOAL: {task["goal"]}
OUTPUT_TYPE: {task["output_type"]}
"""

    if platform == "twitter":
        return call_llm(TWITTER_PROMPT, user_prompt)

    if platform == "tiktok":
        return call_llm(TIKTOK_PROMPT, user_prompt)


def run_execution_pipeline(execution_plan: list):
    results = []

    for task in execution_plan:
        print(f"Executing task {task['task_id']} on {task['platform']}...")

        output = execute_task(task)

        critique = critic_agent(task, output)

        print(critique["score"])
        # 3. If bad → retry loop
        if critique["verdict"] != "approve":
            print(f"Re-executing task {task['task_id']} due to: {critique['issues']}")

            # optional: inject feedback into retry
            task_with_feedback = {
                **task,
                "feedback": critique["improvements"]
            }

            output = execute_task(task_with_feedback)

            # re-check (optional but recommended)
            critique = critic_agent(task, output)

        results.append({
            "task_id": task["task_id"],
            "platform": task["platform"],
            "output": output
        })

    return results