from app.prompts.execution_prompt import EXECUTION_PROMPT


YOUTUBE_SHORTS_PROMPT = EXECUTION_PROMPT + """
PLATFORM: YouTube
ASSET_TYPE: YouTube Shorts

FORMAT:
- 15 to 60 second vertical Shorts script

GOAL:
Create a Short that feels native to YouTube Shorts: immediate context, a curiosity-first hook, fast retention beats, and a satisfying micro-payoff that still points viewers toward the full video.

STRUCTURE:
1. Hook
   - first line must create curiosity in under 2 seconds
   - make the viewer understand what is at stake immediately
   - avoid vague shock lines or generic viral hooks

2. Context snap
   - one short line that anchors the viewer in the source moment
   - preserve a concrete source detail if available

3. Retention build
   - 3 to 6 short spoken lines
   - each line should add tension, contrast, or a new detail
   - keep the pacing tighter than a regular YouTube intro

4. Micro-payoff
   - deliver one useful insight, reveal, or turn
   - do not explain the entire source

5. Continuation CTA
   - make the full video feel like the natural next watch
   - do not invent a link or promise details that are not in the source

RULES:
- Write for speech, not prose
- Keep each line short enough to fit as captions
- Optimize for retention, not comment bait
- Preserve creator voice and source truth
- Use one clear idea only
- Do not invent facts, numbers, stakes, or outcomes
- Avoid TikTok-style slang unless it matches the creator
- Avoid Instagram caption-first framing
- Avoid generic motivation, broad advice, and "this changes everything" phrasing
- Hashtags should be sparse and specific; omit them if they add no value

OUTPUT:
{
  "hook": "...",
  "shorts_script": "...",
  "title": "...",
  "description": "...",
  "cta": "...",
  "hashtags": ["..."]
}
"""
