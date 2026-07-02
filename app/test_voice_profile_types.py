import unittest

from app.voice_engine.types import VoiceProfile


class VoiceProfileTypeNormalizationTests(unittest.TestCase):
    def test_parse_obj_coerces_list_valued_text_fields(self) -> None:
        profile = VoiceProfile.parse_obj(
            {
                "humor_style": ["playful teasing", "wordplay"],
                "style_summary": ["Direct and punchy", "teaches through contrast"],
                "narrative_behavior": {
                    "authority_pattern": ["personal credential mentions", "confident tone"],
                    "closing_pattern": ["thank participants", "sign-off phrase"],
                },
            }
        )

        self.assertEqual(profile.humor_style, "playful teasing; wordplay")
        self.assertEqual(profile.style_summary, "Direct and punchy; teaches through contrast")
        self.assertEqual(
            profile.narrative_behavior.authority_pattern,
            "personal credential mentions; confident tone",
        )
        self.assertEqual(
            profile.narrative_behavior.closing_pattern,
            "thank participants; sign-off phrase",
        )

    def test_parse_obj_coerces_string_valued_list_fields(self) -> None:
        profile = VoiceProfile.parse_obj(
            {
                "tone": "direct",
                "hook_style": "sharp contradiction",
                "cognitive_style": {
                    "reasoning_style": "systems-first",
                },
                "constraint_profile": {
                    "avoids": "generic hype",
                },
            }
        )

        self.assertEqual(profile.tone, ["direct"])
        self.assertEqual(profile.hook_style, ["sharp contradiction"])
        self.assertEqual(profile.cognitive_style.reasoning_style, ["systems-first"])
        self.assertEqual(profile.constraint_profile.avoids, ["generic hype"])


if __name__ == "__main__":
    unittest.main()
