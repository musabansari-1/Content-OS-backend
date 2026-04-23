# agents/strategy_agent.py

from app.utils.llm import call_llm
from app.prompts.strategy_prompt import STRATEGY_PROMPT

def generate_strategy(transcript):
    return call_llm(STRATEGY_PROMPT, transcript)