import unittest

from app.utils.generate_video_clips import ClipCandidate, GroqShortsPipeline


class GenerateVideoClipsBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pipeline = GroqShortsPipeline(output_dir="./output/test_short_clips")

    def _unit(self, start: float, end: float, text: str, gap_before: float = 0.0) -> dict:
        return {
            "start": start,
            "end": end,
            "text": text,
            "gap_before": gap_before,
            "gap_after": 0.0,
            "is_first": start == 0.0,
            "clean_start": self.pipeline._has_reasonable_start(text),
            "clean_end": self.pipeline._has_reasonable_end(text),
            "topic_start": self.pipeline._looks_like_topic_start(text),
        }

    def test_repairs_mid_topic_start_by_including_missing_setup(self) -> None:
        units = [
            self._unit(
                0.0,
                6.0,
                "The real problem with consistency is not motivation.",
                gap_before=1.0,
            ),
            self._unit(
                6.0,
                12.0,
                "It is that your system breaks when the day gets busy.",
            ),
            self._unit(
                12.0,
                19.0,
                "So the fix is to design a smaller promise you can keep every day.",
            ),
        ]
        candidate = self.pipeline._candidate_from_units(
            units=units,
            start_idx=1,
            end_idx=1,
            source="test",
        )

        repaired = self.pipeline._repair_candidate_boundaries(candidate, units)  # type: ignore[arg-type]

        self.assertIsNotNone(repaired)
        self.assertEqual(repaired["start_unit"], 0)
        self.assertGreaterEqual(repaired["end_unit"], 1)
        self.assertTrue(repaired["llm_boundary_flags"]["is_self_contained"])

    def test_extends_clean_punctuation_that_still_sets_up_next_unit(self) -> None:
        units = [
            self._unit(
                0.0,
                8.0,
                "There are three reasons creators lose momentum.",
                gap_before=1.0,
            ),
            self._unit(
                8.0,
                16.0,
                "The first reason is that they plan content that needs a perfect day.",
            ),
            self._unit(
                16.0,
                24.0,
                "The second reason is that they do not keep a small repeatable format.",
            ),
            self._unit(
                24.0,
                32.0,
                "The third reason is that they chase a big outcome before they build a tiny system.",
            ),
        ]

        end_idx = self.pipeline._extend_to_clean_end(0, 0, units)

        self.assertEqual(end_idx, 3)

    def test_sentence_atoms_use_word_level_boundaries(self) -> None:
        words = [
            {"word": "Previous", "start": 8.0, "end": 8.3},
            {"word": "sentence.", "start": 8.35, "end": 8.8},
            {"word": "This", "start": 9.0, "end": 9.2},
            {"word": "topic", "start": 9.25, "end": 9.5},
            {"word": "starts", "start": 9.55, "end": 9.8},
            {"word": "cleanly.", "start": 9.85, "end": 10.2},
        ]

        atoms = self.pipeline._sentence_atoms_from_words(words)

        self.assertEqual(atoms[1]["start"], 9.0)
        self.assertEqual(atoms[1]["end"], 10.2)
        self.assertEqual(atoms[1]["text"], "This topic starts cleanly.")

    def test_segment_sentences_align_to_word_timestamps_without_word_punctuation(self) -> None:
        segments = [
            {
                "start": 8.0,
                "end": 10.2,
                "text": "Previous sentence. This topic starts cleanly.",
            }
        ]
        words = [
            {"word": "Previous", "start": 8.0, "end": 8.3},
            {"word": "sentence", "start": 8.35, "end": 8.8},
            {"word": "This", "start": 9.0, "end": 9.2},
            {"word": "topic", "start": 9.25, "end": 9.5},
            {"word": "starts", "start": 9.55, "end": 9.8},
            {"word": "cleanly", "start": 9.85, "end": 10.2},
        ]

        atoms = self.pipeline._sentence_atoms_from_segments_and_words(segments, words)

        self.assertEqual(atoms[1]["start"], 9.0)
        self.assertEqual(atoms[1]["end"], 10.2)
        self.assertEqual(atoms[1]["text"], "This topic starts cleanly.")

    def test_build_units_keeps_strong_hook_as_own_start_boundary(self) -> None:
        segments = [
            {
                "start": 0.0,
                "end": 8.4,
                "text": (
                    "That context belongs before this. "
                    "Most creators lose momentum because they plan for a perfect day. "
                    "The fix is to design a smaller promise."
                ),
            }
        ]
        raw_words = (
            "That context belongs before this Most creators lose momentum because "
            "they plan for a perfect day The fix is to design a smaller promise"
        ).split()
        words = [
            {"word": word, "start": round(index * 0.35, 2), "end": round(index * 0.35 + 0.25, 2)}
            for index, word in enumerate(raw_words)
        ]

        units = self.pipeline._build_units(segments, words)

        self.assertGreaterEqual(len(units), 2)
        self.assertEqual(units[1]["start"], words[5]["start"])
        self.assertTrue(units[1]["text"].startswith("Most creators lose momentum"))

    def test_reasonable_sentence_without_topic_boundary_cannot_start_clip(self) -> None:
        unit = self._unit(
            18.0,
            26.0,
            "Your system breaks when the day gets busy.",
            gap_before=0.12,
        )
        unit["is_first"] = False
        unit["topic_start"] = False

        self.assertFalse(self.pipeline._unit_can_start_clip(unit))

    def test_render_bounds_do_not_pull_previous_or_next_sentence_words(self) -> None:
        candidate = ClipCandidate(
            clip_id="clip",
            start=10.0,
            end=20.4,
            duration=10.4,
            score=1.0,
            title="Clip",
            rationale="",
            transcript_text="A complete thought.",
        )
        words = [
            {"word": "before.", "start": 9.68, "end": 9.96},
            {"word": "A", "start": 10.0, "end": 10.2},
            {"word": "complete", "start": 10.25, "end": 10.6},
            {"word": "thought.", "start": 20.1, "end": 20.4},
            {"word": "After.", "start": 20.46, "end": 20.8},
        ]

        start, end = self.pipeline._render_bounds_for_candidate(candidate, words, video_duration=30.0)

        self.assertEqual(start, 10.0)
        self.assertLess(end, 20.46)

    def test_render_bounds_ignore_word_that_starts_after_candidate_end(self) -> None:
        candidate = ClipCandidate(
            clip_id="clip",
            start=10.0,
            end=20.4,
            duration=10.4,
            score=1.0,
            title="Clip",
            rationale="",
            transcript_text="A complete thought.",
        )
        words = [
            {"word": "A", "start": 10.0, "end": 10.2},
            {"word": "complete", "start": 10.25, "end": 10.6},
            {"word": "thought.", "start": 20.1, "end": 20.4},
            {"word": "Next", "start": 20.43, "end": 20.7},
        ]

        _, end = self.pipeline._render_bounds_for_candidate(candidate, words, video_duration=30.0)

        self.assertLess(end, 20.43)

    def test_youtube_shorts_uses_native_platform_profile(self) -> None:
        self.assertEqual(
            self.pipeline._platform_profile_for_asset("youtube_shorts"),
            "youtube_shorts",
        )

    def test_strong_hook_scores_above_generic_intro(self) -> None:
        strong = (
            "Most creators lose momentum because they plan for a perfect day. "
            "The fix is to design a promise so small you can keep it every time."
        )
        generic = (
            "Today I want to talk about creator consistency and why it matters. "
            "Creator consistency is important because it helps people publish more often."
        )

        strong_metrics = self.pipeline._editorial_metrics(strong, 24.0)
        generic_metrics = self.pipeline._editorial_metrics(generic, 24.0)

        self.assertGreater(strong_metrics["first_three_score"], generic_metrics["first_three_score"])
        self.assertGreater(strong_metrics["hook_score"], generic_metrics["hook_score"])

    def test_tiktok_platform_fit_prefers_fast_native_hook(self) -> None:
        strong = (
            "If you keep missing uploads, the problem is not discipline. "
            "Your plan is too fragile, so the fix is to make the promise smaller."
        )
        generic = (
            "Today I want to talk about uploading consistently on social media. "
            "Consistency can help creators improve their results over time."
        )
        candidates = self.pipeline._apply_editorial_scores(
            [
                {
                    "clip_id": "strong",
                    "start": 0.0,
                    "end": 22.0,
                    "duration": 22.0,
                    "base_score": 0.5,
                    "title": "Strong",
                    "rationale": "",
                    "summary": "",
                    "transcript_text": strong,
                    "context_before": "",
                    "context_after": "",
                    "start_unit": 0,
                    "end_unit": 0,
                    "source": "test",
                },
                {
                    "clip_id": "generic",
                    "start": 30.0,
                    "end": 52.0,
                    "duration": 22.0,
                    "base_score": 0.5,
                    "title": "Generic",
                    "rationale": "",
                    "summary": "",
                    "transcript_text": generic,
                    "context_before": "",
                    "context_after": "",
                    "start_unit": 1,
                    "end_unit": 1,
                    "source": "test",
                },
            ]
        )
        by_id = {candidate["clip_id"]: candidate for candidate in candidates}

        strong_score = self.pipeline._platform_fit_score(by_id["strong"], "tiktok_clip")
        generic_score = self.pipeline._platform_fit_score(by_id["generic"], "tiktok_clip")

        self.assertGreater(strong_score, generic_score)

    def test_render_uses_accurate_seek_after_input(self) -> None:
        captured = {}

        def capture_run(cmd, output_path):  # noqa: ANN001
            captured["cmd"] = cmd
            captured["output_path"] = output_path

        self.pipeline._run_ffmpeg = capture_run  # type: ignore[method-assign]

        self.pipeline._render_vertical_clip(
            source_video_path="source.mp4",
            output_video_path="out.mp4",
            clip_start=12.5,
            clip_end=18.0,
            video_meta={"width": 1080, "height": 1920},
            subtitles_path=None,
            create_blur_background=False,
        )

        cmd = captured["cmd"]
        self.assertLess(cmd.index("-i"), cmd.index("-ss"))


if __name__ == "__main__":
    unittest.main()
