# # from app.prompts.execution_prompt import EXECUTION_PROMPT


# # INSTAGRAM_REEL_PROMPT = EXECUTION_PROMPT + """
# # PLATFORM: Instagram
# # ASSET_TYPE: Instagram Reel

# # FORMAT:
# # - 15-45 second reel script

# # STRUCTURE:
# # 1. Hook in the first 2 seconds
# # 2. Build emotional or practical tension
# # 3. Deliver partial insight
# # 4. CTA that pushes to the full video

# # RULES:
# # - Feel native to Instagram reels
# # - Keep sentences tight and voice-driven
# # - Use caption/overlay suggestions only when useful

# # OUTPUT:
# # {
# #   "hook": "...",
# #   "reel_script": "...",
# #   "caption": "...",
# #   "cta": "..."
# # }
# # """


# from app.prompts.execution_prompt import EXECUTION_PROMPT


# INSTAGRAM_REEL_PROMPT = EXECUTION_PROMPT + """
# PLATFORM: Instagram
# ASSET_TYPE: Instagram Reel

# FORMAT:
# - 15-45 second reel script

# STRUCTURE:
# 1. Hook in the first 2 seconds (clear, specific, not generic)
# 2. Build tension using a concrete detail or real experience
# 3. Deliver partial insight (focus on ONE core idea)
# 4. Open loop + CTA:
#    - introduce a specific unresolved detail or outcome (not vague promises)
#    - CTA should feel like a natural continuation (e.g., “I break that down in the full video”)

# RULES:
# - Preserve concrete details from the source (numbers, timeframes, real context); do NOT invent or weaken them
# - Focus on ONE core idea; do not introduce multiple tips or habits
# - Avoid generic content phrases (e.g., “this changes everything”, “real learning happens”)
# - Prefer concrete, experience-based wording
# - Do not introduce arbitrary or low-credibility habits (e.g., unrealistic timeframes)
# - Keep sentences tight and voice-driven
# - Feel native to Instagram reels

# CAPTION:
# - Should add a new angle, reflection, or question
# - Do NOT simply restate the reel

# SELF-CHECK BEFORE OUTPUT:
# - Is at least one concrete detail preserved?
# - Does the script sound specific and real (not generic)?
# - Is the open loop specific and curiosity-driven?
# - Could this apply to any topic? If yes, make it more specific.
# - Does the caption add something new?

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
- 15 to 45 second reel script

GOAL:
Create a reel that feels native to Instagram: fast to consume, visually punchy, spoken-first, and built to hold attention through the final line.

STRUCTURE:
1. Hook
   - first line must stop the scroll immediately
   - short, clear, high-curiosity
   - no vague motivational hooks

2. Build
   - introduce one concrete tension, detail, or moment from the source
   - keep progression tight and linear
   - each line should increase curiosity or clarity

3. Partial payoff
   - reveal one useful insight
   - do not over-explain
   - focus on one idea only

4. Open loop + CTA
   - leave one specific unresolved detail, outcome, or implication
   - CTA should feel like a natural continuation to the full video

RULES:
- Write for speech, not prose
- Every line should sound natural when spoken aloud
- Keep sentences short and easy to say
- Use line breaks to reflect spoken pacing
- Focus on one idea only
- Preserve concrete source details (numbers, timelines, specific examples)
- Do not invent details
- Do not sound scripted, polished, or corporate
- Avoid generic “creator advice” phrasing
- Avoid dense wording that reads well but sounds unnatural aloud
- Should feel visual, spoken, and native to reels

CAPTION:
- extend the idea with a reflection, tension, or question
- do not restate the reel
- should add a second reason to engage

SELF-CHECK BEFORE OUTPUT:
- Is the hook specific enough to stop scroll?
- Does the script sound natural when spoken aloud?
- Is there at least one preserved concrete detail?
- Does each line push curiosity forward?
- Is only one idea being explored?
- Does the caption add something new?

OUTPUT:
{
  "hook": "...",
  "reel_script": "...",
  "caption": "...",
  "cta": "..."
}
"""