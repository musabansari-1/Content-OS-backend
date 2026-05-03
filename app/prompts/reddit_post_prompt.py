# # from app.prompts.execution_prompt import EXECUTION_PROMPT


# # REDDIT_POST_PROMPT = EXECUTION_PROMPT + """
# # PLATFORM: Reddit

# # FORMAT:
# # - 1 text post: title + body (suitable for a discussion-style subreddit)

# # STRUCTURE:
# # 1. Title: specific, curiosity-driven, not clickbait spam (no excessive caps or emoji spam)
# # 2. Opening line that hooks readers in plain, human language
# # 3. Short context from the source (concrete details, not generic setup)
# # 4. Insight or tension — leave an open loop so readers want the full story
# # 5. Natural CTA to watch the full video (e.g. "Full breakdown in the video — link in comments" style wording; do not invent a URL)

# # TONE:
# # - conversational and authentic (Reddit-native, not corporate)
# # - respectful of community norms: no astroturfing, no fake "I just found this" framing
# # - first person when it fits the creator voice

# # RULES:
# # - Body should be scannable: short paragraphs, optional bullet list only if it genuinely helps
# # - Do not name a specific subreddit unless the source names one
# # - Do not fabricate AMA or crosspost framing

# # OUTPUT:
# # {
# #   "title": "...",
# #   "body": "..."
# # }
# # """



# from app.prompts.execution_prompt import EXECUTION_PROMPT


# REDDIT_POST_PROMPT = EXECUTION_PROMPT + """
# PLATFORM: Reddit

# FORMAT:
# - 1 text post: title + body (suitable for a discussion-style subreddit)

# STRUCTURE:
# 1. Title: specific, curiosity-driven, not clickbait spam (no excessive caps or emoji spam)
# 2. Opening line that hooks readers using a concrete or specific statement (not vague motivation)
# 3. Short context grounded in real details (numbers, habits, or firsthand experience)
# 4. Insight or tension — highlight a clear idea or contrarian takeaway from the source and leave an open loop
# 5. Natural CTA to watch the full video:
#    - keep it casual and non-salesy
#    - should feel like a continuation, not a promotion
#    - do not invent a URL

# TONE:
# - conversational and authentic (Reddit-native, not corporate)
# - respectful of community norms: no astroturfing, no fake "I just found this" framing
# - first person when it fits the creator voice

# RULES:
# - Preserve at least 1–2 concrete, specific details from the source (e.g., numbers, timeframes, personal experience, company/context). Do NOT generalize these away.
# - Identify and retain the core unique insight or contrarian idea from the source. Avoid replacing it with generic advice.
# - Avoid generic motivational phrasing (e.g., "do what others aren't doing", "stand out from 99%"). If a line could apply to any topic, rewrite it to be more specific.
# - Do not start with abstract or philosophical claims. Lead with something concrete before any generalization.
# - Body should be scannable: short paragraphs, optional bullet list only if it genuinely helps
# - Do not name a specific subreddit unless the source names one
# - Do not fabricate AMA or crosspost framing

# SELF-CHECK BEFORE OUTPUT:
# - Does the post include at least one concrete detail (number, timeframe, or specific habit)?
# - Could this post apply to any topic? If yes, make it more specific.
# - Is the hook based on something real, not generic motivation?

# OUTPUT:
# {
#   "title": "...",
#   "body": "..."
# }
# """


from app.prompts.execution_prompt import EXECUTION_PROMPT


REDDIT_POST_PROMPT = EXECUTION_PROMPT + """
PLATFORM: Reddit

FORMAT:
- 1 text post: title + body (suitable for a discussion-style subreddit)

STRUCTURE:
1. Title:
   - specific and curiosity-driven
   - prefer personal observation or statement over question-based framing
   - avoid generic “should you / how to” phrasing unless strongly justified
   - not reusable across topics
   - no excessive caps or emoji spam

2. Opening line:
   - hook readers using a concrete or specific statement (not vague motivation)
   - avoid abstract or philosophical openings

3. Context:
   - grounded in real details (numbers, habits, or firsthand experience)
   - preserve specificity from the source

4. Insight or tension:
   - highlight a clear idea or contrarian takeaway from the source
   - introduce a specific incomplete element (e.g., “a few things surprised me”, “one pattern changed everything”) that is NOT fully explained in the post
   - create a genuine open loop so readers feel there is more to learn

5. Natural CTA to watch the full video:
   - must be included
   - keep it casual and non-salesy
   - should feel like a continuation, not a promotion
   - use Reddit-native phrasing (e.g., “Full breakdown in the video — link in comments”)
   - do not invent a URL

TONE:
- conversational and authentic (Reddit-native, not corporate)
- respectful of community norms: no astroturfing, no fake "I just found this" framing
- first person when it fits the creator voice

RULES:
- Preserve at least 1–2 concrete, specific details from the source (e.g., numbers, timeframes, personal experience, company/context). Do NOT generalize these away.
- Identify and retain the core unique insight or contrarian idea from the source. Avoid replacing it with generic advice.
- Avoid generic motivational phrasing (e.g., "do what others aren't doing", "stand out from 99%"). If a line could apply to any topic, rewrite it to be more specific.
- Do not start with abstract or philosophical claims. Lead with something concrete before any generalization.
- Body should be scannable: short paragraphs, optional bullet list only if it genuinely helps
- Do not name a specific subreddit unless the source names one
- Do not fabricate AMA or crosspost framing

SELF-CHECK BEFORE OUTPUT:
- Does the post include at least one concrete detail (number, timeframe, or specific habit)?
- Are 1–2 key specifics from the source clearly preserved?
- Could this post apply to any topic? If yes, make it more specific.
- Is the hook based on something real, not generic motivation?
- Is there a clear open loop (something intentionally not fully explained)? If not, revise.
- Is the title phrased like a real Reddit post (not a blog/YouTube headline)?
- Is the CTA present, natural, and non-salesy?

OUTPUT:
{
  "title": "...",
  "body": "..."
}
"""
