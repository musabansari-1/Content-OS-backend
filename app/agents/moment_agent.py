from app.utils.llm import call_llm
from app.prompts.moment_prompt import MOMENT_PROMPT

def extract_moments(transcript):
    return call_llm(MOMENT_PROMPT, transcript, stage="moments")
