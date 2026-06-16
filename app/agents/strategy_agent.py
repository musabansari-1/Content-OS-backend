# agents/strategy_agent.py

import json

from app.prompts.strategy_prompt import STRATEGY_PROMPT
from app.utils.llm import call_llm


STRATEGY_BRIEF_FIELDS = (
    "source_moment",
    "evidence_quote",
    "emotional_angle",
    "open_loop",
    "cta_angle",
)


def _clean_string(value, fallback=""):
    if value is None:
        return fallback

    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)

    text = str(value).strip()
    return text or fallback


def _clean_list(value):
    if value is None:
        return []

    if isinstance(value, str):
        value = [value]

    if not isinstance(value, list):
        return []

    cleaned = []
    for item in value:
        text = _clean_string(item)
        if text and text not in cleaned:
            cleaned.append(text)

    return cleaned


def _build_strategy_brief(task, fallback_input):
    task = task or {}

    brief = {
        field: _clean_string(task.get(field))
        for field in STRATEGY_BRIEF_FIELDS
    }
    brief["must_use_details"] = _clean_list(task.get("must_use_details"))
    brief["must_avoid_claims"] = _clean_list(task.get("must_avoid_claims"))

    if not brief["source_moment"]:
        brief["source_moment"] = fallback_input

    if not brief["open_loop"]:
        brief["open_loop"] = "Leave one source-specific question unresolved."

    if not brief["cta_angle"]:
        brief["cta_angle"] = "Continue naturally into the full video."

    return brief


def _normalize_execution_plan(raw_response, input_data):
    try:
        parsed = json.loads(raw_response)
    except Exception:
        parsed = {}

    raw_plan = parsed.get("execution_plan", [])
    if not isinstance(raw_plan, list):
        raw_plan = []

    catalog_by_asset = {
        asset["asset_type"]: asset for asset in input_data.get("asset_catalog", [])
    }
    target_assets = input_data.get("target_assets", [])

    normalized_plan = []
    for asset_type in target_assets:
        asset_meta = catalog_by_asset.get(asset_type, {})
        matching_task = next(
            (
                task
                for task in raw_plan
                if isinstance(task, dict) and task.get("asset_type") == asset_type
            ),
            None,
        )

        if matching_task:
            task_input = _clean_string(
                matching_task.get("input"),
                f"Best source-grounded angle for {asset_type.replace('_', ' ')}",
            )

            normalized_plan.append(
                {
                    "task_id": len(normalized_plan) + 1,
                    "asset_type": asset_type,
                    "platform": asset_meta.get("platform", matching_task.get("platform", "")),
                    "format": asset_meta.get("format", matching_task.get("format", "")),
                    "input": task_input,
                    "output_type": asset_meta.get(
                        "output_type",
                        matching_task.get("output_type", ""),
                    ),
                    "goal": _clean_string(
                        matching_task.get("goal"),
                        f"Create the strongest {asset_type.replace('_', ' ')} from the source",
                    ),
                    "priority": len(normalized_plan) + 1,
                    "depends_on": [],
                    "strategy_brief": _build_strategy_brief(matching_task, task_input),
                }
            )
            continue

        fallback_input = f"Best source-grounded angle for {asset_type.replace('_', ' ')}"
        normalized_plan.append(
            {
                "task_id": len(normalized_plan) + 1,
                "asset_type": asset_type,
                "platform": asset_meta.get("platform", ""),
                "format": asset_meta.get("format", ""),
                "input": fallback_input,
                "output_type": asset_meta.get("output_type", ""),
                "goal": f"Create the strongest {asset_type.replace('_', ' ')} from the source",
                "priority": len(normalized_plan) + 1,
                "depends_on": [],
                "strategy_brief": _build_strategy_brief({}, fallback_input),
            }
        )

    return {"execution_plan": normalized_plan}


def generate_strategy(input_data):
    response = call_llm(
        STRATEGY_PROMPT,
        json.dumps(input_data),
        stage="strategy",
    )

    return json.dumps(_normalize_execution_plan(response, input_data))
