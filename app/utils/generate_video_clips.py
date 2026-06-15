"""
GroqShortsPipeline - Production-ready short-form video clip extractor.

Improvements over original:
  - Real blur-background (blurred fill + centered overlay) via filter_complex
  - ASS/SSA captions with per-word karaoke highlight instead of plain SRT
  - Proper cross-platform FFmpeg subtitle path escaping
  - Aspect-ratio-aware crop (landscape / portrait / square)
  - FFmpeg subprocess errors surfaced with full stderr context
  - Chunker requests 10-15 candidates spread across the full transcript
  - yt-dlp helper to download a YouTube URL directly
  - Graceful handling when Groq or yt-dlp are not installed

Clip selection improvements (v3):
  - Smarter _build_units: merges segments into 8-20s paragraph-level blocks
    so the LLM sees coherent thought-units, not tiny sentence fragments
  - Stricter TranscriptChunker prompt: hard rules for clean starts/ends,
    explicit BAD start/end examples, self-doubt guidance on boundary flags
  - BoundaryValidator extended with MID_TOPIC_START_PATTERNS and
    MID_TOPIC_END_PATTERNS: catches semantically mid-conversation clips
    that pass grammar checks but still feel incomplete
  - _passes_strict now enforces all 8 quality gates including mid-topic checks

Clip ending improvements (v4):
  - _extend_to_clean_end: after the LLM picks a boundary, scans forward up to
    3 units (max 15s) to find the next sentence-final punctuation mark - fixes
    the most common cause of mid-topic endings where the unit hard-cap cut the
    last sentence short
  - _passes_strict no longer trusts the LLM's has_clean_end flag; ending
    quality is validated entirely by our own text-based checks which are
    more reliable than the LLM's self-reported assessment
  - Payoff penalty in scorer: clips with payoff < 4/10 are multiplied by 0.55,
    payoff < 6/10 by 0.80 - prevents a high-hook low-payoff clip from beating
    a consistently good clip in the final ranking

Quality upgrades (v5):
  - Always builds a deterministic candidate pool in addition to LLM picks
  - Repairs clip boundaries before scoring so clips start/end on complete thoughts
  - Adds editorial scoring for hook, payoff, density, boundary quality, and self-containment
  - Uses MMR-style selection for quality plus diversity instead of simple top-N
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


#

try:
    from groq import Groq
except ImportError:
    Groq = None  # type: ignore

try:
    import yt_dlp  # type: ignore
except ImportError:
    yt_dlp = None  # type: ignore


logger = logging.getLogger(__name__)


#
# Data model
#


@dataclass
class ClipCandidate:
    clip_id: str
    start: float
    end: float
    duration: float
    score: float
    title: str
    rationale: str
    transcript_text: str


#
# Boundary validator
#


class BoundaryValidator:
    BAD_START_PATTERNS = [
        r"^(and|but|because|or|then|also|which|who|that)\b",
        r"^(a|an|the)\s+\w+\s+that\b",
        r"^(of|to|for|with|in|on|at|from)\b",
    ]

    BAD_END_PATTERNS = [
        r"\b(and|but|because|which|that|if|when|while|although)$",
        r"\b(it's|this is|that is|there is|here is)$",
        r"\b(such|more|less|another|also|instead of|because of)$",
        r"[,:;-]\s*$",
    ]

    CTA_PATTERNS = [
        r"\bsubscribe to the channel\b",
        r"\bsee you in the next one\b",
        r"\bwatch this video\b",
        r"\blet me know in the comments\b",
        r"\bwrite hashtag\b",
        r"\bcome back in \d+\s+days\b",
        r"\bmy name is\b.+\bsee you\b",
        r"\blink in (the )?(description|bio)\b",
        r"\bfollow (me|us) for more\b",
        r"\bsmash (that )?like\b",
        r"\bturn on notifications\b",
        r"\bthanks for watching\b",
        r"\bsponsor(ed)? by\b",
    ]

    # Semantic mid-topic starts - grammatically valid but clearly reference
    # something said before, so the clip would feel like it starts mid-conversation.
    MID_TOPIC_START_PATTERNS = [
        r"^so that'?s (why|how|what|the)",
        r"^(and )?that'?s (why|how|what|because)",
        r"^the (second|third|fourth|fifth|next|last|final|other)\b",
        r"^(number|point|reason|thing|step) (two|three|four|five|\d)\b",
        r"^as (i|we) (said|mentioned|talked about|discussed|covered)\b",
        r"^(going )?back to\b",
        r"^so (anyway|basically|essentially|in other words)\b",
        r"^now[,.]? (as|like) (i|we) (said|mentioned)\b",
        r"^(so )?(continuing|moving) (on|forward|along)\b",
        r"^(right[,.]? )?(so[,.]? )?the (next|other) (thing|point|reason|part)\b",
        r"^(this|that|these|those|it|they|he|she) (is|are|was|were|means|shows|proves)\b",
        r"^(when|while|after|before) (that|this|it|they)\b",
        r"^(exactly|basically|essentially|again)[,.]?\b",
    ]

    # Semantic mid-topic ends - sentence is grammatically complete but clearly
    # sets up something that comes next, so the clip would feel unresolved.
    MID_TOPIC_END_PATTERNS = [
        r"\b(for example|for instance|such as|like this)\.?$",
        r"\b(the (first|second|next|last) (one|thing|point|reason|step)) (is|was)\.?$",
        r"\blet me (explain|show you|break|walk you)\b.{0,25}$",
        r"\bso (here|let|now) (is|are|me)\b.{0,25}$",
        r"\bwhich (means|is why|brings us|leads)\b",
        r"\band (here|this) is (where|why|how)\b",
        r"\bso the (question|problem|issue|thing) is\.?$",
        r"\bthere are (two|three|four|five|\d+) (reasons|things|ways|steps|parts)\.?$",
        r"\bthe (first|second|third) (reason|thing|point|step|one) is\.?$",
        r"\bwe'?re going to (talk|cover|look|see)\b.{0,30}$",
        r"\bthat'?s (when|where|why|how) things\b.{0,35}$",
    ]

    def normalize(self, text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").strip())

    def first_words(self, text: str, n: int = 10) -> str:
        return " ".join(self.normalize(text).split()[:n])

    def last_words(self, text: str, n: int = 12) -> str:
        return " ".join(self.normalize(text).split()[-n:])

    def is_valid_start(self, text: str) -> bool:
        t = self.normalize(text)
        if not t:
            return False
        head = self.first_words(t, 10)
        if len(head.split()) < 4:
            return False
        for pattern in self.BAD_START_PATTERNS:
            if re.search(pattern, head, flags=re.IGNORECASE):
                return False
        return True

    def is_valid_end(self, text: str) -> bool:
        t = self.normalize(text)
        if not t:
            return False
        tail = self.last_words(t, 12)
        if not re.search(r"[.?!]$", t):
            return False
        for pattern in self.BAD_END_PATTERNS:
            if re.search(pattern, tail, flags=re.IGNORECASE):
                return False
        return True

    def looks_like_cta_or_outro(self, text: str) -> bool:
        t = self.normalize(text).lower()
        return any(re.search(p, t) for p in self.CTA_PATTERNS)

    def has_unfinished_tail(self, text: str) -> bool:
        tail = self.last_words(text, 10).lower()
        bad_tail_patterns = [
            r"\bit's such$",
            r"\bwhich is$",
            r"\band that$",
            r"\bbut if$",
            r"\bso that$",
            r"\bbecause$",
            r"\bmore than$",
            r"\bthe reason is$",
            r"\bfor example$",
        ]
        return any(re.search(p, tail) for p in bad_tail_patterns)

    def has_weak_lead(self, text: str) -> bool:
        lead = self.first_words(text, 10).lower()
        bad_leads = [
            r"^and\b",
            r"^but\b",
            r"^because\b",
            r"^which\b",
            r"^a small change that\b",
        ]
        return any(re.search(p, lead) for p in bad_leads)

    def has_mid_topic_start(self, text: str) -> bool:
        """Catches semantically mid-conversation starts even if grammatically valid."""
        lead = self.first_words(text, 12).lower()
        return any(re.search(p, lead) for p in self.MID_TOPIC_START_PATTERNS)

    def has_mid_topic_end(self, text: str) -> bool:
        """Catches endings that set up a 'what comes next', making the clip feel unresolved."""
        tail = self.last_words(text, 12).lower()
        return any(re.search(p, tail) for p in self.MID_TOPIC_END_PATTERNS)

    def complete_enough(self, text: str) -> bool:
        return (
            self.is_valid_start(text)
            and self.is_valid_end(text)
            and not self.looks_like_cta_or_outro(text)
            and not self.has_unfinished_tail(text)
            and not self.has_weak_lead(text)
            and not self.has_mid_topic_start(text)
            and not self.has_mid_topic_end(text)
        )


#
# Transcript chunker  (LLM stage 1)
#


class TranscriptChunker:
    SYSTEM_PROMPT = """
You are a professional short-form video editor with years of experience on TikTok, Reels, and YouTube Shorts.

Your ONLY job is to find moments in a transcript that work as COMPLETE, STANDALONE clips.

HARD RULES - violating any of these means the clip is REJECTED:
1. The clip MUST start at the very beginning of a new thought, topic, story, or argument.
   - GOOD starts: "Here's the thing about...", "The biggest mistake people make is...", "Let me tell you a story...", "Most people don't realize..."
   - BAD starts: "...so that's why", "...the second point is", "...and what that means is", "...going back to", anything that references or depends on something said before.
