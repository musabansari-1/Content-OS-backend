from app.prompts.execution_prompt import EXECUTION_PROMPT


INSTAGRAM_CAROUSEL_PROMPT = EXECUTION_PROMPT + """
PLATFORM: Instagram
ASSET_TYPE: Instagram Carousel

FORMAT:
- 6-8 slide carousel

STRUCTURE:
1. Slide 1 = bold hook title
2. Slides 2-6 = escalating story, lesson, or breakdown
3. Final slide = concise takeaway + CTA to full video

RULES:
- Keep each slide compact and skimmable
- Build progression across slides
- Use tension and payoff, not generic listicle energy
- Caption should support the carousel without repeating every slide

OUTPUT:
{
  "slides": ["...", "..."],
  "caption": "...",
  "cta": "..."
}
"""
