"""Milvus Lite vector store for smart-room captions.

Degrades gracefully: if pymilvus is unavailable, exports MilvusUnavailable
sentinel so callers can skip writes.
"""

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    from pymilvus import (
        CollectionSchema,
        DataType,
        FieldSchema,
        MilvusClient,
    )

    _PYMILVUS_AVAILABLE = True
except ImportError:
    _PYMILVUS_AVAILABLE = False
    logger.warning("pymilvus not installed — Milvus store disabled.")


class MilvusUnavailable:
    """Sentinel returned when pymilvus is not installed."""

    def __bool__(self):
        return False

    def __repr__(self):
        return "MilvusUnavailable(pymilvus not installed)"


COLLECTION_NAME = "smart_room_captions"
VECTOR_DIM = 768


def _build_schema():
    fields = [
        FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
        FieldSchema(name="room_id", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="ts_unix", dtype=DataType.DOUBLE),
        FieldSchema(name="ts_iso", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="window_end_unix", dtype=DataType.DOUBLE),
        FieldSchema(name="caption", dtype=DataType.VARCHAR, max_length=4096),
        FieldSchema(name="summary_json", dtype=DataType.VARCHAR, max_length=8192),
        FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=VECTOR_DIM),
    ]
    return CollectionSchema(fields=fields, description="Smart-room caption embeddings")


class MilvusCaptionStore:
    """Thin wrapper around Milvus Lite for caption storage and retrieval."""

    def __init__(self, db_path: str = "./data/captions.db"):
        if not _PYMILVUS_AVAILABLE:
            raise ImportError("pymilvus is not installed")
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._client = MilvusClient(uri=db_path)
        self._ensure_collection()

    def _ensure_collection(self):
        created = False
        if not self._client.has_collection(COLLECTION_NAME):
            schema = _build_schema()
            self._client.create_collection(
                collection_name=COLLECTION_NAME,
                schema=schema,
            )
            created = True
            logger.info("Created Milvus collection '%s'", COLLECTION_NAME)

        # Always ensure index exists (handles case where collection was created
        # but index creation failed on a previous run)
        try:
            indexes = self._client.list_indexes(COLLECTION_NAME)
            if not indexes:
                raise ValueError("no indexes")
        except Exception:
            logger.info("Creating index on '%s.embedding'", COLLECTION_NAME)
            index_params = self._client.prepare_index_params()
            index_params.add_index(
                field_name="embedding",
                index_type="AUTOINDEX",
                metric_type="COSINE",
            )
            self._client.create_index(
                collection_name=COLLECTION_NAME,
                index_params=index_params,
            )
            logger.info("Index created on '%s'", COLLECTION_NAME)

        if not created:
            logger.info("Milvus collection '%s' already exists", COLLECTION_NAME)

    def upsert(self, record: Dict[str, Any]) -> None:
        """Insert or replace a caption record. Dedupes by (room_id, ts_unix)."""
        room_id = record["room_id"]
        ts_unix = record["ts_unix"]
        # Delete existing record with same key
        self._client.delete(
            collection_name=COLLECTION_NAME,
            filter=f'room_id == "{room_id}" && ts_unix == {ts_unix}',
        )
        self._client.insert(
            collection_name=COLLECTION_NAME,
            data=[record],
        )

    def upsert_batch(self, records: List[Dict[str, Any]]) -> None:
        """Batch insert records (no per-record dedup — caller handles)."""
        if not records:
            return
        self._client.insert(
            collection_name=COLLECTION_NAME,
            data=records,
        )

    def search_semantic(
        self,
        vector: List[float],
        top_k: int = 10,
        ts_range: Optional[Tuple[float, float]] = None,
        room_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Vector similarity search with optional metadata filters."""
        filter_parts = []
        if ts_range is not None:
            filter_parts.append(f"ts_unix >= {ts_range[0]} && ts_unix <= {ts_range[1]}")
        if room_id is not None:
            filter_parts.append(f'room_id == "{room_id}"')
        filter_expr = " && ".join(filter_parts) if filter_parts else ""

        results = self._client.search(
            collection_name=COLLECTION_NAME,
            data=[vector],
            limit=top_k,
            output_fields=["room_id", "ts_unix", "ts_iso", "window_end_unix", "caption", "summary_json"],
            filter=filter_expr if filter_expr else None,
        )
        if not results or not results[0]:
            return []
        return [
            {**hit["entity"], "distance": hit["distance"]}
            for hit in results[0]
        ]

    def search_time_range(
        self,
        start_unix: float,
        end_unix: float,
        room_id: Optional[str] = None,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        """Filter-only query for a time range, ordered by ts_unix."""
        filter_parts = [f"ts_unix >= {start_unix} && ts_unix <= {end_unix}"]
        if room_id is not None:
            filter_parts.append(f'room_id == "{room_id}"')
        filter_expr = " && ".join(filter_parts)

        results = self._client.query(
            collection_name=COLLECTION_NAME,
            filter=filter_expr,
            output_fields=["room_id", "ts_unix", "ts_iso", "window_end_unix", "caption", "summary_json"],
            limit=limit,
        )
        return sorted(results, key=lambda r: r.get("ts_unix", 0))

    def count(self, room_id: Optional[str] = None) -> int:
        """Count records in collection, optionally filtered by room_id."""
        if room_id is not None:
            filter_expr = f'room_id == "{room_id}"'
        else:
            filter_expr = ""
        results = self._client.query(
            collection_name=COLLECTION_NAME,
            filter=filter_expr if filter_expr else None,
            output_fields=["id"],
        )
        return len(results)

    def exists(self, room_id: str, ts_unix: float) -> bool:
        """Check if a record with (room_id, ts_unix) already exists."""
        results = self._client.query(
            collection_name=COLLECTION_NAME,
            filter=f'room_id == "{room_id}" && ts_unix == {ts_unix}',
            output_fields=["id"],
            limit=1,
        )
        return len(results) > 0


def create_store(db_path: str = "./data/captions.db"):
    """Factory that returns MilvusCaptionStore or MilvusUnavailable sentinel."""
    if not _PYMILVUS_AVAILABLE:
        return MilvusUnavailable()
    try:
        return MilvusCaptionStore(db_path=db_path)
    except Exception as e:
        logger.error("Failed to create Milvus store: %s", e, exc_info=True)
        return MilvusUnavailable()
