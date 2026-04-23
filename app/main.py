# main.py

from fastapi import FastAPI
from youtube_transcript_api import YouTubeTranscriptApi
from app.agents.strategy_agent import generate_strategy
from app.agents.execution_agent import run_execution_pipeline
from app.agents.execution_agent import generate_content
import json

app = FastAPI()

def transcript_to_text(transcript):
    return " ".join(
        snippet.text for snippet in transcript.snippets
    )

@app.get("/generate")
def generate(video_id: str):
    # 1. Get input
    ytt_api = YouTubeTranscriptApi()
    transcript = ytt_api.fetch(video_id)

    
    transcript = transcript_to_text(transcript)

    # 2. Strategy / Planner Agent
    strategy_output = generate_strategy(transcript)
    print(transcript)

    print(strategy_output)

    strategy_output = json.loads(strategy_output)



    # 3. Extract execution plan
    execution_plan = strategy_output["execution_plan"]

    print(execution_plan)

    # 4. Run Execution + Critic pipeline
    results = run_execution_pipeline(execution_plan)

    # 5. Return full system output
    return {
        "strategy": strategy_output,
        "results": results
    }