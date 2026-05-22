"""Graph-query benchmark: 30 hand-graded `where is X called?` questions.

The truth set lives in ``python_30.jsonl``. The runner is intentionally
deterministic — no LLM call — so a regression in the indexer / resolver is
the only thing that can move the score.
"""
