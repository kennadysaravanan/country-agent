"""
Tools for the Country Agent.

Tools are declared with FastMCP's `@mcp.tool` decorator so the same definitions
could be served over the Model Context Protocol later. They are ALSO exposed as
LangChain `@tool` functions so the LangGraph agent can bind them to the LLM as
native tool calls.

Both wrappers delegate to `_fetch_country` — one source of truth for HTTP +
retry + parsing.

Production touches:
- Field projection on the upstream API (only fetch what the user asked about).
- 3 retries with exponential backoff on transient errors (5xx, network).
- Typed error hierarchy so the agent can distinguish recoverable vs. transient.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from fastmcp import FastMCP
from langchain_core.tools import tool

from app.config import settings
from app.errors import (
    CountriesAPIError,
    CountryNotFoundError,
    InvalidResponseError,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MCP server (declares the tools)
# ---------------------------------------------------------------------------
mcp = FastMCP("country-agent-tools")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
_RETRYABLE_STATUS = {500, 502, 503, 504}
_MAX_RETRIES = 3
_BASE_BACKOFF = 0.4  # seconds


def _http_get(url: str, params: dict | None = None) -> httpx.Response:
    """GET with retries + exponential backoff on transient failures.

    Raises CountriesAPIError after exhausting retries.
    """
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = httpx.get(
                url, params=params, timeout=settings.http_timeout_seconds
            )
        except httpx.RequestError as e:
            last_exc = e
            logger.warning(
                "countries_api.network_error attempt=%d url=%s err=%s",
                attempt, url, e,
            )
        else:
            if resp.status_code not in _RETRYABLE_STATUS:
                return resp
            last_exc = CountriesAPIError(
                f"upstream returned {resp.status_code}"
            )
            logger.warning(
                "countries_api.transient_status attempt=%d url=%s status=%d",
                attempt, url, resp.status_code,
            )

        if attempt < _MAX_RETRIES:
            time.sleep(_BASE_BACKOFF * (2 ** (attempt - 1)))

    raise CountriesAPIError(
        f"Could not reach the countries service after {_MAX_RETRIES} attempts: "
        f"{last_exc}"
    )


def _parse_country(raw: dict[str, Any]) -> dict[str, Any]:
    """Flatten a single REST Countries record into our canonical shape.

    Missing fields become None / [] rather than KeyError — handles partial
    responses (when the API doesn't return a field we projected for, or the
    record genuinely doesn't have that field).
    """
    name = raw.get("name") or {}
    currencies = raw.get("currencies") or {}
    languages = raw.get("languages") or {}
    capital_list = raw.get("capital") or []

    return {
        "name_common": name.get("common"),
        "name_official": name.get("official"),
        "capital": capital_list[0] if capital_list else None,
        "population": raw.get("population"),
        "region": raw.get("region"),
        "subregion": raw.get("subregion"),
        "area_km2": raw.get("area"),
        "currencies": [
            {"code": code, "name": v.get("name"), "symbol": v.get("symbol")}
            for code, v in currencies.items()
        ],
        "languages": list(languages.values()),
        "timezones": raw.get("timezones") or [],
        "borders": raw.get("borders") or [],
        "flag_emoji": raw.get("flag"),
    }


def _fetch_country(name: str, fields: list[str] | None = None) -> dict[str, Any]:
    """Hit REST Countries with optional field projection.

    Raises CountryNotFoundError, CountriesAPIError, or InvalidResponseError.
    """
    url = f"{settings.countries_api_base}/name/{name}"
    params = {"fields": ",".join(fields)} if fields else None

    logger.info("countries_api.request country=%s fields=%s", name, fields)
    resp = _http_get(url, params=params)

    if resp.status_code == 404:
        raise CountryNotFoundError(f"No country found matching '{name}'.")
    if resp.status_code == 403:
        raise CountriesAPIError("The countries service refused the request.")
    if resp.status_code != 200:
        raise CountriesAPIError(
            f"Unexpected status {resp.status_code} from countries service."
        )

    try:
        data = resp.json()
    except ValueError as e:
        raise InvalidResponseError(f"Non-JSON response for '{name}': {e}") from e

    if not isinstance(data, list) or not data:
        raise InvalidResponseError(f"Unexpected response shape for '{name}'.")

    return _parse_country(data[0])


# ---------------------------------------------------------------------------
# MCP tools (the @mcp.tool you asked for)
# ---------------------------------------------------------------------------
@mcp.tool()
def get_country_info(country: str, fields: list[str] | None = None) -> dict[str, Any]:
    """Look up information about a single country by name.

    Args:
        country: Common or official name (e.g. "Brazil", "Japan").
        fields:  Optional list of REST Countries field names to project.
                 If omitted, fetches a default set.
    """
    return _fetch_country(country, fields)


@mcp.tool()
def compare_countries(
    countries: list[str], fields: list[str] | None = None
) -> dict[str, Any]:
    """Look up information about multiple countries at once (max 5).

    Returns `{results, errors}` so partial successes are preserved when one or
    more countries can't be resolved.
    """
    countries = countries[:5]
    results: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for c in countries:
        try:
            results[c] = _fetch_country(c, fields)
        except CountryNotFoundError as e:
            errors[c] = str(e)
        except (CountriesAPIError, InvalidResponseError) as e:
            errors[c] = f"upstream error: {e}"
    return {"results": results, "errors": errors}


# ---------------------------------------------------------------------------
# LangChain wrappers (what the LangGraph agent binds to the LLM)
# ---------------------------------------------------------------------------
# These return errors as plain dicts (not raises) because the LLM needs a
# tool result it can reason over, not an exception.

@tool
def get_country_info_tool(
    country: str, fields: list[str] | None = None
) -> dict[str, Any]:
    """Look up information about a single country by name. Use this when the
    user asks about ONE country.

    `fields` (optional) is a list of REST Countries field names to fetch:
    capital, population, currencies, languages, region, subregion, area,
    timezones, borders, flag. If omitted, returns a default set.
    """
    try:
        return _fetch_country(country, fields)
    except CountryNotFoundError as e:
        return {"error": "not_found", "message": str(e)}
    except (CountriesAPIError, InvalidResponseError) as e:
        return {"error": "upstream", "message": str(e)}


@tool
def compare_countries_tool(
    countries: list[str], fields: list[str] | None = None
) -> dict[str, Any]:
    """Look up information about MULTIPLE countries (max 5). Use this when the
    user asks about two or more countries together.

    Returns `{results, errors}` — partial success is preserved.
    """
    countries = countries[:5]
    results: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for c in countries:
        try:
            results[c] = _fetch_country(c, fields)
        except CountryNotFoundError as e:
            errors[c] = str(e)
        except (CountriesAPIError, InvalidResponseError) as e:
            errors[c] = f"upstream error: {e}"
    return {"results": results, "errors": errors}


LANGCHAIN_TOOLS = [get_country_info_tool, compare_countries_tool]
