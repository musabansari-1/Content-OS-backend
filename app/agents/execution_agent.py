# agents/content_agent.py

from app.utils.llm import call_llm
from app.prompts.execution_prompt import EXECUTION_PROMPT   
from app.prompts.twitter_prompt import TWITTER_PROMPT  
from app.prompts.tiktok_prompt import TIKTOK_PROMPT  
from app.prompts.linkedin_prompt import LINKEDIN_PROMPT
from app.prompts.youtube_prompt import YOUTUBE_PROMPT  
from app.agents.critic_agent import critic_agent
from app.prompts.conversion_prompt import CONVERSION_PROMPT

import json

import json

def optimize_for_conversion(output, platform, critique):
    payload = json.dumps({
        "output": output,
        "platform": platform,
        "issues": critique.get("issues", []),
        "improvements": critique.get("improvements", [])
    })

    return call_llm(CONVERSION_PROMPT, payload)


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

def execute_task(task, source):
    platform = task["platform"]

    user_prompt = f"""
You are a high-performance social media growth writer.
Your goal is NOT just to create content.
Your goal is to DRIVE TRAFFIC to the original video.    

INPUT: {task["input"]}
GOAL: {task["goal"]}
OUTPUT_TYPE: {task["output_type"]}
FEEDBACK: {task["feedback"]}
SOURCE: {source}

Priority order (strict):

-Preserve source truth
-Preserve creator voice
-Adapt to platform
-Optimize for traffic

If two goals conflict, follow higher priority.
IMPORTANT:
- Use SOURCE for real details
- Do not invent any details other than whats in the source.
- Apply FEEDBACK strictly if present. This is critical . Apply feedback compulsarily.
- Avoid generic content

GOAL:
Drive maximum clicks to the original YouTube video.

DO NOT:
- invent events not present in input
- create fake statistics
- exaggerate beyond realism

RULES:
- Every content piece MUST create curiosity gap
- NEVER fully explain the story
- Always leave an unanswered question
- CTA must feel natural, not generic
- Use specific emotional hooks from input
- Write in first person
- Avoid generic advice tone
- Use specific lived experiences
- Maintain same voice across platforms

CRITICAL CONSTRAINT:

-If there is a feedback then strictly follow it. 
- Use ONLY details explicitly present in the input transcript
- DO NOT invent numbers, companies, salaries, or scenarios
- Extract REAL moments from the story
- Specificity > creativity

CONTENT STRATEGY:

- Identify the most emotional / surprising / painful moment
- Build the hook around THAT moment
- Do NOT generalize into abstract ideas

STRICT RULES:
- Use ONLY details present in the SOURCE
- Do NOT invent events, dialogues, or statistics
- If unsure, omit rather than hallucinate

STRICT REQUIREMENTS:

1. Start with a STRONG HOOK (first line / first 2 seconds)
2. Create a CURIOSITY GAP (make user want more)
3. Deliver PARTIAL VALUE (not full story)
4. Add a CLEAR CTA that pushes to the full video
5. Content must feel NATIVE to the platform
6. Avoid generic motivational tone
7. Optimize for engagement (comments, shares, replies)

---

CRITICAL:
- Avoid generic advice or motivational tone
- Use SPECIFIC details from the story (numbers, places, experiences)
- Maximize curiosity gap before CTA
- Make user feel "I need to know what happened next"
- Use ONLY information from the input transcript
- Do NOT invent or exaggerate facts
- Keep content authentic and grounded
- Every output MUST create an open loop
- CTA must make user feel: "I need to know what happened next"


OUTPUT MUST:
- Be platform-specific
- Include a CTA to watch full video
- Be structured for high retention
"""

    # user_prompt = EXECUTION_PROMPT

    if platform == "twitter":
        return call_llm(TWITTER_PROMPT, user_prompt)

    if platform == "tiktok":
        return call_llm(TIKTOK_PROMPT, user_prompt)
    
    if platform == "linkedin":
        return call_llm(LINKEDIN_PROMPT, user_prompt)

    if platform == "youtube":
        return call_llm(YOUTUBE_PROMPT, user_prompt)


