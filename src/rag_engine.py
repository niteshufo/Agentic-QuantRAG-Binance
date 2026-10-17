"""Persistent local market-log retrieval and sentiment analysis."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import secrets
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Protocol


LOGGER = logging.getLogger(__name__)

DEFAULT_PERSIST_DIRECTORY: Final[Path] = Path("data") / "chroma_db"
DEFAULT_COLLECTION_NAME: Final[str] = "market_logs"
DEFAULT_TOP_K: Final[int] = 3
DEFAULT_EMBEDDING_DIMENSION: Final[int] = 256
MAX_TEXT_LENGTH: Final[int] = 50_000
MIN_RETRIEVAL_SCORE: Final[float] = 0.25
NEUTRAL_SENTIMENT_SCORE: Final[float] = 0.0

_WORD_PATTERN: Final[re.Pattern[str]] = re.compile(r"[a-z0-9_'-]+")
_BULLISH_TERMS: Final[frozenset[str]] = frozenset(
    {
        "bullish",
        "bull",
        "rally",
        "surge",
        "growth",
        "breakout",
        "inflow",
        "inflows",
        "strong",
        "support",
        "recovery",
        "optimistic",
        "accumulate",
        "gains",
        "adoption",
        "liquidity",
        "deep",
    }
)
_BEARISH_TERMS: Final[frozenset[str]] = frozenset(
    {
        "bearish",
        "bear",
        "crash",
        "selloff",
        "decline",
        "outflow",
        "weak",
        "resistance",
        "liquidation",
        "panic",
        "risk",
        "recession",
        "losses",
        "regulatory",
        "regulations",
        "margin",
        "cuts",
        "crash",
        "derivative",
        "derivatives",
        "volatility",
    }
)


class RAGEngineError(Exception):
    """Base class for expected RAG engine failures."""


class RAGConfigurationError(RAGEngineError):
    """The RAG engine configuration is invalid."""


class RAGValidationError(RAGEngineError):
    """A document or query failed validation."""


class RAGStorageError(RAGEngineError):
    """The vector store could not be initialized or persisted."""


class RAGProviderError(RAGEngineError):
    """An optional embedding or vector-store provider failed."""


@dataclass(frozen=True, slots=True)
class MarketLogDocument:
    """Validated text and metadata stored in the local vector database."""

    document_id: str
    text: str
    timestamp: datetime
    source: str
    metadata: dict[str, str]


@dataclass(frozen=True, slots=True)
class RetrievedMarketLog:
    """One retrieved document with a similarity score."""

    document: MarketLogDocument
    similarity: float


@dataclass(frozen=True, slots=True)
class SentimentAnalysis:
    """Evidence-backed macro sentiment output."""

    query: str
    score: float
    confidence: float
    retrieved_documents: tuple[RetrievedMarketLog, ...]
    source_mode: str
    analyzed_at: datetime


class EmbeddingProvider(Protocol):
    """Small provider boundary for open-source or deterministic embeddings."""

    @property
    def dimension(self) -> int:
        """Return the embedding dimension."""

    def embed(self, text: str) -> tuple[float, ...]:
        """Embed one validated text block."""


class _PersistentVectorStore(Protocol):
    """Internal protocol shared by Chroma and local fallback stores."""

    def add(self, document: MarketLogDocument, embedding: tuple[float, ...]) -> None:
        """Persist one document and vector."""

    def query(
        self,
        embedding: tuple[float, ...],
        top_k: int,
    ) -> tuple[RetrievedMarketLog, ...]:
        """Return nearest documents."""


class HashEmbeddingProvider:
    """Dependency-free deterministic embedding used as the safe baseline.

    This is not a semantic language model. It provides stable vector-space
    behavior for local development and zero-downtime fallback. Deployments
    requiring semantic embeddings should inject an approved open-source
    LangChain-compatible embedding model through ``EmbeddingProvider``.
    """

    def __init__(self, *, dimension: int = DEFAULT_EMBEDDING_DIMENSION) -> None:
        if dimension <= 0:
            raise RAGConfigurationError("Embedding dimension must be positive.")
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, text: str) -> tuple[float, ...]:
        tokens = _WORD_PATTERN.findall(text.lower())
        vector = [0.0] * self._dimension
        for token in tokens:
            digest = hashlib.blake2b(
                token.encode("utf-8"),
                digest_size=16,
            ).digest()
            index = int.from_bytes(digest[:8], "big") % self._dimension
            sign = 1.0 if digest[8] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm <= 0.0:
            return tuple(vector)
        return tuple(value / norm for value in vector)


class LangChainEmbeddingProvider:
    """Adapter for the local Hugging Face MiniLM LangChain embedding model.

    Imports and model loading remain lazy so a missing package or unavailable
    model can degrade to the local hash-backed JSON store without stopping the
    runtime.
    """

    def __init__(
        self,
        *,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        model_kwargs: Mapping[str, object] | None = None,
        encode_kwargs: Mapping[str, object] | None = None,
    ) -> None:
        if not model_name.strip():
            raise RAGConfigurationError("model_name must not be empty.")
        try:
            from langchain_huggingface import HuggingFaceEmbeddings

            self._model: Any = HuggingFaceEmbeddings(
                model_name=model_name,
                model_kwargs=dict(model_kwargs or {}),
                encode_kwargs=dict(encode_kwargs or {}),
            )
        except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            raise RAGProviderError(
                "LangChain Hugging Face embeddings are unavailable."
            ) from exc
        dimension = self._model.embed_query("dimension probe")
        if not isinstance(dimension, Sequence) or not dimension:
            raise RAGProviderError("Embedding model returned an invalid vector.")
        self._dimension = len(dimension)

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, text: str) -> tuple[float, ...]:
        try:
            vector = self._model.embed_query(text)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise RAGProviderError("LangChain embedding inference failed.") from exc
        if not isinstance(vector, Sequence) or len(vector) != self._dimension:
            raise RAGProviderError("LangChain returned an incompatible embedding.")
        converted = tuple(float(value) for value in vector)
        if not all(math.isfinite(value) for value in converted):
            raise RAGProviderError("LangChain returned a non-finite embedding.")
        return converted


class _LocalJsonVectorStore:
    """Small persistent cosine-similarity store used without ChromaDB."""

    def __init__(self, path: Path, *, dimension: int) -> None:
        self._path = path
        self._dimension = dimension
        self._records: dict[str, tuple[MarketLogDocument, tuple[float, ...]]] = {}
        self._load()

    def add(self, document: MarketLogDocument, embedding: tuple[float, ...]) -> None:
        self._validate_embedding(embedding)
        self._records[document.document_id] = (document, embedding)
        self._persist()

    def query(
        self,
        embedding: tuple[float, ...],
        top_k: int,
    ) -> tuple[RetrievedMarketLog, ...]:
        self._validate_embedding(embedding)
        scored = [
            RetrievedMarketLog(
                document=document,
                similarity=self._cosine_similarity(embedding, stored_embedding),
            )
            for document, stored_embedding in self._records.values()
        ]
        scored.sort(key=lambda result: result.similarity, reverse=True)
        return tuple(scored[:top_k])

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                raise ValueError("store root must be a list")
            for item in payload:
                if not isinstance(item, dict):
                    raise ValueError("store record must be an object")
                document = MarketLogDocument(
                    document_id=str(item["document_id"]),
                    text=str(item["text"]),
                    timestamp=datetime.fromisoformat(str(item["timestamp"])),
                    source=str(item["source"]),
                    metadata={
                        str(key): str(value)
                        for key, value in self._metadata_mapping(item["metadata"]).items()
                    },
                )
                embedding = tuple(float(value) for value in item["embedding"])
                self._validate_embedding(embedding)
                self._records[document.document_id] = (document, embedding)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RAGStorageError("Persistent local vector store is corrupted.") from exc

    @staticmethod
    def _metadata_mapping(value: object) -> Mapping[object, object]:
        if not isinstance(value, Mapping):
            raise ValueError("store metadata must be an object")
        return value

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = [
            {
                **asdict(document),
                "timestamp": document.timestamp.isoformat(),
                "embedding": list(embedding),
            }
            for document, embedding in self._records.values()
        ]
        temporary_path = self._path.with_suffix(f".{secrets.token_hex(6)}.tmp")
        try:
            temporary_path.write_text(
                json.dumps(payload, ensure_ascii=True),
                encoding="utf-8",
            )
            temporary_path.replace(self._path)
        except OSError as exc:
            raise RAGStorageError("Could not persist local vector store.") from exc

    def _validate_embedding(self, embedding: tuple[float, ...]) -> None:
        if len(embedding) != self._dimension:
            raise RAGProviderError("Embedding has an incompatible dimension.")
        if not all(math.isfinite(value) for value in embedding):
            raise RAGProviderError("Embedding contains a non-finite value.")

    @staticmethod
    def _cosine_similarity(
        left: tuple[float, ...],
        right: tuple[float, ...],
    ) -> float:
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if left_norm <= 0.0 or right_norm <= 0.0:
            return 0.0
        return sum(a * b for a, b in zip(left, right, strict=True)) / (
            left_norm * right_norm
        )


class _ChromaVectorStore:
    """ChromaDB adapter with an embedding function supplied by this module."""

    def __init__(
        self,
        persist_directory: Path,
        collection_name: str,
        embedding_provider: EmbeddingProvider,
    ) -> None:
        try:
            chromadb: Any = __import__("chromadb")
            client: Any = chromadb.PersistentClient(path=str(persist_directory))
            self._collection: Any = client.get_or_create_collection(
                name=collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            self._validate_dimension()
            self._embedding_provider = embedding_provider
        except (ImportError, AttributeError, TypeError, ValueError) as exc:
            raise RAGProviderError("ChromaDB is unavailable or incompatible.") from exc
        except (RuntimeError, RAGProviderError) as exc:
            raise RAGProviderError("ChromaDB vector dimensions are incompatible.") from exc

    def _validate_dimension(self) -> None:
        """Reject a collection created with a different embedding model."""

        count = int(self._collection.count())
        if count == 0:
            return
        sample: Any = self._collection.get(
            limit=1,
            include=["embeddings"],
        )
        embeddings = sample.get("embeddings") if isinstance(sample, Mapping) else None
        if embeddings is None or len(embeddings) == 0:
            raise RAGProviderError("Chroma collection embeddings are unavailable.")
        first_embedding = embeddings[0]
        if not isinstance(first_embedding, Sequence):
            raise RAGProviderError("Chroma collection embedding is malformed.")
        if len(first_embedding) != self._embedding_provider.dimension:
            raise RAGProviderError(
                "Chroma collection dimension does not match the embedding model."
            )

    def add(self, document: MarketLogDocument, embedding: tuple[float, ...]) -> None:
        try:
            self._collection.upsert(
                ids=[document.document_id],
                documents=[document.text],
                embeddings=[list(embedding)],
                metadatas=[self._metadata_for(document)],
            )
        except (TypeError, ValueError, RuntimeError) as exc:
            raise RAGProviderError("ChromaDB rejected the market-log document.") from exc

    def query(
        self,
        embedding: tuple[float, ...],
        top_k: int,
    ) -> tuple[RetrievedMarketLog, ...]:
        try:
            result: Any = self._collection.query(
                query_embeddings=[list(embedding)],
                n_results=top_k,
                include=["documents", "metadatas", "distances"],
            )
            documents = result.get("documents", [[]])[0]
            metadatas = result.get("metadatas", [[]])[0]
            distances = result.get("distances", [[]])[0]
        except (AttributeError, KeyError, TypeError, ValueError, RuntimeError) as exc:
            raise RAGProviderError("ChromaDB query failed.") from exc

        retrieved: list[RetrievedMarketLog] = []
        for text, metadata, distance in zip(
            documents,
            metadatas,
            distances,
            strict=False,
        ):
            if not isinstance(text, str) or not isinstance(metadata, Mapping):
                continue
            timestamp = _parse_timestamp(metadata.get("timestamp"))
            document = MarketLogDocument(
                document_id=str(metadata.get("document_id", "")),
                text=text,
                timestamp=timestamp,
                source=str(metadata.get("source", "unknown")),
                metadata={
                    str(key): str(value)
                    for key, value in metadata.items()
                    if key not in {"document_id", "timestamp", "source"}
                },
            )
            distance_value = float(distance)
            similarity = max(-1.0, min(1.0, 1.0 - distance_value))
            retrieved.append(
                RetrievedMarketLog(document=document, similarity=similarity)
            )
        return tuple(retrieved)

    @staticmethod
    def _metadata_for(document: MarketLogDocument) -> dict[str, str]:
        return {
            "document_id": document.document_id,
            "timestamp": document.timestamp.isoformat(),
            "source": document.source,
            **document.metadata,
        }


class MarketSentimentRAGEngine:
    """Persistent RAG engine with MiniLM retrieval and bounded sentiment scoring."""

    def __init__(
        self,
        *,
        persist_directory: Path = DEFAULT_PERSIST_DIRECTORY,
        collection_name: str = DEFAULT_COLLECTION_NAME,
        embedding_provider: EmbeddingProvider | None = None,
        top_k: int = DEFAULT_TOP_K,
        logger: logging.Logger | None = None,
    ) -> None:
        if not collection_name.strip():
            raise RAGConfigurationError("collection_name must not be empty.")
        if top_k <= 0:
            raise RAGConfigurationError("top_k must be positive.")
        self._persist_directory = persist_directory
        self._collection_name = collection_name
        self._top_k = min(top_k, DEFAULT_TOP_K)
        self._logger = logger or LOGGER
        if embedding_provider is None:
            self._embedding_provider, self._embedding_mode = (
                self._default_embedding_provider()
            )
        else:
            self._embedding_provider = embedding_provider
            self._embedding_mode = type(embedding_provider).__name__
        self._store = self._initialize_store()

    @property
    def storage_mode(self) -> str:
        """Return ``chroma`` or ``local_json`` for operational visibility."""

        if isinstance(self._store, _ChromaVectorStore):
            return "chroma"
        return "local_json"

    @property
    def embedding_mode(self) -> str:
        """Return the active semantic or deterministic embedding provider."""

        return self._embedding_mode

    def ingest_text(
        self,
        text: str,
        *,
        source: str = "synthetic_market_stream",
        timestamp: datetime | None = None,
        metadata: Mapping[str, str] | None = None,
        document_id: str | None = None,
    ) -> MarketLogDocument:
        """Validate, embed, and persist one market-log text payload."""

        validated_text = self._validate_text(text, field="text")
        validated_source = self._validate_text(source, field="source")
        event_time = timestamp or datetime.now(UTC)
        if event_time.tzinfo is None:
            raise RAGValidationError("timestamp must be timezone-aware.")
        event_time = event_time.astimezone(UTC)
        safe_metadata = self._validate_metadata(metadata or {})
        stable_id = document_id or self._document_id(
            validated_text,
            validated_source,
            event_time,
        )
        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", stable_id):
            raise RAGValidationError("document_id contains unsupported characters.")
        document = MarketLogDocument(
            document_id=stable_id,
            text=validated_text,
            timestamp=event_time,
            source=validated_source,
            metadata=safe_metadata,
        )
        embedding = self._embedding_provider.embed(validated_text)
        self._store.add(document, embedding)
        self._logger.info(
            "rag_ingest document_id=%s source=%s chars=%d storage_mode=%s",
            document.document_id,
            document.source,
            len(document.text),
            self.storage_mode,
        )
        return document

    def ingest_market_logs(
        self,
        texts: Iterable[str],
        *,
        source: str = "synthetic_market_stream",
    ) -> tuple[MarketLogDocument, ...]:
        """Ingest a finite iterable of market-log payloads."""

        documents = tuple(
            self.ingest_text(text, source=source)
            for text in texts
        )
        self._logger.info("rag_batch_ingest documents=%d", len(documents))
        return documents

    def analyze_macro_sentiment(self, query: str) -> SentimentAnalysis:
        """Retrieve up to three relevant logs and return a bounded score."""

        validated_query = self._validate_text(query, field="query")
        query_embedding = self._embedding_provider.embed(validated_query)
        retrieved = tuple(
            result
            for result in self._store.query(query_embedding, self._top_k)
            if result.similarity >= MIN_RETRIEVAL_SCORE
        )
        score, confidence = self._score_sentiment(retrieved)
        analysis = SentimentAnalysis(
            query=validated_query,
            score=score,
            confidence=confidence,
            retrieved_documents=retrieved,
            source_mode=self.storage_mode,
            analyzed_at=datetime.now(UTC),
        )
        self._logger.info(
            "rag_sentiment query_chars=%d retrieved=%d score=%.6f "
            "confidence=%.6f storage_mode=%s",
            len(validated_query),
            len(retrieved),
            analysis.score,
            analysis.confidence,
            analysis.source_mode,
        )
        return analysis

    def _initialize_store(self) -> _PersistentVectorStore:
        self._persist_directory.mkdir(parents=True, exist_ok=True)
        try:
            return _ChromaVectorStore(
                self._persist_directory,
                self._collection_name,
                self._embedding_provider,
            )
        except RAGProviderError as exc:
            fallback_path = self._persist_directory / "market_logs.json"
            self._logger.warning(
                "rag_storage_degraded mode=local_json reason=%s",
                type(exc).__name__,
            )
            try:
                return _LocalJsonVectorStore(
                    fallback_path,
                    dimension=self._embedding_provider.dimension,
                )
            except (RAGProviderError, RAGStorageError) as fallback_exc:
                if isinstance(self._embedding_provider, HashEmbeddingProvider):
                    raise
                self._logger.warning(
                    "rag_embedding_degraded mode=deterministic_hash reason=%s",
                    type(fallback_exc).__name__,
                )
                self._embedding_provider = HashEmbeddingProvider()
                self._embedding_mode = type(self._embedding_provider).__name__
                return _LocalJsonVectorStore(
                    fallback_path,
                    dimension=self._embedding_provider.dimension,
                )

    def _default_embedding_provider(
        self,
    ) -> tuple[EmbeddingProvider, str]:
        try:
            provider = LangChainEmbeddingProvider(
                model_name="sentence-transformers/all-MiniLM-L6-v2",
            )
        except RAGProviderError as exc:
            self._logger.warning(
                "rag_embedding_degraded mode=deterministic_hash reason=%s",
                type(exc).__name__,
            )
            provider = HashEmbeddingProvider()
        return provider, type(provider).__name__

    @staticmethod
    def _validate_text(value: str, *, field: str) -> str:
        if not isinstance(value, str):
            raise RAGValidationError(f"{field} must be a string.")
        normalized = value.strip()
        if not normalized:
            raise RAGValidationError(f"{field} must not be empty.")
        if len(normalized) > MAX_TEXT_LENGTH:
            raise RAGValidationError(
                f"{field} exceeds the {MAX_TEXT_LENGTH}-character limit."
            )
        if "\x00" in normalized:
            raise RAGValidationError(f"{field} contains a null character.")
        if not any(character.isprintable() or character.isspace() for character in normalized):
            raise RAGValidationError(f"{field} contains corrupted control characters.")
        return normalized

    @staticmethod
    def _validate_metadata(metadata: Mapping[str, str]) -> dict[str, str]:
        validated: dict[str, str] = {}
        for key, value in metadata.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise RAGValidationError("metadata keys and values must be strings.")
            safe_key = MarketSentimentRAGEngine._validate_text(key, field="metadata key")
            safe_value = MarketSentimentRAGEngine._validate_text(
                value,
                field=f"metadata[{safe_key}]",
            )
            validated[safe_key] = safe_value
        return validated

    @staticmethod
    def _document_id(text: str, source: str, timestamp: datetime) -> str:
        digest = hashlib.sha256(
            f"{source}|{timestamp.isoformat()}|{text}".encode("utf-8")
        ).hexdigest()
        return f"market-log-{digest[:32]}"

    @staticmethod
    def _score_sentiment(
        retrieved: Sequence[RetrievedMarketLog],
    ) -> tuple[float, float]:
        if not retrieved:
            return NEUTRAL_SENTIMENT_SCORE, 0.0
        weighted_scores: list[float] = []
        weights: list[float] = []
        for result in retrieved:
            tokens = set(_WORD_PATTERN.findall(result.document.text.lower()))
            bullish = len(tokens & _BULLISH_TERMS)
            bearish = len(tokens & _BEARISH_TERMS)
            total_terms = bullish + bearish
            text_score = (
                0.0
                if total_terms == 0
                else (bullish - bearish) / float(total_terms)
            )
            weight = max(result.similarity, MIN_RETRIEVAL_SCORE)
            weighted_scores.append(text_score * weight)
            weights.append(weight)
        denominator = sum(weights)
        score = 0.0 if denominator <= 0.0 else sum(weighted_scores) / denominator
        confidence = min(1.0, denominator / float(len(retrieved)))
        return max(-1.0, min(1.0, score)), confidence


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        return datetime.now(UTC)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(UTC)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


__all__ = [
    "EmbeddingProvider",
    "HashEmbeddingProvider",
    "LangChainEmbeddingProvider",
    "MarketLogDocument",
    "MarketSentimentRAGEngine",
    "RAGConfigurationError",
    "RAGEngineError",
    "RAGProviderError",
    "RAGStorageError",
    "RAGValidationError",
    "RetrievedMarketLog",
    "SentimentAnalysis",
]
