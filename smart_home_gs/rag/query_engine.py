"""RAG query engine: classify → retrieve → synthesize over caption history."""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from .embeddings import embed
from .milvus_store import MilvusCaptionStore

logger = logging.getLogger(__name__)

USER_TZ = ZoneInfo("Asia/Seoul")
EVENT_GAP_SEC = 120
EVENT_MIN_DURATION_SEC = 30

# Hardcoded expansion queries for common event labels (LLM fallback for others)
_EVENT_EXPANSIONS = {
    "meeting": "multiple occupants group gathering meeting many sensors active chairs",
    "empty": "room empty no occupancy no motion no activity idle far",
    "empty room": "room empty no occupancy no motion no activity idle far",
    "studying": "single occupant stationary desk work focused studying one person",
    "study": "single occupant stationary desk work focused studying one person",
    "occupied": "occupant present near motion active distance infrared",
    "hot": "temperature hot cooling AC needed",
    "cold": "temperature cold heating needed",
}


def _parse_iso_to_epoch(iso_str: Optional[str]) -> Optional[float]:
    """Parse an ISO8601 string to UTC epoch. Returns None on failure."""
    if not iso_str:
        return None
    try:
        from dateutil import parser as dateutil_parser

        dt = dateutil_parser.parse(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=USER_TZ)
        return dt.timestamp()
    except Exception:
        return None


def _epoch_to_kst_str(epoch: float) -> str:
    """Convert UTC epoch to KST display string."""
    dt = datetime.fromtimestamp(epoch, tz=USER_TZ)
    return dt.strftime("%Y-%m-%d %H:%M:%S+09:00")


def _epoch_to_kst_time(epoch: float) -> str:
    """Convert UTC epoch to KST time-only string."""
    dt = datetime.fromtimestamp(epoch, tz=USER_TZ)
    return dt.strftime("%H:%M:%S")


def _format_duration(seconds: float) -> str:
    """Format seconds into human-readable Xm Ys."""
    m, s = divmod(int(seconds), 60)
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


def _classify_query(llm, message: str) -> Dict[str, Any]:
    """Use LLM to classify the user query into type + structured fields."""
    now_kst = datetime.now(USER_TZ).isoformat()
    prompt = (
        "You are a query classifier for a smart-room sensor monitoring system.\n"
        "Classify the user's question and output STRICT JSON with these fields:\n"
        "{\n"
        '  "type": "time_range" | "semantic" | "event_search" | "hybrid",\n'
        '  "start_iso": "ISO8601 or null",\n'
        '  "end_iso": "ISO8601 or null",\n'
        '  "semantic_query": "rephrased query for vector search, or null",\n'
        '  "event_label": "short noun phrase like meeting or empty room, or null",\n'
        '  "expansion_query": "expanded search terms for the event label, or null"\n'
        "}\n\n"
        "Rules:\n"
        '- "when was there a X" / "what time was the X" / "find all X" → event_search with event_label.\n'
        '- "what happened between A and B" / "summarize A to B" → time_range.\n'
        '- "was X ever on", "did anyone Y" without a time window → semantic.\n'
        "- Time range mentioned + specific question → hybrid.\n"
        f"- Current time (KST): {now_kst}. Parse user times as Asia/Seoul unless TZ is explicit.\n"
        "- For event_search, also fill expansion_query with 5-10 descriptive terms for vector search.\n"
        "- Output ONLY the JSON object, no markdown fences, no extra text.\n\n"
        f"User question: {message}\n\n"
        "JSON:"
    )
    response = llm.generate_response({"text": prompt}, max_new_tokens=256, temperature=0.1)
    text = (response or "").strip()
    # Try to extract JSON from response
    # Strip markdown fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON object in text
        match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
            except json.JSONDecodeError:
                parsed = {}
        else:
            parsed = {}

    # Validate and normalize
    valid_types = {"time_range", "semantic", "event_search", "hybrid"}
    qtype = parsed.get("type", "semantic")
    if qtype not in valid_types:
        qtype = "semantic"

    result = {
        "type": qtype,
        "start_iso": parsed.get("start_iso"),
        "end_iso": parsed.get("end_iso"),
        "semantic_query": parsed.get("semantic_query"),
        "event_label": parsed.get("event_label"),
        "expansion_query": parsed.get("expansion_query"),
    }

    # Parse times to epochs
    result["start_epoch"] = _parse_iso_to_epoch(result["start_iso"])
    result["end_epoch"] = _parse_iso_to_epoch(result["end_iso"])

    # If times failed to parse, downgrade to semantic
    if qtype in ("time_range", "hybrid") and result["start_epoch"] is None and result["end_epoch"] is None:
        result["type"] = "semantic"

    # Ensure semantic_query for types that need it
    if result["type"] in ("semantic", "hybrid", "event_search") and not result["semantic_query"]:
        result["semantic_query"] = message

    return result


