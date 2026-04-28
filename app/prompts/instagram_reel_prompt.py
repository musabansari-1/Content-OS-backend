from app.prompts.execution_prompt import EXECUTION_PROMPT


INSTAGRAM_REEL_PROMPT = EXECUTION_PROMPT + """
PLATFORM: Instagram
ASSET_TYPE: Instagram Reel

FORMAT:
- 15-45 second reel script

STRUCTURE:
1. Hook in the first 2 seconds
2. Build emotional or practical tension
3. Deliver partial insight
4. CTA that pushes to the full video

RULES:
- Feel native to Instagram reels
- Keep sentences tight and voice-driven
- Use caption/overlay suggestions only when useful

OUTPUT:
{
  "hook": "...",
  "reel_script": "...",
  "caption": "...",
  "cta": "..."
}
"""
