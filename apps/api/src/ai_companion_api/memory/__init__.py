"""Memory package — persona block, context builder, event-chain, recall, store.

Phase 3 adds the event-chain memory: salience-scored, embedded, prev_event-linked
events; recall ranks by cosine + salience + recency and returns intact chains;
``build_context`` injects ``[persona_block, salient_chains, recent_window, msg]``.
"""

from . import adaptive
from .context_builder import build_context
from .embeddings import cosine, embed, tokenize
from .event_chain import append_event
from .persona_block import build_persona_block, persona_prompt, tone_directives
from .recall import chains_to_messages, rank_and_chain
from .salience import SalienceScore, extract_emotion_tags, score_salience
from .store import InMemoryStore, MemoryStore, PostgresStore, UsageRecord, make_store

__all__ = [
    "InMemoryStore",
    "MemoryStore",
    "PostgresStore",
    "SalienceScore",
    "UsageRecord",
    "adaptive",
    "append_event",
    "build_context",
    "build_persona_block",
    "chains_to_messages",
    "cosine",
    "embed",
    "extract_emotion_tags",
    "make_store",
    "persona_prompt",
    "rank_and_chain",
    "score_salience",
    "tokenize",
    "tone_directives",
]
