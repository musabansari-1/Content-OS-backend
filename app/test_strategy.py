from agents.strategy_agent import generate_strategy

dummy_transcript = """
This video explains why most creators fail at content growth.
The main reason is poor distribution, not content quality.
Creators focus too much on creating instead of distributing.
"""

strategy = generate_strategy(dummy_transcript)

print("\n=== STRATEGY OUTPUT ===")
print(strategy)