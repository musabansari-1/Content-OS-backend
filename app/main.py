# main.py

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from youtube_transcript_api import YouTubeTranscriptApi
from app.agents.strategy_agent import generate_strategy
from app.agents.execution_agent import run_execution_pipeline
from app.agents.execution_agent import generate_content
from app.agents.moment_agent import extract_moments
import json

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

    moments = extract_moments(transcript)

    # 2. Strategy / Planner Agent
    # strategy_output = generate_strategy(transcript)
    strategy_output = generate_strategy({
    "transcript": transcript,
    "moments": moments
})
    print(transcript)

    print(strategy_output)

    strategy_output = json.loads(strategy_output)



    # 3. Extract execution plan
    execution_plan = strategy_output["execution_plan"]

    print(execution_plan)

    # 4. Run Execution + Critic pipeline
    results = run_execution_pipeline(execution_plan, transcript)

    # 5. Return full system output
    return {
        "strategy": strategy_output,
        "results": results
    }
