from app.prompts.execution_prompt import EXECUTION_PROMPT


X_POST_PROMPT = EXECUTION_PROMPT + """
PLATFORM: X

FORMAT:
- 1 single X post
- Keep the post publishable within 280 characters for broad account compatibility.

GOAL:
Create one native X post that feels sharp, specific, and reply-worthy while staying grounded in the source.

STRUCTURE:
1. Post
   - start with a concrete hook from the source
   - make one clear point or tension
   - preserve the creator's voice and reasoning style
   - leave one unresolved curiosity gap
   - include a natural CTA only if it fits inside the post without making it feel like an ad

2. Reply prompt
   - one short question or observation that can naturally invite replies
   - must be grounded in the same source detail or tension

3. CTA
   - a short, non-salesy continuation to the full video
   - do not invent a URL
   - use only if it fits the post naturally

RULES:
- The post must be 280 characters or fewer.
- Do not create a thread.
- Do not use fake statistics, fake outcomes, fake companies, or invented scenarios.
- Do not turn weak source details into certainty.
- Do not use hashtags unless the source clearly depends on a named topic where one hashtag would feel natural.
- Avoid generic X clichés, vague contrarian takes, and engagement bait.
- Prefer one precise source detail over broad advice.
- Keep line breaks minimal; use them only if they improve readability.
- The post should feel like a real creator thought, not a repackaged headline.

SELF-CHECK BEFORE OUTPUT:
- Is the post 280 characters or fewer?
- Is every claim traceable to the source?
- Does it sound like the creator, not a generic viral account?
- Does it work as a standalone X post instead of a thread opener?
- Is there a specific open loop tied to the source?

OUTPUT:
{
  "post": "...",
  "reply_prompt": "...",
  "cta": "..."
}
"""
