"""
Code embedding generator using sentence-transformers.

Uses CodeRankEmbed for code-specific embeddings with batched generation.
"""

from __future__ import annotations

import structlog
from typing import Optional

import numpy as np
import torch
from sentence_transformers import SentenceTransformer

logger = structlog.get_logger()

# Query prefix required by CodeRankEmbed
_QUERY_PREFIX = "Represent this query for searching relevant code: "


class CodeEmbedder:
    """Generate embeddings for code chunks."""

    def __init__(
        self,
        model_name: str = "nomic-ai/CodeRankEmbed",
        batch_size: Optional[int] = None,
        device: Optional[str] = None,
    ) -> None:
        self.model_name = model_name
        self._device = device or self._detect_device()
        # Smaller batch for CPU to avoid OOM on large codebases
        self.batch_size = batch_size if batch_size is not None else (32 if self._device == "cuda" else 4)
        self._model: Optional[SentenceTransformer] = None

    @staticmethod
    def _detect_device() -> str:
        """Detect best available device (skip MPS — known issues)."""
        if torch.cuda.is_available():
            return "cuda"
        return "cpu"

    @property
    def model(self) -> SentenceTransformer:
        """Lazy-load model on first use."""
        if self._model is None:
            logger.info("loading_embedding_model", model=self.model_name, device=self._device)
            self._model = SentenceTransformer(
                self.model_name, device=self._device, trust_remote_code=True
            )
            # Cap sequence length to reduce memory usage on CPU
            self._model.max_seq_length = 512
            logger.info("model_loaded", dim=self._model.get_embedding_dimension())
        return self._model

    def embed_chunks(self, texts: list[str]) -> np.ndarray:
        """Generate embeddings for a list of code chunks.

        Args:
            texts: List of code chunk texts.

        Returns:
            numpy array of shape (len(texts), embedding_dim)
        """
        if not texts:
            return np.array([])

        all_embeddings: list[np.ndarray] = []
        total_batches = (len(texts) + self.batch_size - 1) // self.batch_size

        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            batch_num = i // self.batch_size + 1
            if total_batches > 10 and (batch_num % 10 == 0 or batch_num == total_batches):
                percent = round((batch_num / total_batches) * 100, 1)
                logger.info(
                    "embedding_progress",
                    batch=batch_num,
                    total=total_batches,
                    percent=percent,
                )
            embeddings = self.model.encode(
                batch,
                show_progress_bar=False,
                normalize_embeddings=True,
                batch_size=self.batch_size,
            )
            all_embeddings.append(embeddings)

        logger.info("embedding_complete", batches=total_batches, texts=len(texts))

        return np.vstack(all_embeddings) if all_embeddings else np.array([])

    def embed_query(self, query: str) -> np.ndarray:
        """Generate embedding for a search query (with prefix).

        Args:
            query: Natural language search query.

        Returns:
            numpy array of shape (embedding_dim,)
        """
        prefixed = _QUERY_PREFIX + query
        embedding = self.model.encode(
            [prefixed],
            show_progress_bar=False,
            normalize_embeddings=True,
        )
        return embedding[0]

    @property
    def dimension(self) -> int:
        """Get embedding dimension."""
        return self.model.get_embedding_dimension()
