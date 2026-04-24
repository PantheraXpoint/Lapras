# LAPRAS++ Smart Home Ground Station

Real-time 3D sensor visualization, AI captioning, device control, and RAG-based chat for smart spaces.

## Features

- **3D Visualization** — A-Frame/Three.js rendering of a smart room with live sensor overlays
- **Live Captioning** — LLM generates natural-language descriptions every 30 seconds from sensor data
- **Device Control** — Manual and automated control of lights (Hue) and AC via MQTT
- **RAG Chat (VQA)** — Ask questions about historical room activity ("When was there a meeting?", "Was the room empty this morning?")
- **Multi-room** — Supports N1 Lab and Living Room configurations
- **Rule Automation** — TTL rule sets with manual/automated mode switching
- **Sensor Config** — Runtime sensor assignment per agent

## Prerequisites

```
Python 3.7+
paho-mqtt
```

The following are resolved from the parent LAPRAS workspace (must be on `sys.path`):

```
llms           # provides init_model() — loads QwenLM or other caption model
lapras_middleware   # provides MQTTMessage event structure
```

An MQTT broker must be reachable (default `143.248.55.82:1883`).

## Quick Start

### 1. Live mode (MQTT + sensors required)

```bash
python3 start_3d_visualization_stream.py \
  --mqtt-broker 143.248.55.82 \
  --mqtt-port 1883 \
  --http-port 8765 \
  --caption-model qwenlm \
  --caption-gpus 1 \
  --caption-window-sec 30
```

Open [http://localhost:8765](http://localhost:8765) in a browser.

### 2. Simulated mode (no MQTT needed)

```bash
python3 start_3d_visualization_simulated_stream.py --http-port 8765
```

Cycles through 3 synthetic sensor scenarios. Open the same URL.

### 3. Log sensor stream

```bash
python3 sensor_stream_logger.py \
  --minutes 5 \
  --broker 143.248.55.82 \
  --output logs/my_stream.json
```

### 4. Offline captioning from logs

```bash
python3 sensor_caption_infer.py \
  --input logs/sensor_stream_20260330_194218.json \
  --output logs/captions.json \
  --model qwenlm \
  --window-sec 30
```

### 5. Test captions (3 fixed cases)

```bash
python3 generate_three_case_captions.py --model qwenlm --gpus 1
```

## RAG Chat Setup

The chat panel in the 3D visualization supports natural-language questions over historical caption data. It uses Milvus Lite (local vector DB) and `jina-clip-v1` embeddings.

### Install dependencies

```bash
pip install -r requirements-rag.txt
```

This installs: `pymilvus`, `sentence-transformers`, `einops`, `timm`, `python-dateutil`.

### Backfill existing captions into Milvus

```bash
python3 index_captions_to_milvus.py --logs-dir logs/
```

Options:
| Flag | Default | Description |
|------|---------|-------------|
| `--logs-dir` | `logs/` | Directory with `*_captions_*.json` files |
| `--room-id` | `n1_lab` | Room ID to tag records with |
| `--db-path` | `./data/captions.db` | Milvus Lite DB path |
| `--reindex` | off | Delete + re-insert existing records |
| `--batch-size` | 32 | Embedding batch size |

### Start the server

Just start the server normally. If RAG dependencies are installed, the chat endpoint activates automatically. New captions are indexed in real time.

```bash
python3 start_3d_visualization_stream.py \
  --mqtt-broker 143.248.55.82 \
  --http-port 8765
```

RAG-specific flags:

| Flag | Default | Description |
|------|---------|-------------|
| `--milvus-db-path` | `./data/captions.db` | Milvus Lite DB file |
| `--embedding-model` | `jinaai/jina-clip-v1` | Sentence-transformer model |
| `--disable-rag` | off | Force-disable RAG (endpoint returns 503) |

### Supported query types

| Type | Example | What it does |
|------|---------|-------------|
| **Time range** | "What happened between 10am and 11am on April 2?" | Retrieves all captions in that window, synthesizes a summary |
| **Semantic** | "Was the AC on this afternoon?" | Vector-searches for relevant captions, gives a direct answer |
| **Event search** | "When was there a meeting?" | Finds matching captions, clusters them into time intervals |
| **Hybrid** | "Was anyone near the door between 2pm and 4pm?" | Combines time filter + vector search |

### Test with curl

```bash
# Time-range query
curl -s -X POST http://localhost:8765/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"what happened between 10 and 11 am on April 2 2026","room_id":"n1_lab"}' | python3 -m json.tool

# Event search
curl -s -X POST http://localhost:8765/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"when was there a meeting?","room_id":"n1_lab"}' | python3 -m json.tool

# Semantic
curl -s -X POST http://localhost:8765/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"was the room empty this morning?","room_id":"n1_lab"}' | python3 -m json.tool
```

### API response format

```json
{
  "answer": "rendered text with [#N] citations",
  "query_type": "time_range | semantic | event_search | hybrid",
  "citations": [
    {"n": 1, "ts_iso_kst": "2026-04-02 10:00:00+09:00", "caption": "..."}
  ],
  "intervals": [
    {"start_iso_kst": "...", "end_iso_kst": "...", "duration_sec": 1950, "summary": "..."}
  ]
}
```

`intervals` is only present for `event_search` queries.

## HTTP API Reference

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | 3D visualization page |
| GET | `/api/latest` | Latest sensor snapshot JSON |
| GET | `/api/keys` | Flattened sensor key list |
| GET | `/api/control/capabilities` | Discovered agents and control support |
| GET | `/api/control/mode` | Current control mode (manual/automated) |
| POST | `/api/control/mode` | Set control mode `{"mode":"manual"}` |
| POST | `/api/control/command` | Send control action `{"agent_id","action_name"}` |
| GET | `/api/automation/catalog` | Rule sets and agent capabilities |
| POST | `/api/automation/rules/apply` | Apply TTL rule set |
| POST | `/api/automation/preset/apply` | Apply clubhouse mode preset |
| POST | `/api/automation/threshold/apply` | Set sensor threshold |
| POST | `/api/sensor-config/apply` | Configure sensors for an agent |
| POST | `/api/chat` | RAG chat query `{"message","room_id"}` |

## Project Structure

```
smart_home_gs/
├── start_3d_visualization_stream.py        # Main entry point (live)
├── start_3d_visualization_simulated_stream.py  # Simulated mode
├── sensor_stream_logger.py                 # MQTT stream logger
├── sensor_caption_infer.py                 # Offline captioning
├── generate_three_case_captions.py         # 3 fixed test cases
├── inspect_caption_prompt_input.py         # Debug caption prompts
├── index_captions_to_milvus.py             # Backfill captions to Milvus
├── requirements-rag.txt                    # RAG dependencies
│
├── rag/                                    # RAG subsystem
│   ├── embeddings.py                       # jina-clip-v1 embedding layer
│   ├── milvus_store.py                     # Milvus Lite vector store
│   └── query_engine.py                     # Classify → retrieve → synthesize
│
├── index.html                              # Room selection dashboard
├── visualize_smart_room_aframe.html        # 3D visualization + chat UI
├── smart_room.xml                          # N1 Lab room definition
├── new_smart_room.xml                      # Alternative room layout
│
├── assets/                                 # 3D furniture models (.glb)
├── aframe/                                 # A-Frame v1.7.1 framework
├── logs/                                   # Sensor stream logs & captions
└── data/                                   # Milvus DB (created at runtime)
```
