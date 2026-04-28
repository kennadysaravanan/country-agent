"""
LangGraph Country Agent.

The graph implements the THREE steps named in the brief, as three distinct
nodes — not as a tool-use loop:

    [START]
       │
       ▼
    ┌──────────────────────┐
    │ 1. extract_intent    │  ← intent + field identification (structured)
    └──────────┬───────────┘
               │  is_country_question?
        ┌──────┴──────┐
        │             │
        ▼ no          ▼ yes
    ┌────────┐    ┌──────────────────────┐
    │refusal │    │ 2. invoke_tool       │  ← deterministic, no LLM
    └────┬───┘    └──────────┬───────────┘
         │                   │
         │                   ▼
         │       ┌──────────────────────┐
         │       │ 3. synthesize_answer │  ← grounded synthesis from tool result
         │       └──────────┬───────────┘
         │                  │
         └──────────┬───────┘
                    ▼
                 [END]

Why three explicit nodes (not the standard agent ⇄ tools loop)?
- The brief asks for three named steps. Naming them as nodes makes that
  contract explicit and observable per-node in tracing.
- Tool selection is deterministic: 1 country → get_country_info, 2+ → compare.
  We don't need the LLM to make that decision (saves a token round-trip and
  removes a class of "wrong tool" failures).
- Partial-data handling lives entirely in the tool node, where it belongs.

Memory:
    `ask()` reads prior turns from session_memory, runs ONE traversal of the
    graph, and writes new messages back. The graph itself is stateless.
"""
from __future__ import annotations

import json
import logging
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_groq import ChatGroq
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import ValidationError

from app.config import settings
from app.errors import CountriesAPIError, CountryNotFoundError
from app.mcp_tools import _fetch_country
from app.memory import session_memory
from app.schemas import CountryField, IntentResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Agent state
# ---------------------------------------------------------------------------
class AgentState(TypedDict):
    """State passed between nodes."""
    messages: Annotated[list[BaseMessage], add_messages]
    intent: IntentResult | None
    tool_result: dict | None  # Set by invoke_tool, read by synthesize_answer.


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------
def _llm() -> ChatGroq:
    return ChatGroq(
        model=settings.groq_model,
        api_key=settings.groq_api_key,
        temperature=0,
    )


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
INTENT_SYSTEM_PROMPT = """\
You extract structured intent from the user's latest message about countries.

Available fields (use these exact strings):
- capital, population, currencies, languages, region, subregion, area,
  timezones, borders, flag, all

Return ONLY a JSON object on a single line, no prose, no markdown fences:
{"is_country_question": bool, "countries": [str], "fields": [str]}

Rules:
- "is_country_question" = true ONLY if the user is asking for factual info
  about one or more countries.
- "countries": list every country mentioned. Resolve pronouns from history
  ("its currency?" right after asking about Japan → ["Japan"]).
- "fields": list ONLY the fields the user explicitly asked about. If the
  user asks broadly ("tell me about Brazil"), use ["all"].
- If is_country_question is false, return empty lists for countries and fields.

Examples:
User: "What is the population of Germany?"
{"is_country_question": true, "countries": ["Germany"], "fields": ["population"]}

User: "What's the capital and currency of Japan?"
{"is_country_question": true, "countries": ["Japan"], "fields": ["capital", "currencies"]}

User: "Compare Brazil and India"
{"is_country_question": true, "countries": ["Brazil", "India"], "fields": ["all"]}

User: "Hello!"
{"is_country_question": false, "countries": [], "fields": []}
"""


SYNTHESIS_SYSTEM_PROMPT = """\
You write a concise, friendly answer to the user's country question, grounded \
ONLY in the tool result provided to you.

Rules:
1. Use ONLY the data in `tool_result`. Never invent or recall facts.
2. Answer the SPECIFIC fields the user asked about (`requested_fields`). Don't
   dump every field unless the user asked broadly.
3. Round populations to a readable form (e.g. "approximately 212.6 million").
4. If a field is missing or null in tool_result, say so plainly — don't guess.
5. If `tool_result` contains an `errors` section, mention which countries
   failed and answer for the rest.
6. Keep it natural and conversational. No bullet lists unless comparing 3+ items.
"""


REFUSAL_SYSTEM_PROMPT = """\
The user's message is not a question about countries. Reply briefly and \
politely, and remind them you can answer questions about countries (capital, \
population, currency, languages, region, etc.). One or two sentences.
"""


# ---------------------------------------------------------------------------
# Node 1: extract_intent — intent + field identification
# ---------------------------------------------------------------------------
def extract_intent_node(state: AgentState) -> dict:
    """Single LLM call that returns a structured IntentResult."""
    msgs: list[BaseMessage] = [SystemMessage(INTENT_SYSTEM_PROMPT), *state["messages"]]
    resp = _llm().invoke(msgs)
    raw = (resp.content or "").strip()

    # Strip markdown fences if the model added them despite instructions.
    if raw.startswith("```"):
        raw = raw.strip("`").lstrip("json").strip()

    try:
        parsed = json.loads(raw)
        intent = IntentResult.model_validate(parsed)
    except (json.JSONDecodeError, ValidationError) as e:
        logger.warning("intent.parse_failed raw=%r err=%s", raw[:200], e)
        # Conservative fallback: treat unparseable output as off-topic.
        intent = IntentResult(is_country_question=False, countries=[], fields=[])

    logger.info(
        "intent.extracted is_country=%s countries=%s fields=%s",
        intent.is_country_question, intent.countries, [f.value for f in intent.fields],
    )
    return {"intent": intent}


