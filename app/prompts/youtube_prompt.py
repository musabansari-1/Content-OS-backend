from app.prompts.execution_prompt import EXECUTION_PROMPT


YOUTUBE_PROMPT = EXECUTION_PROMPT + """
PLATFORM: YouTube

FORMAT:
- Video concept + title + hook

STRUCTURE:
1. Title (high curiosity)
2. Thumbnail text idea
3. Opening hook (first 10 sec)
4. Story outline
5. Retention strategy

GOAL:
- maximize CTR + retention

OUTPUT:
{
  "title": "...",
  "thumbnail_text": "...",
  "hook": "...",
  "outline": ["...", "..."]
}
"""