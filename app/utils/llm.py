# import os
# from pathlib import Path

# from groq import Groq


# UTILS_DIR = Path(__file__).resolve().parent
# APP_DIR = UTILS_DIR.parent
# BACKEND_DIR = APP_DIR.parent


# def _load_env_file() -> None:
#     env_path = BACKEND_DIR / ".env"
#     if not env_path.exists():
#         return

#     for raw_line in env_path.read_text(encoding="utf-8").splitlines():
#         line = raw_line.strip()
#         if not line or line.startswith("#") or "=" not in line:
#             continue

#         key, value = line.split("=", 1)
#         os.environ.setdefault(key.strip(), value.strip())


# _load_env_file()

# groq_api_key = os.getenv("GROQ_API_KEY")
# if not groq_api_key:
#     raise RuntimeError("GROQ_API_KEY is not set. Add it to backend/.env or your environment.")

# client = Groq(api_key=groq_api_key)


# def call_llm(system_prompt, user_prompt):
#     response = client.chat.completions.create(
#         model="openai/gpt-oss-120b",
#         messages=[
#             {"role": "system", "content": system_prompt},
#             {"role": "user", "content": user_prompt},
#         ],
#         temperature=0.7,
#     )

#     return response.choices[0].message.content


from openai import OpenAI

from app.core.config import env, require_env


openrouter_api_key = require_env(
    "OPENROUTER_API_KEY",
    "OPENROUTER_API_KEY is not set. Add it to backend/.env or your environment."
)

client = OpenAI(
    api_key=openrouter_api_key,
    base_url="https://openrouter.ai/api/v1",
)


DEFAULT_LLM_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"

LLM_STAGE_SETTINGS = {
    "default": {"temperature": 0.7, "model": DEFAULT_LLM_MODEL},
    "moments": {"temperature": 0.2, "model": DEFAULT_LLM_MODEL},
    "strategy": {"temperature": 0.2, "model": DEFAULT_LLM_MODEL},
    "critic": {"temperature": 0.15, "model": DEFAULT_LLM_MODEL},
    "writer": {"temperature": 0.75, "model": DEFAULT_LLM_MODEL},
    "conversion": {"temperature": 0.45, "model": DEFAULT_LLM_MODEL},
}


def _env_float(name: str, default: float) -> float:
    raw_value = env(name)
    if raw_value is None or not raw_value.strip():
        return default

    try:
        return float(raw_value)
    except ValueError:
        return default


def _env_text(name: str) -> str | None:
    raw_value = env(name)
    if raw_value is None:
        return None

    value = raw_value.strip()
    return value or None


def _resolve_stage_settings(stage: str) -> dict:
    normalized_stage = (stage or "default").strip().lower()
    settings = LLM_STAGE_SETTINGS.get(
        normalized_stage,
        LLM_STAGE_SETTINGS["default"],
    )
    env_prefix = f"LLM_{normalized_stage.upper()}"

    return {
        "temperature": _env_float(
            f"{env_prefix}_TEMPERATURE",
            settings["temperature"],
        ),
        "model": (
            _env_text(f"{env_prefix}_MODEL")
            or _env_text("LLM_MODEL")
            or settings["model"]
        ),
    }


def call_llm(
    system_prompt,
    user_prompt,
    temperature=None,
    model=None,
    stage="default",
):
    stage_settings = _resolve_stage_settings(stage)
    resolved_temperature = (
        stage_settings["temperature"]
        if temperature is None
        else temperature
    )
    resolved_model = model or stage_settings["model"]

    response = client.chat.completions.create(
        model=resolved_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=resolved_temperature,
    )

    return response.choices[0].message.content
