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


if __name__ == "__main__":
    unittest.main()
