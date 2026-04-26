EXAMPLE_VOICE_PROFILE_EXTRACTION_INPUT = {
    "samples": [
        "I used to think consistency was the problem. It wasn't. Distribution was.",
        "Most people are creating more. The winners are repackaging better.",
        "If this lands, go watch the full breakdown because the second mistake is worse.",
    ],
}


EXAMPLE_STORED_VOICE_PROFILE = {
    "tone": ["direct", "confessional", "high-conviction"],
    "sentence_rhythm": "Short, punchy openings followed by 1-2 clarifying sentences. Uses abrupt transitions to create tension, then slows down to explain the implication.",
    "hook_style": [
        "Starts with a mistake, tension, or unpopular opinion",
        "Uses contrast between what the creator believed and what proved true",
        "Opens loops early and delays the payoff"
    ],
    "cta_style": [
        "Invites the audience to see the full breakdown for the missing piece",
        "Keeps CTAs concise and curiosity-driven instead of salesy",
        "Ends with a soft redirect rather than a hard sell"
    ],
    "humor_style": "Light sarcasm used sparingly to underline obvious mistakes or self-correct with credibility.",
    "emotional_intensity": "Medium-high with steady conviction rather than hype.",
    "emoji_usage": "Rare. Emojis are mostly avoided.",
    "punctuation_style": "Frequent periods, occasional em-dash style pauses, very limited exclamation marks. Uses line breaks to isolate tension or emphasize contrast.",
    "preferred_devices": [
        "contrast",
        "confession",
        "open loop",
        "rhetorical question",
        "reframing",
        "tension-release"
    ],
    "banned_phrases": [
        "game changer",
        "unlock your potential",
        "in today's world"
    ],
    "preferred_phrases": [
        "I was wrong about this",
        "here's the part nobody tells you",
        "this is where it gets interesting"
    ],

    "narrative_behavior": {
        "opening_pattern": "Usually opens with tension, a mistaken belief, or a sharp contradiction that forces curiosity.",
        "idea_progression": [
            "introduce tension or mistaken assumption",
            "reveal the real problem",
            "reframe the lesson",
            "turn it into a practical takeaway",
            "leave an open loop or redirect to deeper explanation"
        ],
        "tension_pattern": "Creates tension by exposing a false assumption, delays resolution, then releases it through a sharper underlying truth.",
        "teaching_pattern": "Teaches by reframing mistakes into lessons, simplifying the core insight, and translating it into practical action.",
        "authority_pattern": "Builds authority through self-correction, confident specificity, and hard-earned operational lessons instead of credentials.",
        "closing_pattern": "Usually closes with a concise takeaway, then redirects to the fuller explanation with a curiosity gap."
    },

    "cognitive_style": {
        "reasoning_style": [
            "first-principles",
            "diagnostic",
            "contrast-driven",
            "systems-aware"
        ],
        "decision_lens": [
            "leverage",
            "signal over noise",
            "distribution over volume",
            "practical outcomes over theory"
        ],
        "abstraction_pattern": "Starts with a specific mistake or observation, extracts the underlying principle, then generalizes it into a repeatable rule.",
        "problem_solving_style": "Identifies the wrong assumption first, isolates the actual bottleneck, then reframes the solution around leverage.",
        "common_reframes": [
            "the problem is not X, it's Y",
            "more is not the answer, better distribution is",
            "the bottleneck is not effort, it's leverage"
        ]
    },

    "constraint_profile": {
        "avoids": [
            "generic motivation",
            "empty inspiration",
            "broad self-help language",
            "abstract advice without mechanism"
        ],
        "never_does": [
            "overexplains simple points",
            "uses hype without proof",
            "sounds overly polished or corporate",
            "ends with aggressive sales energy"
        ],
        "overuse_risks": [
            "becoming too dramatic",
            "overusing contrast until it feels formulaic",
            "sounding like generic high-agency creator copy",
            "turning every hook into manufactured urgency"
        ]
    },

    "voice_anchors": [
        "opens with a mistaken belief and flips it",
        "uses contrast as the primary tension engine",
        "turns observations into operational lessons",
        "builds authority through self-correction",
        "closes with an open loop instead of a hard sell"
    ],

    "style_summary": "Writes with sharp conviction, using contrast and self-correction to turn mistakes into practical lessons. The voice is direct and high-signal, but the real signature is the way it reframes assumptions into operational insight."
}
