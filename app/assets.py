AVAILABLE_TARGET_ASSETS = {
    "x_post": {
        "asset_type": "x_post",
        "platform": "x",
        "format": "single_post",
        "output_type": "post",
        "label": "X Post",
        "description": "A concise, source-grounded single post tailored for X.",
    },
    "twitter_thread": {
        "asset_type": "twitter_thread",
        "platform": "twitter",
        "format": "thread",
        "output_type": "tweet_thread",
        "label": "Twitter Thread",
        "description": "A curiosity-driven thread that pulls readers toward the full video.",
    },
    "linkedin_post": {
        "asset_type": "linkedin_post",
        "platform": "linkedin",
        "format": "post",
        "output_type": "post",
        "label": "LinkedIn Post",
        "description": "A professional storytelling post with insight and a soft CTA.",
    },
    "tiktok_clip": {
        "asset_type": "tiktok_clip",
        "platform": "tiktok",
        "format": "short_video",
        "output_type": "short_video",
        "label": "TikTok Clip",
        "description": "A short-form script designed for retention and curiosity.",
    },
    "instagram_carousel": {
        "asset_type": "instagram_carousel",
        "platform": "instagram",
        "format": "carousel",
        "output_type": "carousel",
        "label": "Instagram Carousel",
        "description": "A multi-slide educational or story-driven carousel.",
    },
    "instagram_reel": {
        "asset_type": "instagram_reel",
        "platform": "instagram",
        "format": "reel",
        "output_type": "short_video",
        "label": "Instagram Reel",
        "description": "A reel script adapted for Instagram pacing and hooks.",
    },
    "youtube_video_idea": {
        "asset_type": "youtube_video_idea",
        "platform": "youtube",
        "format": "video_idea",
        "output_type": "video_idea",
        "label": "YouTube Video Idea",
        "description": "A title, hook, thumbnail angle, and outline package.",
    },
    "blog_post": {
        "asset_type": "blog_post",
        "platform": "blog",
        "format": "article",
        "output_type": "article",
        "label": "Blog Post",
        "description": "A long-form article that expands the video into a readable post.",
    },
    "reddit_post": {
        "asset_type": "reddit_post",
        "platform": "reddit",
        "format": "text_post",
        "output_type": "reddit_post",
        "label": "Reddit Post",
        "description": "A discussion-style title and body tailored for Reddit communities.",
    },
    "newsletter": {
        "asset_type": "newsletter",
        "platform": "email",
        "format": "newsletter",
        "output_type": "newsletter",
        "label": "Newsletter",
        "description": "A voice-driven newsletter edition with a strong narrative and CTA.",
    },
}

DEFAULT_TARGET_ASSETS = [
    "twitter_thread",
    "linkedin_post",
]


def get_asset_catalog() -> list[dict]:
    return [AVAILABLE_TARGET_ASSETS[key] for key in AVAILABLE_TARGET_ASSETS]


def build_asset_brief(target_assets: list[str]) -> list[dict]:
    return [AVAILABLE_TARGET_ASSETS[asset] for asset in target_assets]


def normalize_target_assets(target_assets: list[str] | None) -> list[str]:
    if not target_assets:
        return list(DEFAULT_TARGET_ASSETS)

    normalized = []
    for raw_asset in target_assets:
        asset = (raw_asset or "").strip()
        if not asset:
            continue

        if asset not in AVAILABLE_TARGET_ASSETS:
            raise ValueError(f"Unsupported target asset: {asset}")

        if asset not in normalized:
            normalized.append(asset)

    return normalized or list(DEFAULT_TARGET_ASSETS)
