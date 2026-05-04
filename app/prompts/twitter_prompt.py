# from app.prompts.execution_prompt import EXECUTION_PROMPT

# TWITTER_PROMPT = EXECUTION_PROMPT + """
# PLATFORM: Twitter

# FORMAT:
# - Thread (5–7 tweets)

# RULES:
# - Tweet 1 = STRONG HOOK (controversial or emotional)
# - Tweets 2–5 = build tension + insight
# - Tweet 6–7 = CTA + link to full video

# CTA EXAMPLES:
# - "I documented the full story here → [link]"
# - "Watch the full breakdown → [link]"

# ENGAGEMENT:
# - Include 1–2 questions to trigger replies

# OUTPUT:
# {
#   "tweets": ["...", "..."]
# }
# """

from app.prompts.execution_prompt import EXECUTION_PROMPT


TWITTER_PROMPT = EXECUTION_PROMPT + """
PLATFORM: Twitter

FORMAT:
- Thread (5 to 7 tweets)

GOAL:
Create a thread that feels native to Twitter: punchy, high-signal, curiosity-driven, and built for replies.

STRUCTURE:
1. Tweet 1
   - sharp hook
   - strong opinion, tension, or curiosity
   - must stand alone and earn the click

2. Tweets 2 to 5
   - each tweet adds one meaningful layer
   - short, punchy, high-signal
   - build tension and insight progressively

3. Final tweet
   - natural CTA to full video
   - should feel like continuation, not promotion

RULES:
- Each tweet must stand alone and pull forward
- Keep tweets tight and high-signal
- Avoid long blocks
- Avoid generic thread clichés
- Include 1 to 2 natural reply triggers
- Thread should feel like real thinking, not formatted content

OUTPUT:
{
  "tweets": ["...", "..."]
}
"""