2. The clip MUST end with a complete conclusion - a resolved thought, punchline, lesson, or call to reflection.
   - GOOD ends: a sentence ending with "." or "?" that wraps up the idea fully.
   - BAD ends: trailing off mid-thought, ending on a conjunction, ending mid-list, ending with "for example", ending with "there are three reasons" (without giving them).
3. The clip must make 100% sense to someone who has NOT watched any other part of the video.
4. Never start a clip mid-sentence, mid-list, or mid-argument.
5. Never end a clip mid-sentence, mid-list, or with a setup that needs a payoff.
6. Avoid clips that are purely intro/outro/CTA/sponsor/housekeeping.

QUALITY RULES:
- Prefer clips with a strong HOOK in the first 3 seconds: surprising stat, bold claim, relatable problem, direct question, or counterintuitive statement.
- Prefer clips with a satisfying PAYOFF: an insight, revelation, practical tip, emotional resolution, or memorable conclusion.
- Spread selections across the ENTIRE transcript - do not cluster them in one section.
- Return 10-15 clips. Fewer high-quality clips are BETTER than many mediocre ones.
- If a proposed start depends on a previous unit, move the start earlier until it is self-contained.
- If a proposed end sets up the next unit, extend the end until the payoff or resolved lesson is included.

SCORING GUIDANCE:
- has_clean_start: ONLY true if this clip begins at the very start of an independent new thought. If in doubt, mark false.
- has_clean_end: ONLY true if this clip ends with a fully resolved sentence. If in doubt, mark false.
- is_self_contained: ONLY true if a viewer with zero context would understand it completely. If in doubt, mark false.

Return ONLY valid JSON - no explanation, no markdown, no preamble.
""".strip()

    def __init__(self, client: Any, model: str) -> None:
        self.client = client
        self.model = model

    def chunk_units(self, units: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not units or not self.client:
            return []

        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0.2,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "transcript_chunks",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "chunks": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "start_unit":      {"type": "integer"},
                                        "end_unit":        {"type": "integer"},
                                        "title":           {"type": "string"},
                                        "summary":         {"type": "string"},
                                        "has_clean_start": {"type": "boolean"},
                                        "has_clean_end":   {"type": "boolean"},
                                        "is_self_contained": {"type": "boolean"},
                                        "hook_strength":   {"type": "number"},
                                        "payoff_strength": {"type": "number"},
                                        "shareability":    {"type": "number"},
                                        "reason":          {"type": "string"},
                                    },
                                    "required": [
                                        "start_unit", "end_unit", "title", "summary",
                                        "has_clean_start", "has_clean_end", "is_self_contained",
                                        "hook_strength", "payoff_strength", "shareability", "reason",
                                    ],
                                    "additionalProperties": False,
                                },
                            }
                        },
                        "required": ["chunks"],
                        "additionalProperties": False,
                    },
                },
            },
            messages=[
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "goal": (
                                "Select 10-15 of the best complete transcript chunks "
                                "for short-form videos. Spread them across the whole transcript."
                            ),
                            "units": [
                                {
                                    "unit_index": i,
                                    "start":    round(float(u["start"]), 2),
                                    "end":      round(float(u["end"]), 2),
                                    "duration": round(float(u["end"]) - float(u["start"]), 2),
                                    "gap_before": round(float(u.get("gap_before", 0.0)), 2),
                                    "gap_after": round(float(u.get("gap_after", 0.0)), 2),
                                    "clean_start": bool(u.get("clean_start")),
                                    "clean_end": bool(u.get("clean_end")),
                                    "topic_start": bool(u.get("topic_start")),
                                    "text":     u["text"],
                                }
                                for i, u in enumerate(units)
                            ],
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                },
            ],
            )

        try:
            parsed = json.loads(response.choices[0].message.content or "{}")
        except json.JSONDecodeError:
            logger.warning("Chunker returned invalid JSON; falling back to semantic chunking.")
            return []
        return parsed.get("chunks", [])


#
# LLM moment scorer  (LLM stage 2)
#


class LLMMomentScorer:
    SYSTEM_PROMPT = """
You are a senior short-form video editor.

Score already-complete transcript chunks for their strength as TikTok/Reel/Short clips.

Dimensions (0-10 each):
1. completeness    - does it feel like a full standalone moment?
2. hook_strength   - does the opening grab attention immediately?
3. payoff          - does it deliver a satisfying conclusion?
4. clarity         - is it easy to follow without prior context?
5. shareability    - would someone share this unprompted?
6. platform_fit    - does the pacing/length suit short-form?

Rules:
- Completeness is the single most important factor.
- Duration is NOT a hard constraint; a longer complete clip beats a shorter incomplete one.
- composite_score must reflect a weighted blend (weight completeness most heavily).
- Penalize any clip that starts with context-dependent language like "this", "that", "so that's why", "the next thing".
- Penalize any clip that ends with a setup but not the payoff.
- Reward clips that contain a clear setup, turn, and resolved takeaway.

