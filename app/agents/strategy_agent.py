# agents/strategy_agent.py

from app.utils.llm import call_llm
from app.prompts.strategy_prompt import STRATEGY_PROMPT

import json

def generate_strategy(input_data):

    response = call_llm(
        STRATEGY_PROMPT,
        json.dumps(input_data)
    )

    return response  # parsing happens HERE