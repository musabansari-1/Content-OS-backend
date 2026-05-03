# from app.prompts.execution_prompt import EXECUTION_PROMPT


# INSTAGRAM_REEL_PROMPT = EXECUTION_PROMPT + """
# PLATFORM: Instagram
# ASSET_TYPE: Instagram Reel

# FORMAT:
# - 15-45 second reel script

# STRUCTURE:
# 1. Hook in the first 2 seconds
# 2. Build emotional or practical tension
# 3. Deliver partial insight
# 4. CTA that pushes to the full video

# RULES:
# - Feel native to Instagram reels
# - Keep sentences tight and voice-driven
# - Use caption/overlay suggestions only when useful

# OUTPUT:
# {
#   "hook": "...",
#   "reel_script": "...",
#   "caption": "...",
#   "cta": "..."
# }
# """


from app.prompts.execution_prompt import EXECUTION_PROMPT


INSTAGRAM_REEL_PROMPT = EXECUTION_PROMPT + """
PLATFORM: Instagram
ASSET_TYPE: Instagram Reel

FORMAT:
- 15-45 second reel script

STRUCTURE:
1. Hook in the first 2 seconds (clear, specific, not generic)
2. Build tension using a concrete detail or real experience
3. Deliver partial insight (focus on ONE core idea)
4. Open loop + CTA:
   - introduce a specific unresolved detail or outcome (not vague promises)
   - CTA should feel like a natural continuation (e.g., “I break that down in the full video”)

RULES:
- Preserve concrete details from the source (numbers, timeframes, real context); do NOT invent or weaken them
- Focus on ONE core idea; do not introduce multiple tips or habits
- Avoid generic content phrases (e.g., “this changes everything”, “real learning happens”)
- Prefer concrete, experience-based wording
- Do not introduce arbitrary or low-credibility habits (e.g., unrealistic timeframes)
- Keep sentences tight and voice-driven
- Feel native to Instagram reels

CAPTION:
- Should add a new angle, reflection, or question
- Do NOT simply restate the reel

SELF-CHECK BEFORE OUTPUT:
- Is at least one concrete detail preserved?
- Does the script sound specific and real (not generic)?
- Is the open loop specific and curiosity-driven?
- Could this apply to any topic? If yes, make it more specific.
- Does the caption add something new?

OUTPUT:
{
  "hook": "...",
  "reel_script": "...",
  "caption": "...",
  "cta": "..."
}
"""