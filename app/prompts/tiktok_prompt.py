# from app.prompts.execution_prompt import EXECUTION_PROMPT

# TIKTOK_PROMPT = EXECUTION_PROMPT + """
# PLATFORM: TikTok

# FORMAT:
# - 15–45 sec script

# STRUCTURE:
# 1. Hook (first 2 seconds MUST stop scroll)
# 2. Emotional / relatable build
# 3. Open loop (don’t fully resolve story)
# 4. CTA → send to YouTube

# CTA EXAMPLES:
# - "Full story is on my YouTube (link in bio)"
# - "I explain everything in my full video"

# OUTPUT:
# {
#   "hook": "...",
#   "video_script": "...",
#   "cta": "...",
#   "hashtags": ["..."]
# }
# """

from app.prompts.execution_prompt import EXECUTION_PROMPT


TIKTOK_PROMPT = EXECUTION_PROMPT + """
PLATFORM: TikTok

FORMAT:
- 15 to 45 second script

GOAL:
Make this feel native to TikTok: fast, conversational, emotionally immediate, and curiosity-driven.

STRUCTURE:
1. Hook
   - first line must stop scroll immediately
   - short, sharp, high-curiosity

2. Build
   - fast emotional or practical tension
   - conversational and spoken, not polished

3. Open loop
   - reveal enough to create interest
   - do not fully resolve

4. CTA
   - natural continuation to full video

RULES:
- Write for speech, not copy
- Keep phrasing conversational and immediate
- Short lines only
- Fast pacing
- Avoid polished “creator advice” tone
- Should feel native, reactive, and human
- Do not sound scripted or corporate

OUTPUT:
{
  "hook": "...",
  "video_script": "...",
  "cta": "...",
  "hashtags": ["..."]
}
"""