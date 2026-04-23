from agents.execution_agent import run_execution_pipeline

execution_plan = [
    {
        "task_id": 1,
        "input": "Poor distribution is the main reason for content growth failure",
        "platform": "twitter",
        "output_type": "tweet_thread",
        "goal": "Spark conversation"
    },
    {
        "task_id": 2,
        "input": "Creators prioritize creation over distribution",
        "platform": "tiktok",
        "output_type": "short_video",
        "goal": "Viral awareness"
    }
]

results = run_execution_pipeline(execution_plan)

print("\n=== RESULTS ===")
print(results)