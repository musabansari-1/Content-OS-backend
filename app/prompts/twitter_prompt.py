from app.prompts.execution_prompt import EXECUTION_PROMPT

TWITTER_PROMPT = EXECUTION_PROMPT + """
PLATFORM: Twitter

FORMAT:
- Thread (5–7 tweets)

RULES:
- Tweet 1 = STRONG HOOK (controversial or emotional)
- Tweets 2–5 = build tension + insight
- Tweet 6–7 = CTA + link to full video

CTA EXAMPLES:
- "I documented the full story here → [link]"
- "Watch the full breakdown → [link]"

ENGAGEMENT:
- Include 1–2 questions to trigger replies

OUTPUT:
{
  "tweets": ["...", "..."]
}
"""