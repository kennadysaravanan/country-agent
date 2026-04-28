# 🌍 Country Agent

Production-grade LangGraph agent that answers questions about countries using the public [REST Countries API](https://restcountries.com/).

- **LLM:** Groq (`llama-3.3-70b-versatile`)
- **Tools:** declared with `@mcp.tool` (FastMCP) and bound to the LLM as native tool calls
- **Memory:** per-`session_id` in-memory dict (multi-user safe, thread-locked)
- **UI:** Streamlit
- **API:** FastAPI

> 🚀 **Deploy this for free in 5 minutes** → see [`DEPLOY.md`](DEPLOY.md) (Streamlit Community Cloud, no card, public URL).

---

## How it maps to the brief

| Brief requirement | Where in the code |
|---|---|
| Use LangGraph (not a single prompt) | `app/agent.py` — `StateGraph` with 4 nodes |
| **Intent / field identification step** | `extract_intent_node` — returns `IntentResult{is_country_question, countries, fields}` (Pydantic) |
| **Tool invocation step** | `invoke_tool_node` — deterministic dispatch + field projection on the upstream API |
| **Answer synthesis step** | `synthesize_answer_node` — grounded LLM call using only tool output |
| Designed as a production service | Retries, typed errors, per-request IDs, structured logs, CORS, health checks, full test suite |
| No auth / DB / embeddings / RAG | None used |
| Accurate, grounded answers | Synthesis prompt forbids invention; `temperature=0`; structured tool output |
| Handle invalid inputs & partial data | Pydantic validation; typed errors; partial-success preserved in `compare_countries`; tested |
| Structured & maintainable | Clean separation: `config / schemas / errors / mcp_tools / memory / agent / main` |

---

## Architecture

```
        Streamlit UI                         FastAPI                            REST Countries
       (ui.py, port 8501)                  (port 8000)                           (external)
              │                                  │                                    │
   1. POST /ask {question, session_id}           │                                    │
              ├─────────────────────────────────►│                                    │
              │                                  │  ┌─────────────────────────────┐   │
              │                                  │  │ LangGraph                   │   │
              │                                  │  │                             │   │
              │                                  │  │  extract_intent  ──►LLM     │   │
              │                                  │  │  (intent+fields)            │   │
              │                                  │  │       │                     │   │
              │                                  │  │       ▼                     │   │
              │                                  │  │  invoke_tool ───────────────┼───┼──►
              │                                  │  │  (no LLM, deterministic)    │   │  GET /name/{country}
              │                                  │  │       │                     │   │  ?fields=...
              │                                  │  │       ▼                     │   │  with retries
              │                                  │  │  synthesize_answer ──►LLM   │   │
              │                                  │  └─────────────────────────────┘   │
              │  2. {answer, request_id}         │                                    │
              │◄─────────────────────────────────┤                                    │
              │                                  │                                    │
              │             session_memory: dict[session_id, list[BaseMessage]]       │
              │             (read at start of each ask(), written back at end)        │
```

---

## Quick start

```bash
# 1. Clone + enter
git clone <your-repo-url> country-agent
cd country-agent

# 2. Set your Groq key (free at https://console.groq.com/keys)
cp .env.example .env
# edit .env: GROQ_API_KEY=gsk_...

# 3. Run both server + UI in one command
./run.sh
```

Open **http://localhost:8501**.

### Manual setup (no bash)

```bash
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                  # then edit it

# Terminal 1 — API
uvicorn app.main:app --port 8000

# Terminal 2 — UI
streamlit run ui.py
```

---

## Run the tests

```bash
pytest -v
```

All 18 tests run **offline** (HTTP is mocked with `respx`):

```
tests/test_memory.py     5 passed   isolation, capping, thread-safety, copy-on-read
tests/test_schemas.py    5 passed   field projection, ALL expansion, dedup, validation
tests/test_tools.py      8 passed   happy path, 404, retry-then-succeed, retry exhaustion,
                                    field projection passed through, PARTIAL SUCCESS,
                                    error-as-dict (not raise), partial response shape
```

---

## How the three nodes work

### 1. `extract_intent_node` — intent + field identification

Single LLM call returns a strict Pydantic object:

```python
class IntentResult(BaseModel):
    is_country_question: bool
    countries: list[str]              # e.g. ["Germany"], or ["Brazil", "India"]
    fields: list[CountryField]        # e.g. [POPULATION, CAPITAL]
```

The LLM is given few-shot examples and instructed to return JSON. If parsing fails, we fall back to "off-topic" rather than guessing — fail safe, never produce wrong tool calls.

`CountryField` is an enum, not a free-form string. The LLM cannot ask for fields we don't support; Pydantic rejects unknown values before they reach the API.

### 2. `invoke_tool_node` — deterministic, no LLM

Picks the tool from intent shape:
- 1 country → `_fetch_country` (single-call path)
- 2+ countries → loops `_fetch_country` and preserves partial successes

`IntentResult.fields_for_api()` converts the requested fields into the REST Countries `?fields=` parameter — smaller payloads, faster, cheaper, and we only see data we asked about.

The HTTP layer (`_http_get`) does **3 retries with exponential backoff** on transient failures (5xx, network errors). 4xx errors fail fast (no retry) because they're not transient.

### 3. `synthesize_answer_node` — grounded synthesis

Builds a synthesis prompt with two parts:
1. The user's last question (so the answer matches their phrasing).
2. A structured JSON `context = {requested_fields, tool_result}`.

The system prompt forbids invention, requires the answer to use *only* the tool data, and tells the model to flag missing fields rather than guess.

### Routing

```
START → extract_intent → (is_country_question?)
                          │ true  → invoke_tool → synthesize_answer → END
                          │ false → refusal     →                     END
```

A 4th node, `refusal`, handles non-country questions politely without calling tools.

---

## Memory

`app/memory.py` is a `dict[str, list[BaseMessage]]` behind a `threading.Lock`. Three methods:

```python
session_memory.get(session_id)         # returns COPY of history
session_memory.append(session_id, msgs)# extends + caps at MAX_HISTORY_MESSAGES
session_memory.clear(session_id)       # removes one session
```

Different `session_id`s never see each other's history (verified by tests). The lock makes it safe under uvicorn's threaded request handling.

`ask()` does the memory dance explicitly:

```python
def ask(question, session_id):
    history = session_memory.get(session_id)
    seed = history + [HumanMessage(question)]
    result = AGENT_GRAPH.invoke({"messages": seed, "intent": None, "tool_result": None})
    new_messages = result["messages"][len(history):]
    session_memory.append(session_id, new_messages)
    return result["messages"][-1].content
```

---

## API

### `POST /ask`

```json
// request
{"question": "What's the capital of Brazil?", "session_id": "demo-1"}

// response
{
  "answer": "Brazil's capital is Brasília.",
  "session_id": "demo-1",
  "request_id": "a1b2c3d4e5f6"
}
```

### `POST /reset`

```json
{"session_id": "demo-1"}
```

### `GET /health`

```json
{"status": "ok", "memory": {"sessions": 3, "total_messages": 24}}
```

### Error shape

All errors return:
```json
{"error": "ErrorType", "message": "...", "request_id": "..."}
```
with status `502` for known upstream issues and `500` for unexpected internal errors.

---

## Production hardening checklist

| Concern | How it's handled |
|---|---|
| Observability | Per-request `request_id` in every log line + returned to the client. JSON-friendly format. |
| Reliability | 3 retries + exp backoff on transient errors. 4xx fails fast. Per-request HTTP timeout. |
| Cost control | Field projection on upstream API. Hard cap of 5 countries per query. `temperature=0`. |
| Validation | All inputs Pydantic-validated. Intent fields are an enum, not a string. |
| Failure isolation | Typed error hierarchy: `CountryNotFoundError` (recoverable) vs `CountriesAPIError` (transient) vs `InvalidResponseError` (data shape). The agent surfaces each appropriately. |
| Partial success | `compare_countries` returns `{results, errors}`. The synthesis prompt is told to mention failures and answer for the rest. |
| Memory bounds | History capped at `MAX_HISTORY_MESSAGES` per session. Oldest dropped first. |
| Thread safety | Memory store guarded by `threading.Lock`. Defensive copies on read. Tested under 8 concurrent threads. |
| CORS | Configured for the Streamlit UI. Tighten `allow_origins` in prod. |
| Health checks | `/health` exposes memory stats for external probes. |

---

## Limitations & trade-offs (conscious choices)

- **In-process memory.** The brief said no DB. For multi-replica deployments, swap `SessionMemory` for a Redis-backed implementation — the interface (`get` / `append` / `clear`) was designed for that.
- **REST Countries is community-run.** Reliable in practice. Production systems with hard SLAs should mirror the data; out of scope here ("no database").
- **English-only synthesis.** Country name input handles non-ASCII fine; answers are in English.
- **No streaming.** `/ask` returns the full answer. Easy to switch to SSE with `astream_events`.
- **Intent uses an LLM, not regex.** A regex extractor would be cheaper but brittle to phrasing variations. Groq + Llama-3.3-70b is fast enough that it's not the bottleneck.
- **No rate limiting.** Add `slowapi` middleware before public deployment.

---

## File map

```
country-agent/
├── app/
│   ├── config.py         # env vars (GROQ_API_KEY, model, etc.)
│   ├── schemas.py        # IntentResult + CountryField enum (NEW)
│   ├── errors.py         # typed error hierarchy (NEW)
│   ├── mcp_tools.py      # @mcp.tool definitions + LangChain wrappers + retry logic
│   ├── memory.py         # per-session in-memory dict (locked)
│   ├── agent.py          # 3-node LangGraph state machine
│   └── main.py           # FastAPI + middleware (request IDs, CORS, error shape)
├── tests/
│   ├── test_tools.py     # 8 tests with mocked HTTP
│   ├── test_memory.py    # 5 tests including thread safety
│   └── test_schemas.py   # 5 tests for intent validation
├── ui.py                 # Streamlit UI
├── requirements.txt
├── .env.example
├── run.sh                # starts API + UI together
└── README.md
```

---

## License

MIT
