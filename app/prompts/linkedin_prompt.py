from app.prompts.execution_prompt import EXECUTION_PROMPT


LINKEDIN_PROMPT = EXECUTION_PROMPT + """
PLATFORM: LinkedIn

FORMAT:
- 1 post (storytelling + insight)

STRUCTURE:
1. Strong personal or professional hook
2. Insight / lesson
3. Industry relevance
4. CTA to full video

CTA EXAMPLES:
- "I break this down fully here → [link]"
- "Watch the full story → [link]"

TONE:
- thoughtful
- credible
- not clickbait

OUTPUT:
{
  "post": "..."
}
"""