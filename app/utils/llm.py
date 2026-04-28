import os
from pathlib import Path

from groq import Groq


UTILS_DIR = Path(__file__).resolve().parent
APP_DIR = UTILS_DIR.parent
BACKEND_DIR = APP_DIR.parent


def _load_env_file() -> None:
    env_path = BACKEND_DIR / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_env_file()

groq_api_key = os.getenv("GROQ_API_KEY")
if not groq_api_key:
    raise RuntimeError("GROQ_API_KEY is not set. Add it to backend/.env or your environment.")

client = Groq(api_key=groq_api_key)


def call_llm(system_prompt, user_prompt):
    response = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.7,
    )

    return response.choices[0].message.content