# def run_execution_pipeline(execution_plan: list):
#     results = []

#     for task in execution_plan:
#         print(f"Executing task {task['task_id']} on {task['platform']}...")

#         output = execute_task(task)

#         critique = critic_agent(task, output)

#         print(critique["score"])
#         # 3. If bad → retry loop
#         if critique["verdict"] != "approve":
#             print(f"Re-executing task {task['task_id']} due to: {critique['issues']}")

#             # optional: inject feedback into retry
#             task_with_feedback = {
#                 **task,
#                 "feedback": critique["improvements"]
#             }

#             output = execute_task(task_with_feedback)

#             optimized_output = optimize_for_conversion(
#             output,
#             task["platform"]
#             )

#             # re-check (optional but recommended)
#             # critique = critic_agent(task, optimized_output)

#         results.append({
#             "task_id": task["task_id"],
#             "platform": task["platform"],
#             "output": optimized_output
#         })

#     return results


# def run_execution_pipeline(execution_plan: list):
#     results = []

#     for task in execution_plan:
#         print(f"Executing task {task['task_id']} on {task['platform']}...")

#         output = execute_task(task)

#         critique = critic_agent(task, output)

#         print(critique["score"])

#         # Retry if not approved
#         if critique["verdict"] != "approve":
#             print(f"Re-executing task {task['task_id']} due to: {critique['issues']}")

#             task_with_feedback = {
#                 **task,
#                 "feedback": critique["improvements"]
#             }

#             output = execute_task(task_with_feedback)

#         # ✅ ALWAYS optimize (outside if)
#         optimized_output = optimize_for_conversion(
#             output,
#             task["platform"]
#         )

#         results.append({
#             "task_id": task["task_id"],
#             "platform": task["platform"],
#             "output": optimized_output
#         })

#     return results

# def run_execution_pipeline(execution_plan: list):
#     results = []

#     for task in execution_plan:
#         print(f"Executing task {task['task_id']} on {task['platform']}...")

#         output = execute_task(task)

#         critique = critic_agent(task, output)

#         print(critique["score"])

#         # Retry if not approved
#         if critique["verdict"] != "approve":
#             print(f"Re-executing task {task['task_id']} due to: {critique['issues']}")

#             task_with_feedback = {
#                 **task,
#                 "feedback": critique["improvements"]
#             }

#             output = execute_task(task_with_feedback)

#             # 🔁 re-critique after retry (important)
#             critique = critic_agent(task, output)

#         # ✅ ALWAYS optimize USING critique
#         optimized_output = optimize_for_conversion(
#             output,
#             task["platform"],
#             critique   # <-- pass it here
#         )

#         results.append({
#             "task_id": task["task_id"],
#             "platform": task["platform"],
#             "output": optimized_output
#         })

#     return results


# def run_execution_pipeline(execution_plan: list, source: str):
#     results = []

#     for task in execution_plan:
#         print(f"Executing task {task['task_id']} on {task['platform']}...")

#         output = execute_task(task)

#         max_attempts = 3
#         attempt = 0

#         while attempt < max_attempts:
#             critique = critic_agent(task, output, source)
#             print(f"Attempt {attempt+1} Score:", critique["score"])
            
#             print(critique["improvements"])

#             if critique["verdict"] == "approve":
#                 break

#             # 🔥 feed critique back into generation
#             task = {
#                 **task,
#                 "feedback": critique["improvements"]
#             }

#             output = execute_task(task)
#             attempt += 1

#         # ✅ NOW pass critique into optimizer
#         optimized_output = optimize_for_conversion(
#             output,
#             task["platform"],
#             critique  # 👈 important
#         )

#         results.append({
#             "task_id": task["task_id"],
#             "platform": task["platform"],
#             "output": optimized_output
#         })

#     return results


# def run_execution_pipeline(execution_plan: list, source: str):
#     results = []

#     for task in execution_plan:
#         print(f"Executing task {task['task_id']} on {task['platform']}...")

#         max_attempts = 3
#         attempt = 0

