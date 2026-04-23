from app.prompts.execution_prompt import EXECUTION_PROMPT

TIKTOK_PROMPT = EXECUTION_PROMPT + """
PLATFORM: TikTok

FORMAT:
- 15–45 sec script

STRUCTURE:
1. Hook (first 2 seconds MUST stop scroll)
2. Emotional / relatable build
3. Open loop (don’t fully resolve story)
4. CTA → send to YouTube

CTA EXAMPLES:
- "Full story is on my YouTube (link in bio)"
- "I explain everything in my full video"

OUTPUT:
{
  "hook": "...",
  "video_script": "...",
  "cta": "...",
  "hashtags": ["..."]
}
"""