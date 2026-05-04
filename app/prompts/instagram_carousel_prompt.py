# from app.prompts.execution_prompt import EXECUTION_PROMPT


# INSTAGRAM_CAROUSEL_PROMPT = EXECUTION_PROMPT + """
# PLATFORM: Instagram
# ASSET_TYPE: Instagram Carousel

# FORMAT:
# - 6-8 slide carousel

# STRUCTURE:
# 1. Slide 1 = bold hook title
# 2. Slides 2-6 = escalating story, lesson, or breakdown
# 3. Final slide = concise takeaway + CTA to full video

# RULES:
# - Keep each slide compact and skimmable
# - Build progression across slides
# - Use tension and payoff, not generic listicle energy
# - Caption should support the carousel without repeating every slide

# OUTPUT:
# {
#   "slides": ["...", "..."],
#   "caption": "...",
#   "cta": "..."
# }
# """

from app.prompts.execution_prompt import EXECUTION_PROMPT


INSTAGRAM_CAROUSEL_PROMPT = EXECUTION_PROMPT + """
PLATFORM: Instagram
ASSET_TYPE: Instagram Carousel

FORMAT:
- 6 to 8 slides

GOAL:
Create a swipe-worthy carousel where each slide earns the next swipe.

STRUCTURE:
1. Slide 1
   - bold, high-clarity hook
   - must create immediate curiosity or tension
   - no vague motivational hooks

2. Slides 2 to 6
   - each slide advances the idea
   - one idea per slide
   - short, punchy, highly skimmable
   - each slide should create momentum into the next

3. Final slide
   - concise takeaway
   - soft CTA to full video

RULES:
- Each slide should feel visually scannable
- Keep text compact and high-signal
- One idea per slide only
- Build narrative progression, not listicle bullets
- Use tension + payoff, not generic educational filler
- Avoid over-explaining
- Avoid repeating the same point across slides
- Caption should extend the idea, not restate slides

OUTPUT:
{
  "slides": ["...", "..."],
  "caption": "...",
  "cta": "..."
}
"""
