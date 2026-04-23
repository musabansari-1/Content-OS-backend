# utils/llm.py

from groq import Groq

client = Groq()

def call_llm(system_prompt, user_prompt):
    response = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.7,
    )

    return response.choices[0].message.content