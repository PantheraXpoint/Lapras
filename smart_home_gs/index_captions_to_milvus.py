#!/usr/bin/env python3
"""
Backfill Milvus with captions from logged JSON files.

Usage:
    python3 index_captions_to_milvus.py --logs-dir logs/ [--reindex] [--room-id n1_lab] [--db-path ./data/captions.db]
"""

import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from rag.embeddings import embed_batch
from rag.milvus_store import MilvusCaptionStore


def load_caption_windows(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("windows", [])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill Milvus with caption logs.")
    parser.add_argument("--logs-dir", type=str, default="logs/", help="Directory containing caption JSONs.")
    parser.add_argument("--reindex", action="store_true", help="Re-index existing records (delete + insert).")
    parser.add_argument("--room-id", type=str, default="n1_lab", help="Room ID to assign.")
    parser.add_argument("--db-path", type=str, default="./data/captions.db", help="Milvus Lite DB path.")
    parser.add_argument("--embedding-model", type=str, default="jinaai/jina-clip-v1", help="Embedding model name.")
    parser.add_argument("--batch-size", type=int, default=32, help="Embedding batch size.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    pattern = os.path.join(args.logs_dir, "*_captions_*.json")
    files = sorted(glob.glob(pattern))
    if not files:
        print(f"No caption files found matching: {pattern}")
        return

    print(f"Found {len(files)} caption file(s):")
    for f in files:
        print(f"  {f}")

    store = MilvusCaptionStore(db_path=args.db_path)

    all_windows: List[Dict[str, Any]] = []
    for fpath in files:
        windows = load_caption_windows(fpath)
        print(f"  Loaded {len(windows)} windows from {os.path.basename(fpath)}")
        all_windows.extend(windows)

    if not all_windows:
        print("No windows to index.")
        return

    # Filter already-indexed if not reindexing
    to_index: List[Dict[str, Any]] = []
    for w in all_windows:
        ts_unix = w.get("window_start_unix")
        if ts_unix is None:
            continue
        if not args.reindex and store.exists(args.room_id, ts_unix):
            continue
        to_index.append(w)

    print(f"\nWindows to index: {len(to_index)} (skipped {len(all_windows) - len(to_index)} already indexed)")

    if not to_index:
        print("Nothing to index.")
        total = store.count(room_id=args.room_id)
        print(f"Total records in collection for room '{args.room_id}': {total}")
        return

    # Batch embed and insert
    indexed = 0
    batch_size = args.batch_size
    for i in range(0, len(to_index), batch_size):
        batch = to_index[i : i + batch_size]
        captions = [w.get("caption", "") for w in batch]
        embeddings = embed_batch(captions, model_name=args.embedding_model)

        records = []
        for w, emb in zip(batch, embeddings):
            ts_unix = w["window_start_unix"]
            ts_iso = w.get("window_start_iso", datetime.fromtimestamp(ts_unix, tz=timezone.utc).isoformat())
            window_end_unix = w.get("window_end_unix", ts_unix + 30)
            summary = w.get("window_summary", {})

            if args.reindex:
                # Delete existing before batch insert
                store._client.delete(
                    collection_name="smart_room_captions",
                    filter=f'room_id == "{args.room_id}" && ts_unix == {ts_unix}',
                )

            records.append({
                "room_id": args.room_id,
                "ts_unix": ts_unix,
                "ts_iso": ts_iso,
                "window_end_unix": window_end_unix,
                "caption": w.get("caption", ""),
                "summary_json": json.dumps(summary, ensure_ascii=False)[:8192],
                "embedding": emb,
            })

        store.upsert_batch(records)
        indexed += len(records)

        if indexed % 50 < batch_size or indexed == len(to_index):
            print(f"  Indexed {indexed}/{len(to_index)} ...")

    total = store.count(room_id=args.room_id)
    print(f"\nDone. Indexed {indexed} windows.")
    print(f"Total records in collection for room '{args.room_id}': {total}")


if __name__ == "__main__":
    main()
