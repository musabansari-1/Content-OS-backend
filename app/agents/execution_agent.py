# agents/content_agent.py

import json
from typing import Optional

from app.agents.critic_agent import critic_agent
from app.assets import AVAILABLE_TARGET_ASSETS
from app.prompts.blog_post_prompt import BLOG_POST_PROMPT
from app.prompts.conversion_prompt import CONVERSION_PROMPT
from app.prompts.execution_prompt import build_execution_user_prompt
from app.prompts.instagram_carousel_prompt import INSTAGRAM_CAROUSEL_PROMPT
from app.prompts.instagram_reel_prompt import INSTAGRAM_REEL_PROMPT
from app.prompts.linkedin_prompt import LINKEDIN_PROMPT
from app.prompts.newsletter_prompt import NEWSLETTER_PROMPT
from app.prompts.tiktok_prompt import TIKTOK_PROMPT
from app.prompts.twitter_prompt import TWITTER_PROMPT
from app.prompts.youtube_prompt import YOUTUBE_PROMPT
from app.utils.llm import call_llm
from app.voice_engine.service import CreatorVoiceProfileService


def optimize_for_conversion(output, platform, critique):
    payload = json.dumps(
        {
            "output": output,
            "platform": platform,
            "issues": critique.get("issues", []),
            "improvements": critique.get("improvements", []),
        }
    )

    return call_llm(CONVERSION_PROMPT, payload)


def parse_llm_output(response):
    try:
        return json.loads(response)
    except Exception:
        return {
            "error": "invalid_json",
            "raw": response,
        }


def generate_content(strategy):
    source = strategy.get("source", "")
    return build_execution_user_prompt(strategy, source)


def _voice_profile_to_dict(voice_profile) -> Optional[dict]:
    if not voice_profile:
        return None

    if hasattr(voice_profile, "model_dump"):
        return voice_profile.model_dump()

    return voice_profile.dict()


def execute_task(task, source, creator_voice_profile=None):
    asset_type = task["asset_type"]
    user_prompt = build_execution_user_prompt(task, source, creator_voice_profile)

    if asset_type == "twitter_thread":
        return call_llm(TWITTER_PROMPT, user_prompt)

    if asset_type == "tiktok_clip":
        return call_llm(TIKTOK_PROMPT, user_prompt)

    if asset_type == "linkedin_post":
        return call_llm(LINKEDIN_PROMPT, user_prompt)

    if asset_type == "youtube_video_idea":
        return call_llm(YOUTUBE_PROMPT, user_prompt)

    if asset_type == "instagram_carousel":
        return call_llm(INSTAGRAM_CAROUSEL_PROMPT, user_prompt)

    if asset_type == "instagram_reel":
        return call_llm(INSTAGRAM_REEL_PROMPT, user_prompt)

    if asset_type == "blog_post":
        return call_llm(BLOG_POST_PROMPT, user_prompt)

    if asset_type == "newsletter":
        return call_llm(NEWSLETTER_PROMPT, user_prompt)

    raise ValueError(f"Unsupported asset type: {asset_type}")


def run_execution_pipeline(
    execution_plan: list,
    source: str,
    user_id=None,
    creator_voice_profile_service=None,
):
    results = []
    voice_profile_record = None

    if user_id is not None:
        profile_service = creator_voice_profile_service or CreatorVoiceProfileService()
        voice_profile_record = profile_service.getVoiceProfile(user_id)

    creator_voice_profile = None
    if voice_profile_record:
        creator_voice_profile = _voice_profile_to_dict(
            voice_profile_record.voice_profile_json
        )
        creator_voice_profile["profile_version"] = voice_profile_record.version

    # v2 plug-in point:
    # style retrieval can enrich the persisted base profile with request-specific
    # snippets before execution, without changing storage or extraction contracts.

    for task in execution_plan:
        if task.get("asset_type") not in AVAILABLE_TARGET_ASSETS:
            raise ValueError(
                f"Unsupported asset type in execution plan: {task.get('asset_type')}"
            )

        print(f"Executing task {task['task_id']} on {task['asset_type']}...")

        max_attempts = 3
        attempt = 0

        base_task = {
            **task,
            "source": source,
            "feedback": [],
        }

        best_output = None
        best_score = -1
        best_critique = None

        current_task = base_task
        accumulated_feedback = []

        while attempt < max_attempts:
            output = execute_task(current_task, source, creator_voice_profile)
            critique = critic_agent(current_task, output, source)

            print(f"Attempt {attempt + 1} Score:", critique["score"])
            print("output: ", output, sep="\n")
            print("critique feedback: ", critique["improvements"], sep="\n")

            if critique["score"] > best_score:
                best_score = critique["score"]
                best_output = output
                best_critique = critique

            if critique["verdict"] == "approve":
                break

            accumulated_feedback.extend(critique["improvements"])
            accumulated_feedback = list(dict.fromkeys(accumulated_feedback))

            current_task = {
                **base_task,
                "feedback": accumulated_feedback,
            }

            attempt += 1

        optimized_output = optimize_for_conversion(
            best_output,
            task["platform"],
            best_critique,
        )

        results.append(
            {
                "task_id": task["task_id"],
                "asset_type": task["asset_type"],
                "platform": task["platform"],
                "output": optimized_output,
            }
        )

    return results
