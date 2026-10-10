"""
Client for an OpenAI-compatible embeddings endpoint (llama.cpp, Ollama, vLLM,
or a hosted API). Optional: nothing uses it unless EMBEDDINGS_API_URL is set.
"""

import os

import niquests
import numpy as np

from helpers.user_agent import USER_AGENT

DEFAULT_MODEL = "bge-m3"


class EmbeddingsError(Exception):
    """Raised when the embeddings endpoint is not configured or answers badly."""


def api_url() -> str | None:
    """Base URL of the endpoint, from EMBEDDINGS_API_URL (e.g. http://127.0.0.1:8918/v1)."""
    value = os.getenv("EMBEDDINGS_API_URL", "").strip()
    return value.rstrip("/") or None


def model() -> str:
    """Model name sent to the endpoint, from EMBEDDINGS_MODEL."""
    return os.getenv("EMBEDDINGS_MODEL", "").strip() or DEFAULT_MODEL


def is_configured() -> bool:
    return api_url() is not None


async def embed(
    texts: list[str],
    timeout: float = 60.0,
    session: niquests.AsyncSession | None = None,
) -> np.ndarray:
    """
    Embed texts. Returns one unit-length float32 row per text, in order.

    Raises:
        EmbeddingsError: not configured, or an unusable answer.
        niquests.RequestException: the endpoint cannot be reached.
    """
    base_url = api_url()
    if base_url is None:
        raise EmbeddingsError("EMBEDDINGS_API_URL is not set.")
    headers = {"User-Agent": USER_AGENT}
    api_key = os.getenv("EMBEDDINGS_API_KEY", "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    own = session is None
    if own:
        session = niquests.AsyncSession()
    assert session is not None
    try:
        resp = await session.post(
            f"{base_url}/embeddings",
            json={"model": model(), "input": texts},
            headers=headers,
            timeout=timeout,
        )
        resp.raise_for_status()
        payload = resp.json()
    finally:
        if own:
            await session.close()

    try:
        items = sorted(payload["data"], key=lambda item: item["index"])
        vectors = np.asarray([item["embedding"] for item in items], dtype=np.float32)
    except (KeyError, TypeError, ValueError) as e:
        raise EmbeddingsError(f"Unexpected answer from the embeddings endpoint: {e}")
    if vectors.ndim != 2 or len(vectors) != len(texts):
        raise EmbeddingsError(
            f"Expected {len(texts)} embeddings, got an array of shape {vectors.shape}."
        )
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.where(norms == 0, 1.0, norms)
