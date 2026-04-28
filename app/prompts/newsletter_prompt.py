from app.prompts.execution_prompt import EXECUTION_PROMPT


NEWSLETTER_PROMPT = EXECUTION_PROMPT + """
PLATFORM: Email
ASSET_TYPE: Newsletter

FORMAT:
- 1 newsletter edition

STRUCTURE:
1. Strong subject line
2. Opening that feels personal and voice-driven
3. Main lesson or story progression
4. Clear takeaway
5. CTA to the full video

RULES:
- Feel like a real creator newsletter, not a polished corporate email
- Preserve narrative flow and identity markers
- Use natural transitions and readable paragraphing
- Keep the CTA aligned with curiosity, not hard selling

OUTPUT:
{
  "subject_line": "...",
  "preview_text": "...",
  "body": "...",
  "cta": "..."
}
"""
