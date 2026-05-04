# from app.prompts.execution_prompt import EXECUTION_PROMPT


# NEWSLETTER_PROMPT = EXECUTION_PROMPT + """
# PLATFORM: Email
# ASSET_TYPE: Newsletter

# FORMAT:
# - 1 newsletter edition

# STRUCTURE:
# 1. Strong subject line
# 2. Opening that feels personal and voice-driven
# 3. Main lesson or story progression
# 4. Clear takeaway
# 5. CTA to the full video

# RULES:
# - Feel like a real creator newsletter, not a polished corporate email
# - Preserve narrative flow and identity markers
# - Use natural transitions and readable paragraphing
# - Keep the CTA aligned with curiosity, not hard selling

# OUTPUT:
# {
#   "subject_line": "...",
#   "preview_text": "...",
#   "body": "...",
#   "cta": "..."
# }
# """


from app.prompts.execution_prompt import EXECUTION_PROMPT


NEWSLETTER_PROMPT = EXECUTION_PROMPT + """
PLATFORM: Email
ASSET_TYPE: Newsletter

FORMAT:
- 1 creator-style newsletter edition

GOAL:
Make this feel like a real creator email written to an audience, not a polished brand email.

STRUCTURE:
1. Subject line
   - specific, curiosity-driven, human
   - avoid corporate newsletter phrasing

2. Preview text
   - extend curiosity naturally

3. Opening
   - personal, direct, conversational
   - should feel like the creator is writing to one person

4. Body
   - one clear story / lesson progression
   - natural transitions
   - readable, conversational paragraphs
   - no corporate polish

5. Closing
   - clear takeaway
   - soft CTA to full video

RULES:
- Should feel personal, not branded
- Should feel written, not marketed
- Preserve creator voice and narrative habits
- Avoid corporate polish or over-structured thought leadership tone
- Prioritize intimacy, clarity, and narrative flow
- CTA should feel like a natural continuation

OUTPUT:
{
  "subject_line": "...",
  "preview_text": "...",
  "body": "...",
  "cta": "..."
}
"""
