# from app.prompts.execution_prompt import EXECUTION_PROMPT


# BLOG_POST_PROMPT = EXECUTION_PROMPT + """
# PLATFORM: Blog
# ASSET_TYPE: Blog Post

# FORMAT:
# - 1 long-form article

# STRUCTURE:
# 1. Strong headline
# 2. Opening paragraph that creates tension or curiosity
# 3. Clear sub-sections with progression
# 4. Practical insight or lesson from the source story
# 5. Closing CTA to the full video

# RULES:
# - Preserve the creator's reasoning, not just their surface tone
# - Expand ideas thoughtfully without inventing new facts
# - Use scannable formatting with sections or subheads
# - Avoid sounding like generic SEO filler

# OUTPUT:
# {
#   "title": "...",
#   "subtitle": "...",
#   "sections": [
#     {
#       "heading": "...",
#       "body": "..."
#     }
#   ],
#   "cta": "..."
# }
# """


from app.prompts.execution_prompt import EXECUTION_PROMPT


BLOG_POST_PROMPT = EXECUTION_PROMPT + """
PLATFORM: Blog
ASSET_TYPE: Blog Post

FORMAT:
- 1 long-form article

STRUCTURE:
1. Strong headline
2. Opening paragraph:
   - start with a concrete detail, experience, or observation (not abstract claims)
   - avoid generic statements like “most people think…” unless grounded immediately in a real example

3. Clear sub-sections with progression:
   - each section should build on the previous one (no repetition)
   - prioritize depth over breadth (fewer, stronger sections instead of many shallow ones)

4. Practical insight or lesson:
   - deepen a single core idea from the source
   - avoid turning the content into a multi-step framework unless explicitly present in the source

5. Closing CTA to the full video:
   - natural and low-hype
   - should feel like a continuation, not a promotion
   - do not invent new claims

RULES:
- Preserve the creator's reasoning, not just their surface tone
- Preserve concrete details (numbers, timeframes, personal experience); do NOT generalize them away
- Expand ideas thoughtfully without inventing new facts
- Avoid generic “top 1%”, “99%”, or “average vs elite” framing unless explicitly central to the source
- Do NOT exaggerate outcomes or introduce hype (e.g., “changed everything”, “top 1%”) unless clearly stated
- Do NOT convert the insight into a checklist, system, or multiple tips unless the source does so
- Avoid over-explaining or repeating the same idea
- Use scannable formatting with meaningful sections (not filler)
- Avoid sounding like generic SEO or productivity content

OUTPUT:
{
  "title": "...",
  "subtitle": "...",
  "sections": [
    {
      "heading": "...",
      "body": "..."
    }
  ],
  "cta": "..."
}
"""