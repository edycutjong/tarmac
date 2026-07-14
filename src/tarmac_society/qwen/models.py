"""Qwen Cloud surface constants — the ONLY model ids this project uses.

Role agents:   qwen3.7-plus       (persona fidelity + tool discipline, cheap
                                   enough that a ~60-turn society is
                                   affordable to *measure*, not just run)
Mediator:      qwen3.7-max        (+ thinking: adjudicating five conflicting
                                   position papers is the hardest step)
Citations:     text-embedding-v4  (regulation-passage retrieval)

Endpoint: DashScope international, OpenAI-compatible mode.
"""

ROLE_MODEL = "qwen3.7-plus"
MEDIATOR_MODEL = "qwen3.7-max"
EMBED_MODEL = "text-embedding-v4"

BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
API_KEY_ENV = "DASHSCOPE_API_KEY"

TEMPERATURE = 0.2  # bounded nondeterminism for the bench