def _retrieve_time_range(
    store: MilvusCaptionStore,
    start_epoch: float,
    end_epoch: float,
    room_id: Optional[str],
) -> List[Dict[str, Any]]:
    """Retrieve captions in a time range, downsampling if too many."""
    rows = store.search_time_range(start_epoch, end_epoch, room_id=room_id, limit=500)
    if len(rows) > 200:
        # Downsample to ~50 evenly spaced, keeping first and last
        step = max(1, len(rows) // 50)
        sampled = [rows[0]]
        for i in range(step, len(rows) - 1, step):
            sampled.append(rows[i])
        sampled.append(rows[-1])
        rows = sampled
    return rows


def _retrieve_semantic(
    store: MilvusCaptionStore,
    query: str,
    top_k: int = 10,
    ts_range: Optional[Tuple[float, float]] = None,
    room_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Semantic vector search."""
    vec = embed(query)
    return store.search_semantic(vec, top_k=top_k, ts_range=ts_range, room_id=room_id)


def _cluster_events(
    hits: List[Dict[str, Any]],
    gap_sec: float = EVENT_GAP_SEC,
    min_duration_sec: float = EVENT_MIN_DURATION_SEC,
) -> List[Dict[str, Any]]:
    """Cluster hits into time intervals by ts_unix proximity."""
    if not hits:
        return []
    sorted_hits = sorted(hits, key=lambda h: h.get("ts_unix", 0))
    clusters: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = [sorted_hits[0]]

    for hit in sorted_hits[1:]:
        if hit["ts_unix"] - current[-1]["ts_unix"] <= gap_sec:
            current.append(hit)
        else:
            clusters.append(current)
            current = [hit]
    clusters.append(current)

    intervals = []
    for cluster in clusters:
        start_unix = cluster[0]["ts_unix"]
        # Use window_end_unix of last hit if available, else ts_unix + 30
        end_unix = cluster[-1].get("window_end_unix", cluster[-1]["ts_unix"] + 30)
        duration = end_unix - start_unix
        if duration < min_duration_sec:
            continue

        # Sample captions: first, middle, last
        sample_captions = []
        indices = [0]
        if len(cluster) > 2:
            indices.append(len(cluster) // 2)
        if len(cluster) > 1:
            indices.append(len(cluster) - 1)
        for idx in indices:
            cap = cluster[idx].get("caption", "")
            if cap:
                sample_captions.append(cap)

        intervals.append({
            "start_unix": start_unix,
            "end_unix": end_unix,
            "duration_sec": duration,
            "member_count": len(cluster),
            "member_ts": [h["ts_unix"] for h in cluster],
            "sample_captions": sample_captions,
        })

    return intervals


def _synthesize_time_range(llm, query: str, rows: List[Dict[str, Any]]) -> str:
    """Build synthesis prompt for time_range queries."""
    if not rows:
        return "No matching activity was recorded for that time range."

    captions_block = "\n".join(
        f"[#{i+1}] {_epoch_to_kst_str(r.get('ts_unix', 0))}: {r.get('caption', '')}"
        for i, r in enumerate(rows)
    )
    prompt = (
        "You are a smart-room assistant. The user asked about a time range.\n"
        "Below are sensor captions from that period (numbered, in KST timezone).\n\n"
        f"User question: {query}\n\n"
        f"Captions:\n{captions_block}\n\n"
        "Instructions:\n"
        "- Write one concise operational summary paragraph.\n"
        "- Cite specific captions as [#N] when referencing events.\n"
        "- If evidence is empty, say 'No matching activity was recorded.' Do not invent timestamps or events.\n\n"
        "Summary:"
    )
    response = llm.generate_response({"text": prompt}, max_new_tokens=512, temperature=0.3)
    return (response or "No matching activity was recorded.").strip()
    # return (response or "Error").strip()


def _synthesize_semantic(llm, query: str, rows: List[Dict[str, Any]]) -> str:
    """Build synthesis prompt for semantic/hybrid queries."""
    if not rows:
        return "No matching activity was recorded."

    captions_block = "\n".join(
        f"[#{i+1}] {_epoch_to_kst_str(r.get('ts_unix', 0))}: {r.get('caption', '')}"
        for i, r in enumerate(rows)
    )
    prompt = (
        "You are a smart-room assistant. The user asked a question about the room.\n"
        "Below are the most relevant sensor captions (numbered, KST timezone).\n\n"
        f"User question: {query}\n\n"
        f"Captions:\n{captions_block}\n\n"
        "Instructions:\n"
        "- Give a direct answer to the question.\n"
        "- Cite captions as [#N] as evidence.\n"
        "- If evidence is empty, say 'No matching activity was recorded.' Do not invent timestamps or events.\n\n"
        "Answer:"
    )
    response = llm.generate_response({"text": prompt}, max_new_tokens=512, temperature=0.3)
    return (response or "No matching activity was recorded.").strip()


def _synthesize_event_search(llm, query: str, intervals: List[Dict[str, Any]]) -> str:
    """Build synthesis prompt for event_search queries."""
    if not intervals:
        return "No matching events were found."

    interval_block = ""
    for iv in intervals:
        start_kst = _epoch_to_kst_time(iv["start_unix"])
        end_kst = _epoch_to_kst_time(iv["end_unix"])
        dur = _format_duration(iv["duration_sec"])
        samples = " | ".join(iv.get("sample_captions", [])[:3])
        interval_block += f"- {start_kst} to {end_kst} KST ({dur}), {iv['member_count']} records. Samples: {samples}\n"

    prompt = (
        "You are a smart-room assistant. The user searched for events.\n"
        "Below are clustered time intervals where matching activity was detected.\n\n"
        f"User question: {query}\n\n"
        f"Intervals:\n{interval_block}\n"
        "Instructions:\n"
        "- List each interval as: bullet '• HH:MM:SS – HH:MM:SS KST (Xm Ys) — <one-sentence description grounded in the sample captions>'\n"
        "- End with one short closing sentence with overall count.\n"
        "- Do not invent beyond what the captions state.\n\n"
        "Answer:"
    )
    response = llm.generate_response({"text": prompt}, max_new_tokens=512, temperature=0.3)
    return (response or "No matching events were found.").strip()


def answer(
    message: str,
    room_id: str,
    llm: Any,
    store: MilvusCaptionStore,
    embedding_model: str = "jinaai/jina-clip-v1",
) -> Dict[str, Any]:
    """Main entry point: classify, retrieve, synthesize."""

    # Step 1: Classify
    classification = _classify_query(llm, message)
    qtype = classification["type"]
    logger.info("Query classified: type=%s, classification=%s", qtype, json.dumps(classification, default=str))

    start_epoch = classification.get("start_epoch")
    end_epoch = classification.get("end_epoch")
    semantic_query = classification.get("semantic_query") or message
    event_label = classification.get("event_label")
    expansion_query = classification.get("expansion_query")

    result: Dict[str, Any] = {"query_type": qtype}

    # Step 2: Retrieve
    if qtype == "time_range":
        if start_epoch is None or end_epoch is None:
            result["answer"] = "Could not parse the time range from your question. Please specify start and end times."
            result["citations"] = []
            return result
        rows = _retrieve_time_range(store, start_epoch, end_epoch, room_id)
        answer_text = _synthesize_time_range(llm, message, rows)
        result["answer"] = answer_text
        result["citations"] = [
            {"n": i + 1, "ts_iso_kst": _epoch_to_kst_str(r.get("ts_unix", 0)), "caption": r.get("caption", "")}
            for i, r in enumerate(rows)
        ]

    elif qtype == "semantic":
        rows = _retrieve_semantic(store, semantic_query, top_k=10, room_id=room_id)
        answer_text = _synthesize_semantic(llm, message, rows)
        result["answer"] = answer_text
        result["citations"] = [
            {"n": i + 1, "ts_iso_kst": _epoch_to_kst_str(r.get("ts_unix", 0)), "caption": r.get("caption", "")}
            for i, r in enumerate(rows)
        ]

    elif qtype == "hybrid":
        ts_range = None
        if start_epoch is not None and end_epoch is not None:
            ts_range = (start_epoch, end_epoch)
        rows = _retrieve_semantic(store, semantic_query, top_k=15, ts_range=ts_range, room_id=room_id)
        answer_text = _synthesize_semantic(llm, message, rows)
        result["answer"] = answer_text
        result["citations"] = [
            {"n": i + 1, "ts_iso_kst": _epoch_to_kst_str(r.get("ts_unix", 0)), "caption": r.get("caption", "")}
            for i, r in enumerate(rows)
        ]

    elif qtype == "event_search":
        # Build expansion query
        eq = expansion_query
        if not eq and event_label:
            eq = _EVENT_EXPANSIONS.get(event_label.lower())
        if not eq:
            eq = semantic_query

        ts_range = None
        if start_epoch is not None and end_epoch is not None:
            ts_range = (start_epoch, end_epoch)

        hits = _retrieve_semantic(store, eq, top_k=100, ts_range=ts_range, room_id=room_id)

        # Filter by similarity threshold
        hits = [h for h in hits if h.get("distance", 0) >= 0.55]

        # Cluster into intervals
        intervals = _cluster_events(hits)

        answer_text = _synthesize_event_search(llm, message, intervals)
        result["answer"] = answer_text
        result["intervals"] = [
            {
                "start_iso_kst": _epoch_to_kst_str(iv["start_unix"]),
                "end_iso_kst": _epoch_to_kst_str(iv["end_unix"]),
                "duration_sec": iv["duration_sec"],
                "summary": "",  # filled below
            }
            for iv in intervals
        ]
        result["citations"] = [
            {"n": i + 1, "ts_iso_kst": _epoch_to_kst_str(h.get("ts_unix", 0)), "caption": h.get("caption", "")}
            for i, h in enumerate(hits[:20])
        ]
        # Extract interval summaries from the answer text (best-effort)
        # The LLM answer already contains the formatted intervals
    else:
        result["answer"] = "I couldn't understand the query type. Please try rephrasing."
        result["citations"] = []

    return result
