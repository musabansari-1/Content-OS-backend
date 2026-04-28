from app.prompts.execution_prompt import EXECUTION_PROMPT


BLOG_POST_PROMPT = EXECUTION_PROMPT + """
PLATFORM: Blog
ASSET_TYPE: Blog Post

FORMAT:
- 1 long-form article

STRUCTURE:
1. Strong headline
2. Opening paragraph that creates tension or curiosity
3. Clear sub-sections with progression
4. Practical insight or lesson from the source story
5. Closing CTA to the full video

RULES:
- Preserve the creator's reasoning, not just their surface tone
- Expand ideas thoughtfully without inventing new facts
- Use scannable formatting with sections or subheads
- Avoid sounding like generic SEO filler

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