# ---------------------------------------------------------------------------
# Node 2: invoke_tool — deterministic, no LLM
# ---------------------------------------------------------------------------
def invoke_tool_node(state: AgentState) -> dict:
    """Call the appropriate tool based on the intent. No LLM in this node."""
    intent = state["intent"]
    assert intent is not None, "invoke_tool reached without intent"

    api_fields = intent.fields_for_api()
    countries = intent.countries[:5]  # hard cap

    if not countries:
        # Intent said it was a country question but couldn't pin down a country.
        # Surface this as a tool result the synthesis node can explain.
        return {
            "tool_result": {
                "kind": "no_country",
                "message": (
                    "I couldn't tell which country you meant. Could you say the "
                    "country name?"
                ),
            }
        }

    if len(countries) == 1:
        # Single-country path
        try:
            data = _fetch_country(countries[0], api_fields)
            return {"tool_result": {"kind": "single", "country": countries[0], "data": data}}
        except CountryNotFoundError as e:
            return {"tool_result": {"kind": "not_found", "country": countries[0], "message": str(e)}}
        except CountriesAPIError as e:
            return {"tool_result": {"kind": "upstream_error", "message": str(e)}}

    # Multi-country path — preserve partial successes
    results: dict = {}
    errors: dict = {}
    for c in countries:
        try:
            results[c] = _fetch_country(c, api_fields)
        except CountryNotFoundError as e:
            errors[c] = str(e)
        except CountriesAPIError as e:
            errors[c] = f"upstream error: {e}"
    return {
        "tool_result": {
            "kind": "compare",
            "results": results,
            "errors": errors,
        }
    }


# ---------------------------------------------------------------------------
# Node 3: synthesize_answer — grounded LLM synthesis
# ---------------------------------------------------------------------------
def synthesize_answer_node(state: AgentState) -> dict:
    """Take tool_result + intent + history → produce a natural-language answer."""
    intent = state["intent"]
    tool_result = state["tool_result"] or {}

    # Hand the LLM ONLY the data and the spec. We construct the synthesis
    # prompt as a single human message containing structured context. This
    # prevents the model from being confused by the conversation history
    # (which may contain irrelevant prior tool results from earlier turns).
    requested = (
        [f.value for f in intent.fields] if intent and intent.fields else ["all"]
    )
    context = {
        "requested_fields": requested,
        "tool_result": tool_result,
    }

    # Last user question, for grounding the answer style.
    last_user = next(
        (m.content for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        "",
    )

    prompt_msgs: list[BaseMessage] = [
        SystemMessage(SYNTHESIS_SYSTEM_PROMPT),
        HumanMessage(
            f"User question: {last_user}\n\n"
            f"Context (use ONLY this):\n{json.dumps(context, ensure_ascii=False, indent=2)}"
        ),
    ]
    resp = _llm().invoke(prompt_msgs)

    # Persist a clean assistant message into the conversation history.
    return {"messages": [AIMessage(content=resp.content)]}


# ---------------------------------------------------------------------------
# Refusal node (off-topic branch)
# ---------------------------------------------------------------------------
def refusal_node(state: AgentState) -> dict:
    msgs: list[BaseMessage] = [SystemMessage(REFUSAL_SYSTEM_PROMPT), *state["messages"]]
    resp = _llm().invoke(msgs)
    return {"messages": [AIMessage(content=resp.content)]}


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
def route_after_intent(state: AgentState) -> Literal["invoke_tool", "refusal"]:
    intent = state["intent"]
    if intent and intent.is_country_question:
        return "invoke_tool"
    return "refusal"


# ---------------------------------------------------------------------------
# Graph build
# ---------------------------------------------------------------------------
def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("extract_intent", extract_intent_node)
    graph.add_node("invoke_tool", invoke_tool_node)
    graph.add_node("synthesize_answer", synthesize_answer_node)
    graph.add_node("refusal", refusal_node)

    graph.add_edge(START, "extract_intent")
    graph.add_conditional_edges("extract_intent", route_after_intent)
    graph.add_edge("invoke_tool", "synthesize_answer")
    graph.add_edge("synthesize_answer", END)
    graph.add_edge("refusal", END)

    return graph.compile()


AGENT_GRAPH = build_graph()


# ---------------------------------------------------------------------------
# Public entrypoint used by the FastAPI route
# ---------------------------------------------------------------------------
def ask(question: str, session_id: str) -> str:
    """Run the agent for one user turn within a session.

    Memory flow:
        1. Pull prior messages for this session_id from session_memory.
        2. Append the new user message.
        3. Run the graph with the full history as the seed state.
        4. Compute which messages are NEW and persist them back.
        5. Return the final assistant message text.
    """
    history = session_memory.get(session_id)
    new_user_msg = HumanMessage(content=question)
    seed = history + [new_user_msg]

    result = AGENT_GRAPH.invoke(
        {"messages": seed, "intent": None, "tool_result": None}
    )

    final_messages: list[BaseMessage] = result["messages"]
    new_messages = final_messages[len(history):]
    session_memory.append(session_id, new_messages)

    last = final_messages[-1]
    return last.content if isinstance(last.content, str) else str(last.content)
