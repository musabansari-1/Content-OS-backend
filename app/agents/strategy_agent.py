# agents/strategy_agent.py

import json

from app.prompts.strategy_prompt import STRATEGY_PROMPT
from app.utils.llm import call_llm


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
            normalized_plan.append(
                {
                    "task_id": len(normalized_plan) + 1,
                    "asset_type": asset_type,
                    "platform": asset_meta.get("platform", matching_task.get("platform", "")),
                    "format": asset_meta.get("format", matching_task.get("format", "")),
                    "input": (matching_task.get("input") or "").strip(),
                    "output_type": asset_meta.get(
                        "output_type",
                        matching_task.get("output_type", ""),
                    ),
                    "goal": (matching_task.get("goal") or "").strip(),
                    "priority": len(normalized_plan) + 1,
                    "depends_on": [],
                }
            )
            continue

        normalized_plan.append(
            {
                "task_id": len(normalized_plan) + 1,
                "asset_type": asset_type,
                "platform": asset_meta.get("platform", ""),
                "format": asset_meta.get("format", ""),
                "input": f"Best source-grounded angle for {asset_type.replace('_', ' ')}",
                "output_type": asset_meta.get("output_type", ""),
                "goal": f"Create the strongest {asset_type.replace('_', ' ')} from the source",
                "priority": len(normalized_plan) + 1,
                "depends_on": [],
            }
        )

    return {"execution_plan": normalized_plan}


def generate_strategy(input_data):
    response = call_llm(
        STRATEGY_PROMPT,
        json.dumps(input_data),
    )

    return json.dumps(_normalize_execution_plan(response, input_data))
