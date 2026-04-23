import json
from app.utils.llm import call_llm
from app.prompts.critic_prompt import CRITIC_PROMPT

# def critic_agent(task, output):
#     user_prompt = f"""
# TASK:
# {json.dumps(task, indent=2)}

# OUTPUT:
# {json.dumps(output, indent=2)}
# """

#     response = call_llm(CRITIC_PROMPT, user_prompt)

#     try:
#         return json.loads(response)
#     except:
#         return {
#             "score": 0,
#             "verdict": "reject",
#             "issues": ["invalid_json_from_llm"],
#             "improvements": []
#         }

def critic_agent(task, output, source):
    user_prompt = f"""
TASK:
{json.dumps(task, indent=2)}

OUTPUT:
{json.dumps(output, indent=2)}

SOURCE:
{source}
"""

    response = call_llm(CRITIC_PROMPT, user_prompt)

    try:
        return json.loads(response)
    except:
        return {
            "score": 0,
            "verdict": "reject",
            "issues": ["invalid_json_from_llm"],
            "improvements": []
        }