Return ONLY valid JSON:
{
  "results": [
    {
      "clip_id": "string",
      "completeness": 0,
      "hook_strength": 0,
      "payoff": 0,
      "clarity": 0,
      "shareability": 0,
      "platform_fit": 0,
      "composite_score": 0.0,
      "reason": "short reason"
    }
  ]
}
""".strip()

    def __init__(self, client: Any, model: str) -> None:
        self.client = client
        self.model = model

    def score_candidates(
        self,
        candidates: list[dict[str, Any]],
        batch_size: int = 6,
    ) -> list[dict[str, Any]]:
        if not self.client:
            return candidates

        results: list[dict[str, Any]] = []

        for i in range(0, len(candidates), batch_size):
            batch = candidates[i : i + batch_size]
            response = self.client.chat.completions.create(
                model=self.model,
                temperature=0.2,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "scored_chunks",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "properties": {
                                "results": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "clip_id":        {"type": "string"},
                                            "completeness":   {"type": "number"},
                                            "hook_strength":  {"type": "number"},
                                            "payoff":         {"type": "number"},
                                            "clarity":        {"type": "number"},
                                            "shareability":   {"type": "number"},
                                            "platform_fit":   {"type": "number"},
                                            "composite_score":{"type": "number"},
                                            "reason":         {"type": "string"},
                                        },
                                        "required": [
                                            "clip_id", "completeness", "hook_strength",
                                            "payoff", "clarity", "shareability",
                                            "platform_fit", "composite_score", "reason",
                                        ],
                                        "additionalProperties": False,
                                    },
                                }
                            },
                            "required": ["results"],
                            "additionalProperties": False,
                        },
                    },
                },
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "candidates": [
                                    {
                                        "clip_id":  c["clip_id"],
                                        "start":    c["start"],
                                        "end":      c["end"],
                                        "duration": c["duration"],
                                        "title":    c["title"],
                                        "summary":  c.get("summary", ""),
                                        "boundary_score": c.get("boundary_score"),
                                        "hook_score": c.get("hook_score"),
                                        "payoff_score": c.get("payoff_score"),
                                        "self_contained_score": c.get("self_contained_score"),
                                        "text":     c["transcript_text"],
                                    }
                                    for c in batch
                                ]
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                    },
                ],
            )

            try:
                parsed = json.loads(response.choices[0].message.content or "{}")
            except json.JSONDecodeError:
                logger.warning("Scorer returned invalid JSON for batch starting at index %s", i)
                continue
            results.extend(parsed.get("results", []))

        by_id = {r["clip_id"]: r for r in results}
        merged: list[dict[str, Any]] = []

        for c in candidates:
            llm        = by_id.get(c["clip_id"], {})
            base       = float(c.get("editorial_score", c.get("base_score", 0.0)))
            llm_score  = float(llm.get("composite_score", 0.0)) / 10.0
            dur_score  = self._duration_score(float(c["duration"]))

            if llm:
                final = 0.55 * llm_score + 0.35 * base + 0.10 * dur_score
            else:
                final = 0.88 * base + 0.12 * dur_score

            # Payoff penalty - a clip with a weak ending should rank much lower
            # regardless of how strong its hook or shareability scores are.
            # This stops a 9/10 hook + 2/10 payoff clip beating a solid 7/7 clip.
            payoff_raw = float(llm.get("payoff", 5.0))
            if payoff_raw < 4.0:
                final *= 0.55   # heavy penalty - ending is clearly unresolved
            elif payoff_raw < 6.0:
                final *= 0.80   # moderate penalty - ending is weak but not broken

            completeness_raw = float(llm.get("completeness", 7.0))
            if completeness_raw < 5.0:
                final *= 0.60
            elif completeness_raw < 7.0:
                final *= 0.82

            boundary_score = float(c.get("boundary_score", 1.0))
            if boundary_score < 0.60:
                final *= 0.55
            elif boundary_score < 0.75:
                final *= 0.82

            merged.append(
                {
                    **c,
                    "duration_score": round(dur_score, 4),
                    "llm_scores":     llm,
                    "final_score":    round(final, 4),
                    "rationale":      llm.get("reason", c.get("rationale", "")),
                }
            )

        merged.sort(key=lambda x: x["final_score"], reverse=True)
        return merged

    @staticmethod
    def _duration_score(duration: float) -> float:
        if 20 <= duration <= 50:  return 1.00
        if 10 <= duration <  20:  return 0.85
        if 50 <  duration <= 75:  return 0.80
        if 75 <  duration <= 90:  return 0.60
        if  7 <= duration <  10:  return 0.45
        if 90 <  duration <= 120: return 0.40
        return 0.15


#
# YouTube downloader helper
#


def download_youtube_video(url: str, output_dir: str) -> str:
    """
    Download a YouTube video using yt-dlp and return the local file path.

    Install:  pip install yt-dlp
    """
    if yt_dlp is None:
        raise ImportError(
            "yt-dlp is not installed. Run:  pip install yt-dlp"
        )

    output_dir_path = Path(output_dir)
    output_dir_path.mkdir(parents=True, exist_ok=True)

    ydl_opts: dict[str, Any] = {
        # Prefer a single mp4 with audio; fall back to best available
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": str(output_dir_path / "%(id)s.%(ext)s"),
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        # ydl.prepare_filename returns the final path (post-merge)
        path = ydl.prepare_filename(info)

    # yt-dlp may have changed the extension after merging
    for ext in ("mp4", "mkv", "webm"):
        candidate = Path(path).with_suffix(f".{ext}")
        if candidate.exists():
            return str(candidate)

    raise FileNotFoundError(
        f"Downloaded file not found at expected path: {path}"
    )


#
# Main pipeline
#


class GroqShortsPipeline:
    """
    End-to-end pipeline:
      transcription dict  +  source video  ->  vertical 9:16 short clips
    """

    MIN_CLIP_DURATION = 8.0
    IDEAL_MIN_DURATION = 18.0
    IDEAL_MAX_DURATION = 72.0
    MAX_CLIP_DURATION = 135.0
    MAX_POOL_SIZE = 90

    def __init__(
        self,
        ffmpeg_bin: str = "ffmpeg",
        ffprobe_bin: str = "ffprobe",
        output_dir: str = "./output/short_clips",
        chunker: TranscriptChunker | None = None,
        scorer: LLMMomentScorer | None = None,
    ) -> None:
        self.ffmpeg_bin  = ffmpeg_bin
        self.ffprobe_bin = ffprobe_bin
        self.output_dir  = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.chunker   = chunker
        self.scorer    = scorer
        self.validator = BoundaryValidator()

    #

    def process(
        self,
        source_video_path: str,
        transcription: dict[str, Any],
        clip_count: int = 3,
        add_captions: bool = True,
        words_per_caption: int = 4,
        create_blur_background: bool = False,
        debug: bool = True,
    ) -> dict[str, Any]:
        source_video = Path(source_video_path)
        self._validate_process_inputs(source_video, clip_count, words_per_caption)
        self._ensure_media_tools()

        logger.info(
            "Clip pipeline started: source_video_path=%s clip_count=%s add_captions=%s create_blur_background=%s",
            source_video_path,
            clip_count,
            add_captions,
            create_blur_background,
        )

        transcript = self._normalize_input(transcription)
        if not transcript["segments"]:
            raise ValueError("Transcription must include timestamped segments or words to generate clips.")
        logger.info(
            "Normalized transcription: text_chars=%s segments=%s words=%s",
            len(transcript.get("text", "")),
            len(transcript.get("segments", [])),
            len(transcript.get("words", [])),
        )

        run_id       = str(uuid.uuid4())
        run_dir      = self.output_dir / run_id
        clips_dir    = run_dir / "clips"
        subs_dir     = run_dir / "subtitles"
        debug_dir    = run_dir / "debug"
        for d in (clips_dir, subs_dir, debug_dir):
            d.mkdir(parents=True, exist_ok=True)

        logger.info(
            "Clip output directories created: run_id=%s run_dir=%s clips_dir=%s subs_dir=%s debug_dir=%s",
            run_id,
            run_dir,
            clips_dir,
            subs_dir,
            debug_dir,
        )

        video_meta = self._probe_video(str(source_video))
        logger.info("Video metadata: %s", video_meta)
        units      = self._build_units(transcript["segments"])
        logger.info("Built transcript units: count=%s", len(units))
        if not units:
            raise ValueError("Transcription did not produce any usable clip units.")

        raw_llm_chunks: list[dict[str, Any]]  = []
        llm_candidates: list[dict[str, Any]] = []

        if self.chunker:
            try:
                logger.info("Running chunker on %s units", len(units))
                raw_llm_chunks  = self.chunker.chunk_units(units)
                llm_candidates = self._materialize_llm_chunks(raw_llm_chunks, units)
                logger.info(
                    "Chunker produced %s raw chunks and %s materialized candidates",
                    len(raw_llm_chunks),
                    len(llm_candidates),
                )
            except Exception as e:
                logger.exception("Chunker failed")
                if debug:
                    self._write_json(debug_dir / "chunker_error.json", {"error": str(e)})

        deterministic_candidates = self._semantic_fallback_chunks(units)
        candidate_dicts = self._prepare_candidate_pool(
            [*llm_candidates, *deterministic_candidates],
            units,
            clip_count=clip_count,
            debug_dir=debug_dir if debug else None,
        )
        logger.info(
            "Prepared candidate pool: llm=%s deterministic=%s validated=%s",
            len(llm_candidates),
            len(deterministic_candidates),
            len(candidate_dicts),
        )

        if self.scorer and candidate_dicts:
            try:
                logger.info("Scoring %s candidates", len(candidate_dicts))
                candidate_dicts = self.scorer.score_candidates(candidate_dicts)
                candidate_dicts = self._apply_editorial_scores(candidate_dicts)
                logger.info("Scoring complete: %s candidates", len(candidate_dicts))
            except Exception as e:
                logger.exception("Scorer failed")
                if debug:
                    self._write_json(debug_dir / "scorer_error.json", {"error": str(e)})

        candidate_dicts = self._select_diverse_candidates(candidate_dicts, clip_count)
        logger.info("Selected %s diverse candidates", len(candidate_dicts))

        if debug:
            self._write_json(debug_dir / "raw_llm_chunks.json",  raw_llm_chunks)
            self._write_json(debug_dir / "final_candidates.json", candidate_dicts)

        candidates = self._dicts_to_clip_candidates(candidate_dicts)
        logger.info("Converted %s candidates to dataclass instances", len(candidates))

        rendered: list[dict[str, Any]] = []
        render_errors: list[dict[str, Any]] = []
        for idx, candidate in enumerate(candidates, start=1):
            safe_name  = (self._slugify(candidate.title)[:70] or f"clip-{idx}")
            clip_path  = clips_dir / f"{idx:02d}-{safe_name}.mp4"
            sub_path   = subs_dir  / f"{idx:02d}-{safe_name}.ass"
            video_duration = float(video_meta.get("duration") or 0.0)
            clip_start = max(0.0, candidate.start - 0.12)
            clip_end = candidate.end + 0.22
            if video_duration > 0:
                clip_start = min(clip_start, video_duration)
                clip_end = min(clip_end, video_duration)
            if clip_end <= clip_start:
                error = {
                    "clip_id": candidate.clip_id,
                    "title": candidate.title,
                    "error": "candidate timestamps are outside the source video duration",
                    "start": candidate.start,
                    "end": candidate.end,
                    "video_duration": video_duration,
                }
                logger.warning("Skipping unrenderable candidate: %s", error)
                render_errors.append(error)
                continue
            logger.info(
                "Rendering candidate %s: clip_id=%s start=%s end=%s clip_path=%s sub_path=%s",
                idx,
                candidate.clip_id,
                clip_start,
                clip_end,
                clip_path,
                sub_path,
            )

            try:
                if add_captions and transcript["words"]:
                    logger.info("Writing subtitles for clip_id=%s word_count=%s", candidate.clip_id, len(transcript["words"]))
                    self._write_ass_for_clip(
                        words            = transcript["words"],
                        clip_start       = clip_start,
                        clip_end         = clip_end,
                        output_ass_path  = str(sub_path),
                        words_per_caption= words_per_caption,
                    )
                else:
                    logger.info("Skipping subtitles for clip_id=%s", candidate.clip_id)
                    sub_path = None  # type: ignore[assignment]

                logger.info("Starting FFmpeg render for clip_id=%s", candidate.clip_id)
                self._render_vertical_clip(
                    source_video_path    = str(source_video),
                    output_video_path    = str(clip_path),
                    clip_start           = clip_start,
                    clip_end             = clip_end,
                    video_meta           = video_meta,
                    subtitles_path       = str(sub_path) if sub_path else None,
                    create_blur_background = create_blur_background,
                )
                logger.info("Finished FFmpeg render for clip_id=%s output=%s", candidate.clip_id, clip_path)
            except Exception as exc:
                logger.exception("Failed to render clip_id=%s", candidate.clip_id)
                render_errors.append(
                    {
                        "clip_id": candidate.clip_id,
                        "title": candidate.title,
                        "start": clip_start,
                        "end": clip_end,
                        "error": str(exc),
                    }
                )
                continue

            rendered.append(
                {
                    "clip":          asdict(candidate),
                    "video_path":    str(clip_path.resolve()),
                    "subtitle_path": str(sub_path.resolve()) if sub_path else None,
                }
            )

        result = {
            "source_video_path": str(source_video.resolve()),
            "output_dir":        str(run_dir.resolve()),
            "run_id":            run_id,
            "plain_text":        transcript["text"],
            "video_meta":        video_meta,
            "selected_clips":    rendered,
            "failed_clips":      render_errors,
        }
        logger.info(
            "Writing clip pipeline result: run_id=%s output_dir=%s selected_clips=%s",
            run_id,
            run_dir,
            len(rendered),
        )
        self._write_json(run_dir / "result.json", result)
        if not rendered and render_errors:
            raise RuntimeError(f"All selected clip renders failed. See debug output in {run_dir}.")
        return result

    def _validate_process_inputs(
        self,
        source_video: Path,
        clip_count: int,
        words_per_caption: int,
    ) -> None:
        if not source_video.exists():
            raise FileNotFoundError(f"Source video not found: {source_video}")
        if not source_video.is_file():
            raise ValueError(f"Source video path is not a file: {source_video}")
        if clip_count < 1:
            raise ValueError("clip_count must be at least 1")
        if words_per_caption < 1:
            raise ValueError("words_per_caption must be at least 1")

    def _ensure_media_tools(self) -> None:
        missing = [
            tool
            for tool in (self.ffmpeg_bin, self.ffprobe_bin)
            if not self._is_executable_available(tool)
        ]
        if missing:
            raise RuntimeError(
                "Missing required media tool(s): "
                + ", ".join(missing)
                + ". Install FFmpeg and make sure ffmpeg/ffprobe are on PATH."
            )

    @staticmethod
    def _is_executable_available(command: str) -> bool:
        candidate = Path(command)
        if candidate.exists():
            return candidate.is_file()
        return shutil.which(command) is not None

    #

    def _normalize_input(self, transcription: dict[str, Any]) -> dict[str, Any]:
        text = self._clean_text(transcription.get("text") or "")

        segments: list[dict[str, Any]] = []
        for seg in transcription.get("segments", []):
            seg_text = self._clean_text(seg.get("text") or "")
            if not seg_text:
                continue
            segments.append(
                {
                    "id":    seg.get("id"),
                    "start": float(seg.get("start", 0)),
                    "end":   float(seg.get("end",   0)),
                    "text":  seg_text,
                }
            )

        words: list[dict[str, Any]] = []
        for w in transcription.get("words", []):
            token = (w.get("word") or "").strip()
            if token:
                words.append(
                    {
                        "word":  token,
                        "start": float(w.get("start", 0)),
                        "end":   float(w.get("end",   0)),
                    }
                )

        if not segments and words:
            segments = self._segments_from_words(words)

        if not text:
            text = " ".join(s["text"] for s in segments if s["text"]).strip()

        return {"text": text, "segments": segments, "words": words}

    def _segments_from_words(
        self,
        words: list[dict[str, Any]],
        target_duration: float = 12.0,
        max_duration: float = 20.0,
    ) -> list[dict[str, Any]]:
        segments: list[dict[str, Any]] = []
        current: list[dict[str, Any]] = []

        for word in words:
            if not current:
                current = [word]
                continue

            start = float(current[0]["start"])
            end = float(word["end"])
            token = str(word.get("word") or "")
            previous_token = str(current[-1].get("word") or "")
            sentence_boundary = previous_token.rstrip().endswith((".", "?", "!"))

            if (end - start >= target_duration and sentence_boundary) or (end - start >= max_duration):
                segments.append(self._word_group_to_segment(len(segments), current))
                current = [word]
            else:
                current.append(word)

        if current:
            segments.append(self._word_group_to_segment(len(segments), current))

        return segments

    def _word_group_to_segment(self, segment_id: int, words: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "id": segment_id,
            "start": float(words[0]["start"]),
            "end": float(words[-1]["end"]),
            "text": self._clean_text(" ".join(str(w.get("word") or "") for w in words)),
        }

    def _clean_text(self, text: str) -> str:
        text = re.sub(r"\s+", " ", (text or "").strip())
        text = re.sub(r"\b([A-Za-z]+)\s+\1\b", r"\1", text)
        return text.strip()

    #

    def _build_units(self, segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Build sentence/thought units that are small enough for precise clipping
        and large enough for the LLM to reason about complete ideas.
        """
        atoms: list[dict[str, Any]] = []
        for seg in segments:
            text  = seg["text"].strip()
            start = float(seg["start"])
            end   = float(seg["end"])
            if not text:
                continue

            parts = self._split_text_sentences(text)
            total_words = sum(max(1, len(part.split())) for part in parts)
            cursor = start
            duration = max(0.01, end - start)
            for idx, part in enumerate(parts):
                part_words = max(1, len(part.split()))
                part_duration = duration * (part_words / max(1, total_words))
                part_end = end if idx == len(parts) - 1 else min(end, cursor + part_duration)
                atoms.append(
                    {
                        "start": round(cursor, 2),
                        "end": round(part_end, 2),
                        "text": part.strip(),
                    }
                )
                cursor = part_end

        if not atoms:
            return []

        MIN_UNIT_DURATION = 4.0
        TARGET_UNIT_DURATION = 10.0
        MAX_UNIT_DURATION = 16.0
        PAUSE_THRESHOLD = 0.9

        units: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        previous_end: float | None = None

        for atom in atoms:
            start = float(atom["start"])
            end = float(atom["end"])
            text = atom["text"].strip()
            gap_before = 0.0 if previous_end is None else max(0.0, start - previous_end)

            if current is None:
                current = {
                    "start": start,
                    "end": end,
                    "texts": [text],
                    "gap_before": gap_before,
                }
                previous_end = end
                continue

            current_text = " ".join(current["texts"]).strip()
            current_duration = float(current["end"]) - float(current["start"])
            current_ends_clean = self._has_reasonable_end(current_text)
            next_opens_topic = self._looks_like_topic_start(text)
            long_pause = gap_before >= PAUSE_THRESHOLD

            should_flush = (
                long_pause
                or (current_duration >= MIN_UNIT_DURATION and current_ends_clean and next_opens_topic)
                or (current_duration >= TARGET_UNIT_DURATION and current_ends_clean)
                or current_duration >= MAX_UNIT_DURATION
            )

            if should_flush:
                units.append(self._finalize_unit(current, gap_after=gap_before))
                current = {
                    "start": start,
                    "end": end,
                    "texts": [text],
                    "gap_before": gap_before,
                }
            else:
                current["end"] = end
                current["texts"].append(text)

            previous_end = end

        if current:
            units.append(self._finalize_unit(current, gap_after=0.0))

        return units

    def _split_text_sentences(self, text: str) -> list[str]:
        text = self._clean_text(text)
        if not text:
            return []
        pieces = re.split(r"(?<=[.?!])\s+(?=[A-Z0-9\"'])", text)
        return [piece.strip() for piece in pieces if piece.strip()]

    def _finalize_unit(self, current: dict[str, Any], gap_after: float) -> dict[str, Any]:
        text = self._clean_text(" ".join(current["texts"]))
        return {
            "start": round(float(current["start"]), 2),
            "end": round(float(current["end"]), 2),
            "text": text,
            "gap_before": round(float(current.get("gap_before", 0.0)), 2),
            "gap_after": round(gap_after, 2),
            "clean_start": self._has_reasonable_start(text),
            "clean_end": self._has_reasonable_end(text),
            "topic_start": self._looks_like_topic_start(text),
        }

    def _looks_like_topic_start(self, text: str) -> bool:
        lead = self.validator.first_words(text, 12).lower()
        if not lead:
            return False
        patterns = [
            r"^(here'?s|this is|the truth is|the problem is|the biggest|one thing|first|next)\b",
            r"^(let me|i want to|i think|i learned|most people|people don'?t)\b",
            r"^(why|how|what|when|if you|imagine|suppose)\b",
            r"^(there'?s|there is|there are)\b",
            r"^(so|now)[,.]? (here'?s|let'?s|the point|the question)\b",
        ]
        return any(re.search(pattern, lead) for pattern in patterns)

    #

    def _materialize_llm_chunks(
        self,
        raw_chunks: list[dict[str, Any]],
        units: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []

        for chunk in raw_chunks:
            start_idx = int(chunk["start_unit"])
            end_idx   = int(chunk["end_unit"])

            if start_idx < 0 or end_idx >= len(units) or start_idx > end_idx:
                continue

            # Extend end_idx forward to the next clean sentence boundary if the
            # LLM-chosen end unit doesn't end with sentence-final punctuation.
            end_idx = self._extend_to_clean_end(end_idx, units)

            candidate = self._candidate_from_units(
                units=units,
                start_idx=start_idx,
                end_idx=end_idx,
                source="llm",
                title=(chunk.get("title") or "Generated Clip").strip(),
                rationale=(chunk.get("reason") or "").strip(),
                summary=(chunk.get("summary") or "").strip(),
            )
            if not candidate:
                continue
            candidate["base_score"] = self._base_score_from_metadata(chunk, float(candidate["duration"]))
            candidate["llm_boundary_flags"] = {
                "has_clean_start":  bool(chunk.get("has_clean_start")),
                "has_clean_end":    bool(chunk.get("has_clean_end")),
                "is_self_contained":bool(chunk.get("is_self_contained")),
            }
            candidates.append(candidate)

        return candidates

    def _base_score_from_metadata(self, chunk: dict[str, Any], duration: float) -> float:
        hook        = float(chunk.get("hook_strength",   0.0)) / 10.0
        payoff      = float(chunk.get("payoff_strength", 0.0)) / 10.0
        shareability= float(chunk.get("shareability",    0.0)) / 10.0
        dur_score   = self._soft_duration_score(duration)
        return round(0.40 * hook + 0.30 * payoff + 0.20 * shareability + 0.10 * dur_score, 4)

    @staticmethod
    def _soft_duration_score(duration: float) -> float:
        if 20 <= duration <= 50:  return 1.00
        if 10 <= duration <  20:  return 0.85
        if 50 <  duration <= 75:  return 0.80
        if 75 <  duration <= 90:  return 0.60
        if  7 <= duration <  10:  return 0.45
        if 90 <  duration <= 120: return 0.40
        return 0.15

    def _extend_to_clean_end(
        self,
        end_idx: int,
        units: list[dict[str, Any]],
        max_extra_seconds: float = 15.0,
        max_extra_units: int = 3,
    ) -> int:
        """
        If the clip's last unit does not end with sentence-final punctuation,
        scan forward up to max_extra_units to find a unit that does.

        This fixes the most common case of mid-topic endings: the LLM picks
        a clean-ish boundary but the last unit ran up against the 20s hard cap
        mid-sentence, so the clip sounds cut off.

        Never extends by more than max_extra_seconds.
        Never extends into a unit that itself ends mid-topic.
        Returns the original end_idx if no better boundary is found.
        """
        # Already ends cleanly - nothing to do
        if re.search(r"[.?!]\s*$", units[end_idx]["text"].strip()):
            return end_idx

        base_end_time = float(units[end_idx]["end"])

        for lookahead in range(1, max_extra_units + 1):
            next_idx = end_idx + lookahead
            if next_idx >= len(units):
                break

            extra_seconds = float(units[next_idx]["end"]) - base_end_time
            if extra_seconds > max_extra_seconds:
                break

            unit_text = units[next_idx]["text"].strip()

            # This unit ends with sentence-final punctuation
            if re.search(r"[.?!]\s*$", unit_text):
                # Make sure extending here doesn't introduce a mid-topic ending
                if not self.validator.has_mid_topic_end(unit_text):
                    return next_idx

        # No cleaner boundary found - return original
        return end_idx

    #

    def _semantic_fallback_chunks(self, units: list[dict[str, Any]]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        max_abs = self.MAX_CLIP_DURATION

        for i in range(len(units)):
            if not self._unit_can_start_clip(units[i]):
                continue

            for j in range(i, len(units)):
                duration = float(units[j]["end"]) - float(units[i]["start"])
                if duration > max_abs:
                    break
                if duration < self.MIN_CLIP_DURATION:
                    continue
                if not self._unit_can_end_clip(units[j]):
                    continue

                candidate = self._candidate_from_units(
                    units=units,
                    start_idx=i,
                    end_idx=j,
                    source="semantic",
                    rationale="deterministic semantic window",
                )
                if candidate:
                    candidates.append(candidate)

        return candidates

    def _candidate_from_units(
        self,
        units: list[dict[str, Any]],
        start_idx: int,
        end_idx: int,
        source: str,
        title: str | None = None,
        rationale: str = "",
        summary: str = "",
    ) -> dict[str, Any] | None:
        if start_idx < 0 or end_idx >= len(units) or start_idx > end_idx:
            return None

        start = float(units[start_idx]["start"])
        end = float(units[end_idx]["end"])
        duration = end - start
        if duration <= 0:
            return None

        text = self._candidate_text(units, start_idx, end_idx)
        if not text:
            return None

        generated_title = " ".join(text.split()[:8]).strip() or "Generated Clip"
        candidate = {
            "clip_id": str(uuid.uuid4()),
            "start": round(start, 2),
            "end": round(end, 2),
            "duration": round(duration, 2),
            "base_score": self._heuristic_text_score(text, duration),
            "title": title or generated_title,
            "rationale": rationale,
            "summary": summary,
            "transcript_text": text,
            "start_unit": start_idx,
            "end_unit": end_idx,
            "source": source,
        }
        return candidate

    def _candidate_text(self, units: list[dict[str, Any]], start_idx: int, end_idx: int) -> str:
        return self._clean_text(" ".join(units[i]["text"] for i in range(start_idx, end_idx + 1)))

    def _unit_can_start_clip(self, unit: dict[str, Any]) -> bool:
        text = unit.get("text", "")
        if (
            float(unit.get("gap_before", 0.0)) >= 0.75
            and self._has_reasonable_start(text)
            and not self.validator.has_mid_topic_start(text)
        ):
            return True
        return (
            (bool(unit.get("topic_start")) and not self.validator.has_mid_topic_start(text))
            or (
                self._has_reasonable_start(text)
                and not self.validator.has_mid_topic_start(text)
                and not self.validator.has_weak_lead(text)
            )
        )

    def _unit_can_end_clip(self, unit: dict[str, Any]) -> bool:
        text = unit.get("text", "")
        return (
            self._has_reasonable_end(text)
            and not self.validator.has_unfinished_tail(text)
            and not self.validator.has_mid_topic_end(text)
        )

    def _prepare_candidate_pool(
        self,
        candidates: list[dict[str, Any]],
        units: list[dict[str, Any]],
        clip_count: int,
        debug_dir: Path | None = None,
    ) -> list[dict[str, Any]]:
        repaired: list[dict[str, Any]] = []
        for candidate in candidates:
            repaired_candidate = self._repair_candidate_boundaries(candidate, units)
            if repaired_candidate:
                repaired.append(repaired_candidate)

        deduped = self._dedupe_candidates(repaired)
        scored = self._apply_editorial_scores(deduped)
        validated = self._validate_candidates_with_backoff(scored, debug_dir)
        pool_size = max(self.MAX_POOL_SIZE, clip_count * 24)
        prepared = self._preselect_candidate_pool(validated, pool_size)

        if debug_dir:
            self._write_json(debug_dir / "candidate_pool_prepared.json", prepared)

        return prepared

    def _repair_candidate_boundaries(
        self,
        candidate: dict[str, Any],
        units: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        start_idx = int(candidate.get("start_unit", 0))
        end_idx = int(candidate.get("end_unit", 0))
        if start_idx < 0 or end_idx >= len(units) or start_idx > end_idx:
            return None

        best: dict[str, Any] | None = None
        best_score = -1.0
        for s in range(max(0, start_idx - 2), min(len(units), start_idx + 3)):
            if s > end_idx:
                break
            for e in range(max(s, end_idx), min(len(units), end_idx + 4)):
                duration = float(units[e]["end"]) - float(units[s]["start"])
                if duration < self.MIN_CLIP_DURATION or duration > self.MAX_CLIP_DURATION:
                    continue
                text = self._candidate_text(units, s, e)
                boundary_score = self._boundary_score(text)
                end_bonus = 0.08 if self._unit_can_end_clip(units[e]) else 0.0
                start_bonus = 0.08 if self._unit_can_start_clip(units[s]) else 0.0
                duration_bonus = self._soft_duration_score(duration) * 0.08
                score = boundary_score + start_bonus + end_bonus + duration_bonus
                if score > best_score:
                    best_score = score
                    best = self._candidate_from_units(
                        units=units,
                        start_idx=s,
                        end_idx=e,
                        source=candidate.get("source", "candidate"),
                        title=candidate.get("title"),
                        rationale=candidate.get("rationale", ""),
                        summary=candidate.get("summary", ""),
                    )

        if not best:
            return None

        preserved = {
            key: value
            for key, value in candidate.items()
            if key not in {"start", "end", "duration", "transcript_text", "start_unit", "end_unit", "base_score"}
        }
        best.update(preserved)
        best["base_score"] = max(
            float(candidate.get("base_score", 0.0)),
            self._heuristic_text_score(best["transcript_text"], float(best["duration"])),
        )
        if int(best["start_unit"]) != start_idx or int(best["end_unit"]) != end_idx:
            best["boundary_repaired"] = True
            best["original_start_unit"] = start_idx
            best["original_end_unit"] = end_idx
        return best

    def _dedupe_candidates(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        best_by_key: dict[tuple[int, int], dict[str, Any]] = {}
        for candidate in candidates:
            key = (round(float(candidate["start"])), round(float(candidate["end"])))
            current = best_by_key.get(key)
            if current is None or float(candidate.get("base_score", 0.0)) > float(current.get("base_score", 0.0)):
                best_by_key[key] = candidate
        return list(best_by_key.values())

    def _preselect_candidate_pool(
        self,
        candidates: list[dict[str, Any]],
        max_candidates: int,
    ) -> list[dict[str, Any]]:
        ranked = sorted(
            candidates,
            key=lambda c: float(c.get("final_score", c.get("editorial_score", c.get("base_score", 0.0)))),
            reverse=True,
        )
        return ranked[:max_candidates]

    def _apply_editorial_scores(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        scored: list[dict[str, Any]] = []
        for candidate in candidates:
            text = candidate.get("transcript_text", "")
            duration = float(candidate.get("duration", 0.0))
            metrics = self._editorial_metrics(text, duration)
            previous_final = float(candidate.get("final_score", candidate.get("base_score", 0.0)))
            editorial = metrics["editorial_score"]
            final = max(previous_final, editorial)
            if metrics["boundary_score"] < 0.55:
                final *= 0.55
            if metrics["payoff_score"] < 0.45:
                final *= 0.72
            if metrics["self_contained_score"] < 0.50:
                final *= 0.78

            scored.append(
                {
                    **candidate,
                    **metrics,
                    "base_score": max(float(candidate.get("base_score", 0.0)), editorial),
                    "final_score": round(final, 4),
                }
            )

        scored.sort(key=lambda c: float(c.get("final_score", 0.0)), reverse=True)
        return scored

    def _editorial_metrics(self, text: str, duration: float) -> dict[str, float]:
        boundary = self._boundary_score(text)
        hook = self._hook_score(text)
        payoff = self._payoff_score(text)
        density = self._information_density_score(text, duration)
        self_contained = self._self_contained_score(text)
        retention = self._retention_score(text)
        duration_score = self._soft_duration_score(duration)
        editorial = (
            0.24 * boundary
            + 0.18 * hook
            + 0.20 * payoff
            + 0.13 * density
            + 0.13 * self_contained
            + 0.07 * retention
            + 0.05 * duration_score
        )
        return {
            "boundary_score": round(boundary, 4),
            "hook_score": round(hook, 4),
            "payoff_score": round(payoff, 4),
            "density_score": round(density, 4),
            "self_contained_score": round(self_contained, 4),
            "retention_score": round(retention, 4),
            "editorial_score": round(editorial, 4),
        }

    def _boundary_score(self, text: str) -> float:
        score = 0.0
        if self.validator.is_valid_start(text):
            score += 0.28
        if self.validator.is_valid_end(text):
            score += 0.32
        if not self.validator.has_mid_topic_start(text):
            score += 0.14
        if not self.validator.has_mid_topic_end(text):
            score += 0.14
        if not self.validator.has_unfinished_tail(text):
            score += 0.07
        if not self.validator.looks_like_cta_or_outro(text):
            score += 0.05
        return min(1.0, score)

    def _hook_score(self, text: str) -> float:
        lead = self.validator.first_words(text, 28).lower()
        patterns = [
            r"\bwhy\b", r"\bhow to\b", r"\bwhat if\b", r"\bdo you\b",
            r"\bmost people\b", r"\bnobody\b", r"\bthe truth\b",
            r"\bbiggest mistake\b", r"\bhere'?s\b", r"\bimagine\b",
            r"\bsecret\b", r"\bproblem\b", r"\bnever\b", r"\balways\b",
            r"\b\d+[%x]?\b",
        ]
        score = 0.25 + min(0.65, sum(1 for p in patterns if re.search(p, lead)) * 0.16)
        if re.search(r"^(um|uh|yeah|okay|so yeah|alright)\b", lead):
            score -= 0.25
        if len(lead.split()) >= 8:
            score += 0.10
        return max(0.0, min(1.0, score))

    def _payoff_score(self, text: str) -> float:
        tail = self.validator.last_words(text, 34).lower()
        score = 0.35 if self._has_reasonable_end(text) else 0.0
        patterns = [
            r"\bthat'?s why\b", r"\bthe point is\b", r"\bso\b",
            r"\btherefore\b", r"\bultimately\b", r"\bin the end\b",
            r"\bwhat matters\b", r"\bthe lesson\b", r"\bthat means\b",
            r"\bas a result\b", r"\bin reality\b", r"\bremember\b",
        ]
        score += min(0.45, sum(1 for p in patterns if re.search(p, tail)) * 0.15)
        if not self.validator.has_mid_topic_end(text) and not self.validator.has_unfinished_tail(text):
            score += 0.20
        return max(0.0, min(1.0, score))

    def _information_density_score(self, text: str, duration: float) -> float:
        tokens = self._tokenize(text)
        if not tokens or duration <= 0:
            return 0.0
        word_count = max(1, len(text.split()))
        unique_ratio = len(tokens) / word_count
        words_per_second = word_count / duration
        density = min(1.0, unique_ratio * 1.8) * 0.55 + min(1.0, words_per_second / 2.8) * 0.45
        filler_count = len(re.findall(r"\b(um|uh|like|you know|sort of|kind of|basically)\b", text.lower()))
        if filler_count:
            density *= max(0.65, 1.0 - filler_count * 0.04)
        return max(0.0, min(1.0, density))

    def _self_contained_score(self, text: str) -> float:
        score = 1.0
        lead = self.validator.first_words(text, 14).lower()
        if self.validator.has_mid_topic_start(text):
            score -= 0.35
        if re.search(r"^(this|that|these|those|it|they|he|she|we)\b", lead):
            score -= 0.20
        if re.search(r"\b(as i said|like i said|earlier|previously|before this|that thing)\b", text.lower()):
            score -= 0.25
        if self.validator.looks_like_cta_or_outro(text):
            score -= 0.30
        return max(0.0, min(1.0, score))

    def _retention_score(self, text: str) -> float:
        t = text.lower()
        patterns = [
            r"\bbut\b", r"\bhowever\b", r"\binstead\b", r"\bthe problem\b",
            r"\bthe reason\b", r"\bwhat happens\b", r"\bturns out\b",
            r"\bsurprising\b", r"\bmistake\b", r"\bimportant\b",
            r"\bbecause\b", r"\bif you\b", r"\byou can\b",
        ]
        return min(1.0, 0.25 + sum(1 for p in patterns if re.search(p, t)) * 0.10)

    def _heuristic_text_score(self, text: str, duration: float) -> float:
        t = text.lower()
        hook_pats = [
            r"\bdo you\b", r"\bhow to\b", r"\bwhy\b", r"\bimagine\b",
            r"\bthe point is\b", r"\blet's start\b", r"\bif you\b", r"\bquestion is\b",
        ]
        payoff_pats = [
            r"\bthe point is\b", r"\bthat's because\b", r"\bin reality\b",
            r"\bhere's how\b", r"\bthis is called\b", r"\bthat means\b", r"\bas a result\b",
        ]
        hook_score   = min(1.0, sum(1 for p in hook_pats   if re.search(p, t)) * 0.22)
        payoff_score = min(1.0, sum(1 for p in payoff_pats if re.search(p, t)) * 0.20)
        dur_score    = self._soft_duration_score(duration)
        return round(0.45 * hook_score + 0.35 * payoff_score + 0.20 * dur_score, 4)

    #

    def _validate_candidates_with_backoff(
        self,
        candidates: list[dict[str, Any]],
        debug_dir: Path | None = None,
    ) -> list[dict[str, Any]]:
        strict: list[dict[str, Any]]  = []
        relaxed: list[dict[str, Any]] = []
        lenient: list[dict[str, Any]] = []
        rows: list[dict[str, Any]]    = []

        for c in candidates:
            s_ok, s_reasons = self._passes_strict(c)
            r_ok, r_reasons = self._passes_relaxed(c)
            l_ok, l_reasons = self._passes_lenient(c)

            rows.append(
                {
                    "clip_id":        c["clip_id"],
                    "start":          c["start"],
                    "end":            c["end"],
                    "duration":       c["duration"],
                    "title":          c.get("title"),
                    "source":         c.get("source"),
                    "strict_ok":      s_ok,
                    "strict_reasons": s_reasons,
                    "relaxed_ok":     r_ok,
                    "relaxed_reasons":r_reasons,
                    "lenient_ok":     l_ok,
                    "lenient_reasons":l_reasons,
                    "text_preview":   c.get("transcript_text", "")[:300],
                }
            )

            if s_ok:
                strict.append({**c, "validation_tier": "strict"})
            elif r_ok:
                relaxed.append(self._with_validation_penalty(c, "relaxed", 0.88))
            elif l_ok:
                lenient.append(self._with_validation_penalty(c, "lenient", 0.72))

        if debug_dir:
            self._write_json(debug_dir / "candidate_validation.json", rows)

        accepted = [*strict, *relaxed, *lenient]
        accepted.sort(
            key=lambda item: float(item.get("final_score", item.get("base_score", 0.0))),
            reverse=True,
        )
        return accepted

    def _with_validation_penalty(
        self,
        candidate: dict[str, Any],
        tier: str,
        multiplier: float,
    ) -> dict[str, Any]:
        final = float(candidate.get("final_score", candidate.get("base_score", 0.0))) * multiplier
        return {
            **candidate,
            "validation_tier": tier,
            "final_score": round(final, 4),
        }

    def _passes_strict(self, c: dict[str, Any]) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        text     = c.get("transcript_text", "").strip()
        duration = float(c.get("duration", 0.0))

        if duration < 8 or duration > 120:                reasons.append("extreme_duration")
        if not self.validator.is_valid_start(text):        reasons.append("bad_start")
        if not self.validator.is_valid_end(text):          reasons.append("bad_end")
        if self.validator.looks_like_cta_or_outro(text):   reasons.append("cta_or_outro")
        if self.validator.has_unfinished_tail(text):       reasons.append("unfinished_tail")
        if self.validator.has_weak_lead(text):             reasons.append("weak_lead")
        if self.validator.has_mid_topic_start(text):       reasons.append("mid_topic_start")
        if self.validator.has_mid_topic_end(text):         reasons.append("mid_topic_end")

        # We trust LLM's has_clean_start and is_self_contained assessments,
        # but NOT has_clean_end - our text-based validators above are more
        # reliable for ending quality than the LLM's self-reported flag.
        flags = c.get("llm_boundary_flags")
        if flags:
            if not flags.get("has_clean_start"):   reasons.append("llm_clean_start_false")
            if not flags.get("is_self_contained"): reasons.append("llm_self_contained_false")
            # has_clean_end intentionally omitted - validated by our own checks above

        return (len(reasons) == 0, reasons)

    def _passes_relaxed(self, c: dict[str, Any]) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        text     = c.get("transcript_text", "").strip()
        duration = float(c.get("duration", 0.0))

        if duration < 7 or duration > 140:
            reasons.append("extreme_duration")
        if not self._has_reasonable_start(text):
            reasons.append("bad_start")
        if not self._has_reasonable_end(text):
            reasons.append("bad_end")
        if self.validator.looks_like_cta_or_outro(text):
            reasons.append("cta_or_outro")
        if self.validator.has_unfinished_tail(text):
            reasons.append("unfinished_tail")

        return (len(reasons) == 0, reasons)

    def _passes_lenient(self, c: dict[str, Any]) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        text     = c.get("transcript_text", "").strip()
        duration = float(c.get("duration", 0.0))

        if duration < 6 or duration > 160:
            reasons.append("extreme_duration")
        if len(text.split()) < 16:
            reasons.append("too_short_text")
        if not re.search(r"[.?!]$", text):
            reasons.append("no_terminal_punctuation")

        lead = self.validator.first_words(text, 8).lower()
        if re.search(r"^(and|but|because|which)\b", lead):
            reasons.append("fragment_start")

        tail = self.validator.last_words(text, 8).lower()
        if re.search(r"\b(and|but|because|which|if|when)$", tail):
            reasons.append("fragment_end")

        return (len(reasons) == 0, reasons)

    def _has_reasonable_start(self, text: str) -> bool:
        lead = self.validator.first_words(text, 10).strip()
        if len(lead.split()) < 4:
            return False
        if re.search(r"^(and|but|because|which)\b", lead, flags=re.IGNORECASE):
            return False
        return True

    def _has_reasonable_end(self, text: str) -> bool:
        return bool(re.search(r"[.?!]$", text.strip()))

    #

    def _select_diverse_candidates(
        self,
        candidates: list[dict[str, Any]],
        clip_count: int,
    ) -> list[dict[str, Any]]:
        remaining = sorted(
            candidates,
            key=lambda x: float(x.get("final_score", x.get("base_score", 0.0))),
            reverse=True,
        )
        selected: list[dict[str, Any]] = []
        if not remaining:
            return selected

        while remaining and len(selected) < clip_count:
            best_idx: int | None = None
            best_mmr = -999.0
            for idx, candidate in enumerate(remaining):
                quality = float(candidate.get("final_score", candidate.get("base_score", 0.0)))
                similarity = 0.0
                for chosen in selected:
                    overlap = self._time_overlap_ratio(
                        float(candidate["start"]),
                        float(candidate["end"]),
                        float(chosen["start"]),
                        float(chosen["end"]),
                    )
                    text_similarity = self._jaccard_similarity(
                        candidate.get("transcript_text", ""),
                        chosen.get("transcript_text", ""),
                    )
                    similarity = max(similarity, overlap, text_similarity)

                if similarity > 0.62:
                    continue

                mmr = (0.78 * quality) - (0.22 * similarity)

                if mmr > best_mmr:
                    best_mmr = mmr
                    best_idx = idx

            if best_idx is None:
                break

            selected.append(remaining.pop(best_idx))

        return selected

    def _dicts_to_clip_candidates(self, items: list[dict[str, Any]]) -> list[ClipCandidate]:
        return [
            ClipCandidate(
                clip_id         = item["clip_id"],
                start           = float(item["start"]),
                end             = float(item["end"]),
                duration        = float(item["duration"]),
                score           = float(item.get("final_score", item.get("base_score", 0.0))),
                title           = item["title"],
                rationale       = item.get("rationale", ""),
                transcript_text = item["transcript_text"],
            )
            for item in items
        ]

    #

    def _write_ass_for_clip(
        self,
        words: list[dict[str, Any]],
        clip_start: float,
        clip_end: float,
        output_ass_path: str,
        words_per_caption: int = 4,
    ) -> None:
        """
        Write an ASS subtitle file with karaoke-style per-word highlight.
        The current word renders in a vivid yellow while the rest stay white.
        Font size 52 is legible on 1080x1920.
        """
        words_per_caption = max(1, words_per_caption)
        header = (
            "[Script Info]\n"
            "ScriptType: v4.00+\n"
            "PlayResX: 1080\n"
            "PlayResY: 1920\n"
            "WrapStyle: 0\n\n"
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding\n"
            # PrimaryColour = white (&H00FFFFFF), SecondaryColour = yellow (&H0000FFFF)
            # Alignment=2 -> bottom-centre; MarginV=120 -> lift from very bottom
            "Style: Default,Arial,52,&H00FFFFFF,&H0000FFFF,&H00000000,&H96000000,"
            "-1,0,0,0,100,100,0,0,1,3,1,2,40,40,120,1\n\n"
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        )

        in_range = [
            w for w in words
            if float(w["end"]) >= clip_start and float(w["start"]) <= clip_end
        ]

        lines: list[str] = []
        for i in range(0, len(in_range), words_per_caption):
            group   = in_range[i : i + words_per_caption]
            g_start = max(0.0, float(group[0]["start"]) - clip_start)
            g_end   = max(0.0, float(group[-1]["end"])  - clip_start)
            if g_end <= g_start:
                g_end = g_start + 0.4

            # {\\kf<cs>} = karaoke fill-from-left over <cs> centiseconds
            # while the fill is happening, SecondaryColour (yellow) is shown
            text_parts: list[str] = []
            for w in group:
                dur_cs = max(1, int((float(w["end"]) - float(w["start"])) * 100))
                text_parts.append(f"{{\\kf{dur_cs}}}{self._escape_ass_text(str(w['word']))}")

            lines.append(
                f"Dialogue: 0,{self._ass_time(g_start)},{self._ass_time(g_end)},"
                f"Default,,0,0,0,,{'  '.join(text_parts)}"
            )

        with open(output_ass_path, "w", encoding="utf-8") as f:
            f.write(header + "\n".join(lines) + "\n")

    @staticmethod
    def _escape_ass_text(text: str) -> str:
        return (
            text.replace("\\", "/")
            .replace("{", "(")
            .replace("}", ")")
            .replace("\n", " ")
            .strip()
        )

    def _ass_time(self, seconds: float) -> str:
        seconds = max(0.0, seconds)
        h  = int(seconds // 3600)
        m  = int((seconds % 3600) // 60)
        s  = int(seconds % 60)
        cs = int(round((seconds - int(seconds)) * 100))
        return f"{h}:{m:02}:{s:02}.{cs:02}"

    #

    def _render_vertical_clip(
        self,
        source_video_path: str,
        output_video_path: str,
        clip_start: float,
        clip_end: float,
        video_meta: dict[str, Any],
        subtitles_path: str | None = None,
        create_blur_background: bool = False,
    ) -> None:
        duration = clip_end - clip_start
        if duration <= 0:
            raise ValueError("clip_end must be greater than clip_start")

        width  = int(video_meta.get("width",  1920))
        height = int(video_meta.get("height", 1080))

        if create_blur_background:
            self._render_blur_background(
                source_video_path,
                output_video_path,
                clip_start,
                duration,
                subtitles_path,
            )
        else:
            vf = self._build_crop_filter(width, height, subtitles_path)
            self._run_ffmpeg(
                [
                    self.ffmpeg_bin,
                    "-y",
                    "-threads", "1",
                    "-filter_threads", "1",
                    "-ss", str(clip_start),
                    "-t",  str(duration),
                    "-i",  source_video_path,
                    "-vf", vf,
                    "-c:v", "libx264",
                    "-preset", "veryfast",
                    "-crf", "23",
                    "-pix_fmt", "yuv420p",
                    "-c:a", "aac",
                    "-b:a", "128k",
                    "-movflags", "+faststart",
                    output_video_path,
                ],
                output_video_path,
            )

    def _render_blur_background(
        self,
        source_video_path: str,
        output_video_path: str,
        clip_start: float,
        duration: float,
        subtitles_path: str | None,
    ) -> None:
        """
        Real blur background:
          - background layer  = video scaled to fill 1080x1920, then heavy boxblur
          - foreground layer  = video scaled to fit 1080x1920, centred on top
          - captions applied as a final subtitles filter on the composited output
        """
        # Build filter_complex for blur bg + centred fg
        filter_complex = (
            "[0:v]split=2[bg_raw][fg_raw];"

            # background: scale-to-fill then blur
            "[bg_raw]"
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,"
            "boxblur=luma_radius=25:luma_power=2"
            "[bg];"

            # foreground: scale-to-fit (no upscale beyond 1080 wide)
            "[fg_raw]"
            "scale=1080:1920:force_original_aspect_ratio=decrease"
            "[fg];"

            # overlay fg centred on bg
            "[bg][fg]overlay=(W-w)/2:(H-h)/2[composited]"
        )

        # If captions, chain a subtitles filter onto the composited output
        if subtitles_path:
            escaped         = self._escape_sub_path(subtitles_path)
            filter_complex += f";[composited]subtitles='{escaped}'[out]"
            map_arg         = "[out]"
        else:
            filter_complex += ";[composited]null[out]"
            map_arg         = "[out]"

        self._run_ffmpeg(
            [
                self.ffmpeg_bin,
                "-y",
                "-threads", "1",
                "-filter_threads", "1",
                "-ss", str(clip_start),
                "-t",  str(duration),
                "-i",  source_video_path,
                "-filter_complex", filter_complex,
                "-map", map_arg,
                "-map", "0:a?",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "23",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-b:a", "128k",
                "-movflags", "+faststart",
                output_video_path,
            ],
            output_video_path,
        )

    def _build_crop_filter(
        self,
        width: int,
        height: int,
        subtitles_path: str | None,
    ) -> str:
        """
        Aspect-ratio-aware crop -> 1080x1920.

        landscape  (w > h)  : centre-crop to 9:16
        portrait   (h > w)  : scale to fit, pad to 1080x1920
        square     (w ~ h)  : scale down then pad
        """
        aspect = (width / height) if height else 1.0

        if aspect > 1.05:
            # Landscape -> crop out the sides
            filters = [
                "crop='if(gt(a,9/16),ih*9/16,iw)':'if(gt(a,9/16),ih,iw*16/9)':"
                "'(iw-ow)/2':'(ih-oh)/2'",
                "scale=1080:1920",
            ]
        elif aspect < 0.95:
            # Portrait -> already tall, just scale
            filters = ["scale=1080:1920:force_original_aspect_ratio=decrease",
                       "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black"]
        else:
            # Square -> scale to width then pad top/bottom
            filters = ["scale=1080:1080",
                       "pad=1080:1920:0:(oh-ih)/2:black"]

        if subtitles_path:
            escaped = self._escape_sub_path(subtitles_path)
            style   = (
                "Alignment=2,Fontsize=52,Bold=1,Outline=2,Shadow=1,"
                "MarginV=120,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000"
            )
            filters.append(f"subtitles='{escaped}':force_style='{style}'")

        return ",".join(filters)

    @staticmethod
    def _escape_sub_path(path: str) -> str:
        """
        Escape a file path for use inside an FFmpeg -vf subtitles= value.
        Works on Windows (drive letters with colons) and Unix.
        """
        # Normalise to forward slashes
        p = path.replace("\\", "/")
        # Escape the drive-letter colon  C:/...  ->  C\:/...
        p = re.sub(r"^([A-Za-z]):/", r"\1\:/", p)
        # Escape any remaining single quotes
        p = p.replace("'", "\\'")
        return p

    #

    def _run_ffmpeg(self, cmd: list[str], output_path: str) -> None:
        try:
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=1800,
            )
        except subprocess.TimeoutExpired as exc:
            logger.exception("FFmpeg timed out for output_path=%s", output_path)
            raise RuntimeError(
                f"FFmpeg timed out while writing '{output_path}'.\n"
                f"Command:\n  {' '.join(cmd)}"
            ) from exc
        except subprocess.CalledProcessError as exc:
            logger.exception("FFmpeg failed for output_path=%s", output_path)
            raise RuntimeError(
                f"FFmpeg failed while writing '{output_path}'.\n"
                f"Command:\n  {' '.join(cmd)}\n\n"
                f"stderr:\n{exc.stderr}"
            ) from exc

    #

    def _probe_video(self, video_path: str) -> dict[str, Any]:
        cmd = [
            self.ffprobe_bin,
            "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            "-show_format",
            video_path,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=60)
        except subprocess.TimeoutExpired as exc:
            logger.exception("ffprobe timed out for video_path=%s", video_path)
            raise RuntimeError(f"ffprobe timed out on '{video_path}'.") from exc
        except subprocess.CalledProcessError as exc:
            logger.exception("ffprobe failed for video_path=%s", video_path)
            raise RuntimeError(
                f"ffprobe failed on '{video_path}'.\nstderr:\n{exc.stderr}"
            ) from exc

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"ffprobe returned invalid JSON for '{video_path}'.") from exc

        video_stream = next(
            (s for s in data.get("streams", []) if s.get("codec_type") == "video"), {}
        )
        audio_stream = next(
            (s for s in data.get("streams", []) if s.get("codec_type") == "audio"), {}
        )
        if not video_stream:
            raise RuntimeError(f"No video stream found in '{video_path}'.")

        return {
            "duration":    float(data.get("format", {}).get("duration", 0) or 0),
            "size":        int(data.get("format",   {}).get("size",     0) or 0),
            "width":       int(video_stream.get("width",  0) or 0),
            "height":      int(video_stream.get("height", 0) or 0),
            "video_codec": video_stream.get("codec_name"),
            "audio_codec": audio_stream.get("codec_name"),
        }

    #

    def _time_overlap_ratio(
        self,
        start_a: float, end_a: float,
        start_b: float, end_b: float,
    ) -> float:
        intersection = max(0.0, min(end_a, end_b) - max(start_a, start_b))
        if intersection <= 0:
            return 0.0
        return intersection / max(0.001, min(end_a - start_a, end_b - start_b))

    def _jaccard_similarity(self, text_a: str, text_b: str) -> float:
        a = self._tokenize(text_a)
        b = self._tokenize(text_b)
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)

    _STOPWORDS = frozenset(
        "the a an and or but if to of in on for is it this that you your "
        "i we they are was be with as at by from so do does did not have "
        "has had will would can could just".split()
    )

    def _tokenize(self, text: str) -> set[str]:
        words = re.findall(r"\b[a-zA-Z0-9']+\b", (text or "").lower())
        return {w for w in words if w not in self._STOPWORDS and len(w) > 2}

    #

    def _write_json(self, path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f"{path.name}.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)

    def _slugify(self, value: str) -> str:
        value = value.lower().strip()
        value = re.sub(r"[^a-z0-9]+", "-", value)
        return value.strip("-")


#
# Groq transcription normaliser  (handles model_dump / plain dict / object)
#


def normalize_groq_transcription(transcription: Any) -> dict[str, Any]:
    if hasattr(transcription, "model_dump"):
        data: dict[str, Any] = transcription.model_dump()
    elif isinstance(transcription, dict):
        data = transcription
    else:
        data = {
            "text":     getattr(transcription, "text",     ""),
            "segments": getattr(transcription, "segments", []),
            "words":    getattr(transcription, "words",    []),
        }

    text = (data.get("text") or "").strip()

    segments: list[dict[str, Any]] = []
    for seg in data.get("segments", []):
        if isinstance(seg, dict):
            segments.append(
                {
                    "id":    seg.get("id"),
                    "start": float(seg.get("start", 0)),
                    "end":   float(seg.get("end",   0)),
                    "text":  (seg.get("text") or "").strip(),
                }
            )
        else:
            segments.append(
                {
                    "id":    getattr(seg, "id",    None),
                    "start": float(getattr(seg, "start", 0)),
                    "end":   float(getattr(seg, "end",   0)),
                    "text":  (getattr(seg, "text", "") or "").strip(),
                }
            )

    words: list[dict[str, Any]] = []
    for word in data.get("words", []):
        if isinstance(word, dict):
            token = (word.get("word") or "").strip()
            if token:
                words.append(
                    {
                        "word":  token,
                        "start": float(word.get("start", 0)),
                        "end":   float(word.get("end",   0)),
                    }
                )
        else:
            token = (getattr(word, "word", "") or "").strip()
            if token:
                words.append(
                    {
                        "word":  token,
                        "start": float(getattr(word, "start", 0)),
                        "end":   float(getattr(word, "end",   0)),
                    }
                )

    if not text:
        text = " ".join(s["text"] for s in segments if s["text"]).strip()

    return {"text": text, "segments": segments, "words": words}


#
# Public convenience function
#


def generate_short_clips_from_groq(
    source_video_path: str,
    transcription: Any,
    clip_count: int = 3,
    output_dir: str = "./output/short_clips",
    add_captions: bool = True,
    words_per_caption: int = 4,
    create_blur_background: bool = False,
    groq_api_key: str | None = None,
    chunk_model: str = "openai/gpt-oss-120b",
    scorer_model: str = "openai/gpt-oss-120b",
    debug: bool = True,
) -> dict[str, Any]:
    """
    High-level entry point.  Accepts a Groq transcription object or plain dict.

    Example
    -------
    from groq import Groq

    groq_client = Groq(api_key="...")
    with open("video.mp4", "rb") as f:
        transcription = groq_client.audio.transcriptions.create(
            file=f,
            model="whisper-large-v3",
            response_format="verbose_json",
            timestamp_granularities=["segment", "word"],
        )

    result = generate_short_clips_from_groq(
        source_video_path="video.mp4",
        transcription=transcription,
        clip_count=5,
        create_blur_background=True,
    )
    print(result["selected_clips"])
    """
    logger.info(
        "generate_short_clips_from_groq called: source_video_path=%s clip_count=%s output_dir=%s add_captions=%s create_blur_background=%s",
        source_video_path,
        clip_count,
        output_dir,
        add_captions,
        create_blur_background,
    )
    normalized = normalize_groq_transcription(transcription)
    logger.info(
        "Normalized transcription bundle: text_chars=%s segments=%s words=%s",
        len(normalized.get("text", "")),
        len(normalized.get("segments", [])),
        len(normalized.get("words", [])),
    )

    from app.core.config import env

    api_key = groq_api_key or env("GROQ_API_KEY")
    client  = Groq(api_key=api_key) if (api_key and Groq is not None) else None
    logger.info("Groq client initialized: has_client=%s", bool(client))

    chunker = TranscriptChunker(client=client, model=chunk_model) if client else None
    scorer  = LLMMomentScorer(client=client,  model=scorer_model) if client else None
    logger.info("Pipeline helpers ready: has_chunker=%s has_scorer=%s", bool(chunker), bool(scorer))

    pipeline = GroqShortsPipeline(
        output_dir=output_dir,
        chunker=chunker,
        scorer=scorer,
    )

    return pipeline.process(
        source_video_path      = source_video_path,
        transcription          = normalized,
        clip_count             = clip_count,
        add_captions           = add_captions,
        words_per_caption      = words_per_caption,
        create_blur_background = create_blur_background,
        debug                  = debug,
    )


def generate_short_clips_from_youtube(
    youtube_url: str,
    transcription: Any,
    clip_count: int = 3,
    download_dir: str = "./output/downloads",
    output_dir: str = "./output/short_clips",
    add_captions: bool = True,
    words_per_caption: int = 4,
    create_blur_background: bool = False,
    groq_api_key: str | None = None,
    chunk_model: str = "llama-3.3-70b-versatile",
    scorer_model: str = "llama-3.3-70b-versatile",
    debug: bool = True,
) -> dict[str, Any]:
    """
    Convenience wrapper:  YouTube URL -> download -> clip generation.

    Requires yt-dlp:  pip install yt-dlp

    The caller is still responsible for providing the Groq transcription
    (transcribe the downloaded file first, then pass it here).
    """
    video_path = download_youtube_video(youtube_url, download_dir)

    return generate_short_clips_from_groq(
        source_video_path      = video_path,
        transcription          = transcription,
        clip_count             = clip_count,
        output_dir             = output_dir,
        add_captions           = add_captions,
        words_per_caption      = words_per_caption,
        create_blur_background = create_blur_background,
        groq_api_key           = groq_api_key,
        chunk_model            = chunk_model,
        scorer_model           = scorer_model,
        debug                  = debug,
    )
