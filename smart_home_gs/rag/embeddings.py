"""Embedding layer using sentence-transformers with jina-clip-v1.

Module-level singleton so the model loads once per process.
"""

import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

DIM = 768

_model = None
_model_name: Optional[str] = None


def _load_model(model_name: str = "jinaai/jina-clip-v1"):
    global _model, _model_name
    if _model is not None and _model_name == model_name:
        return _model
    from sentence_transformers import SentenceTransformer

    logger.info("Loading embedding model: %s", model_name)
    _model = SentenceTransformer(model_name, trust_remote_code=True)
    _model_name = model_name
    return _model


def embed(text: str, model_name: str = "jinaai/jina-clip-v1") -> List[float]:
    """Embed a single text string. Returns list of floats with length DIM."""
    model = _load_model(model_name)
    vec = model.encode([text], normalize_embeddings=True)
    return vec[0].tolist()


def embed_batch(texts: List[str], model_name: str = "jinaai/jina-clip-v1") -> List[List[float]]:
    """Embed a batch of text strings. Returns list of float-lists."""
    if not texts:
        return []
    model = _load_model(model_name)
    vecs = model.encode(texts, normalize_embeddings=True, batch_size=32)
    return [v.tolist() for v in vecs]