#         # 🔒 keep original task safe
#         base_task = {**task, "source": source}

#         best_output = None
#         best_score = -1
#         best_critique = None

#         current_task = base_task

#         while attempt < max_attempts:
#             output = execute_task(current_task)

#             critique = critic_agent(current_task, output, source)

#             print(f"Attempt {attempt+1} Score:", critique["score"])
#             print("output: ",output, sep="\n")
#             print("critique feedback: ",critique["improvements"], sep="\n")

#             # ✅ track best output
#             if critique["score"] > best_score:
#                 best_score = critique["score"]
#                 best_output = output
#                 best_critique = critique

#             if critique["verdict"] == "approve":
#                 break

#             # 🔁 create new task with feedback (DO NOT overwrite original)
#             current_task = {
#                 **base_task,
#                 "feedback": critique["improvements"]
#             }

#             attempt += 1

#         # ✅ always use best output (not last)
#         optimized_output = optimize_for_conversion(
#             best_output,
#             task["platform"],
#             best_critique
#         )

#         results.append({
#             "task_id": task["task_id"],
#             "platform": task["platform"],
#             "output": optimized_output
#         })

#     return results


# def run_execution_pipeline(execution_plan: list, source: str):
#     results = []

#     for task in execution_plan:
#         print(f"Executing task {task['task_id']} on {task['platform']}...")

#         max_attempts = 3
#         attempt = 0

#         # ✅ include empty feedback from start
#         base_task = {
#             **task,
#             "source": source,
#             "feedback": []   # 👈 important
#         }

#         best_output = None
#         best_score = -1
#         best_critique = None

#         current_task = base_task

#         while attempt < max_attempts:
#             output = execute_task(current_task)

#             critique = critic_agent(current_task, output, source)

#             print(f"Attempt {attempt+1} Score:", critique["score"])
#             print("output: ", output, sep="\n")
#             print("critique feedback: ", critique["improvements"], sep="\n")

#             # ✅ track best output
#             if critique["score"] > best_score:
#                 best_score = critique["score"]
#                 best_output = output
#                 best_critique = critique

#             if critique["verdict"] == "approve":
#                 break

#             # 🔁 always overwrite feedback cleanly
#             current_task = {
#                 **base_task,
#                 "feedback": critique["improvements"]
#             }

#             attempt += 1

#         optimized_output = optimize_for_conversion(
#             best_output,
#             task["platform"],
#             best_critique
#         )

#         results.append({
#             "task_id": task["task_id"],
#             "platform": task["platform"],
#             "output": optimized_output
#         })

#     return results


def run_execution_pipeline(execution_plan: list, source: str):
    results = []

    for task in execution_plan:
        print(f"Executing task {task['task_id']} on {task['platform']}...")

        max_attempts = 3
        attempt = 0

        # ✅ include empty feedback from start
        base_task = {
            **task,
            "source": source,
            "feedback": []
        }

        best_output = None
        best_score = -1
        best_critique = None

        current_task = base_task
        accumulated_feedback = []   # 👈 NEW

        while attempt < max_attempts:
            output = execute_task(current_task, source)

            critique = critic_agent(current_task, output, source)

            print(f"Attempt {attempt+1} Score:", critique["score"])
            print("output: ", output, sep="\n")
            print("critique feedback: ", critique["improvements"], sep="\n")

            # ✅ track best output
            if critique["score"] > best_score:
                best_score = critique["score"]
                best_output = output
                best_critique = critique

            if critique["verdict"] == "approve":
                break

            # 🔥 accumulate feedback instead of overwriting
            accumulated_feedback.extend(critique["improvements"])

            # (optional but recommended) remove duplicates
            accumulated_feedback = list(set(accumulated_feedback))

            current_task = {
                **base_task,
                "feedback": accumulated_feedback
            }

            attempt += 1

        optimized_output = optimize_for_conversion(
            best_output,
            task["platform"],
            best_critique
        )

        results.append({
            "task_id": task["task_id"],
            "platform": task["platform"],
            "output": optimized_output
        })

    return results