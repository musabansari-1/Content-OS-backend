from __future__ import annotations

import json
from typing import Any


class LLMMomentScorer:
    """
    Scores candidate clip windows with an LLM.

    Expected candidate shape:
    {
        "clip_id": "...",
        "start": 12.34,
        "end": 36.90,
        "duration": 24.56,
        "score": 0.71,   # heuristic score from your current pipeline
        "title": "...",
        "rationale": "...",
        "transcript_text": "..."
    }
    """

    SYSTEM_PROMPT = """
You are a senior short-form video editor for TikTok, Instagram Reels, and YouTube Shorts.

Your task:
Evaluate transcript clip candidates and score how strong each one is as a standalone short-form clip.

Scoring dimensions (0 to 10 each):
1. hook_strength
2. self_contained
3. clarity
4. emotion_curiosity
5. shareability
6. platform_fit

Rules:
- Prefer clips with a strong opening line in the first 1-3 seconds.
- Prefer clips that make sense without prior context.
- Prefer one clear idea over broad rambling discussion.
- Prefer clips with tension, payoff, novelty, surprise, emotional contrast, or memorable phrasing.
- Avoid intros, outros, sponsor sections, housekeeping, and context-heavy fragments.
- Do not invent timestamps.
- Use the provided transcript text only.

Composite score formula:
composite_score =
0.25 * hook_strength +
0.20 * self_contained +
0.15 * clarity +
0.20 * emotion_curiosity +
0.10 * shareability +
0.10 * platform_fit

Return ONLY valid JSON in this exact shape:
{
  "results": [
    {
      "clip_id": "string",
      "start": 0.0,
      "end": 0.0,
      "hook_strength": 0,
      "self_contained": 0,
      "clarity": 0,
      "emotion_curiosity": 0,
      "shareability": 0,
      "platform_fit": 0,
      "composite_score": 0.0,
      "reason": "short reason"
    }
  ]
}
""".strip()

    def __init__(self, client: Any, model: str):
        self.client = client
        self.model = model

    def score_candidates(
        self,
        candidates: list[dict[str, Any]],
        batch_size: int = 8,
        temperature: float = 0.2,
    ) -> list[dict[str, Any]]:
        scored: list[dict[str, Any]] = []

        for i in range(0, len(candidates), batch_size):
            batch = candidates[i:i + batch_size]
            prompt = self._build_user_prompt(batch)
            result = self._call_llm(prompt=prompt, temperature=temperature)
            scored.extend(result)

        merged = self._merge_results(candidates, scored)
        merged.sort(key=lambda x: x["final_score"], reverse=True)
        return merged

    def _build_user_prompt(self, candidates: list[dict[str, Any]]) -> str:
        payload = []
        for c in candidates:
            payload.append(
                {
                    "clip_id": c["clip_id"],
                    "start": round(float(c["start"]), 2),
                    "end": round(float(c["end"]), 2),
                    "duration": round(float(c["duration"]), 2),
                    "transcript_text": c["transcript_text"],
                }
            )

        return (
            "Score these transcript clip candidates for short-form virality potential.\n\n"
            + json.dumps({"candidates": payload}, ensure_ascii=False, indent=2)
        )

    def _call_llm(self, prompt: str, temperature: float) -> list[dict[str, Any]]:
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=temperature,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )

        content = response.choices[0].message.content
        parsed = json.loads(content)
        return parsed.get("results", [])

    def _merge_results(
        self,
        original_candidates: list[dict[str, Any]],
        llm_results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        by_clip_id = {item["clip_id"]: item for item in llm_results}
        merged = []

        for candidate in original_candidates:
            llm = by_clip_id.get(candidate["clip_id"], {})
            heuristic_score = float(candidate.get("score", 0.0))
            llm_score_10 = float(llm.get("composite_score", 0.0))
            llm_score = llm_score_10 / 10.0

            final_score = (0.35 * heuristic_score) + (0.65 * llm_score)

            merged.append(
                {
                    **candidate,
                    "llm_scores": {
                        "hook_strength": llm.get("hook_strength"),
                        "self_contained": llm.get("self_contained"),
                        "clarity": llm.get("clarity"),
                        "emotion_curiosity": llm.get("emotion_curiosity"),
                        "shareability": llm.get("shareability"),
                        "platform_fit": llm.get("platform_fit"),
                        "composite_score": llm.get("composite_score"),
                        "reason": llm.get("reason"),
                    },
                    "heuristic_score": heuristic_score,
                    "final_score": round(final_score, 4),
                }
            )

        return merged