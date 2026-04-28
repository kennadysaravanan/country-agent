"""
Tool-layer tests.

These run completely offline via `respx`, so they prove behavior under
failure modes that are hard to reproduce against the real API:
- Country not found (404)
- Upstream 5xx with retry success on the 3rd attempt
- Partial success in compare_countries (1 of 3 fails)
- Field projection is passed through correctly
"""
from __future__ import annotations

import os
import pytest
import respx
from httpx import Response

# Set a dummy key so app.config doesn't refuse to import.
os.environ.setdefault("GROQ_API_KEY", "dummy")

from app.config import settings
from app.errors import CountriesAPIError, CountryNotFoundError
from app.mcp_tools import _fetch_country, compare_countries_tool, get_country_info_tool

BASE = settings.countries_api_base


def _brazil_payload(fields: list[str] | None = None) -> list[dict]:
    """Minimal REST Countries-shaped payload."""
    return [
        {
            "name": {"common": "Brazil", "official": "Federative Republic of Brazil"},
            "capital": ["Brasília"],
            "population": 212559409,
            "currencies": {"BRL": {"name": "Brazilian real", "symbol": "R$"}},
            "languages": {"por": "Portuguese"},
            "region": "Americas",
            "subregion": "South America",
            "area": 8515767.0,
            "flag": "🇧🇷",
        }
    ]


@respx.mock
def test_fetch_country_happy_path():
    respx.get(f"{BASE}/name/Brazil").mock(return_value=Response(200, json=_brazil_payload()))
    out = _fetch_country("Brazil")
    assert out["name_common"] == "Brazil"
    assert out["capital"] == "Brasília"
    assert out["population"] == 212559409
    assert out["currencies"][0]["code"] == "BRL"


@respx.mock
def test_fetch_country_not_found_raises():
    respx.get(f"{BASE}/name/Wakanda").mock(return_value=Response(404))
    with pytest.raises(CountryNotFoundError):
        _fetch_country("Wakanda")


@respx.mock
def test_fetch_country_retries_on_5xx_then_succeeds():
    """First two calls return 503, third returns 200. Should succeed silently."""
    route = respx.get(f"{BASE}/name/Brazil").mock(
        side_effect=[
            Response(503),
            Response(503),
            Response(200, json=_brazil_payload()),
        ]
    )
    out = _fetch_country("Brazil")
    assert out["name_common"] == "Brazil"
    assert route.call_count == 3


@respx.mock
def test_fetch_country_gives_up_after_max_retries():
    respx.get(f"{BASE}/name/Brazil").mock(return_value=Response(502))
    with pytest.raises(CountriesAPIError):
        _fetch_country("Brazil")


@respx.mock
def test_field_projection_is_passed_through():
    route = respx.get(f"{BASE}/name/Brazil").mock(
        return_value=Response(200, json=_brazil_payload())
    )
    _fetch_country("Brazil", fields=["name", "population"])
    # Verify the upstream URL got the projection
    assert "fields=name%2Cpopulation" in str(route.calls[0].request.url) \
        or "fields=name,population" in str(route.calls[0].request.url)


@respx.mock
def test_compare_partial_success():
    """One country resolves, one 404s. Both must appear in the response."""
    respx.get(f"{BASE}/name/Brazil").mock(return_value=Response(200, json=_brazil_payload()))
    respx.get(f"{BASE}/name/Wakanda").mock(return_value=Response(404))

    out = compare_countries_tool.invoke(
        {"countries": ["Brazil", "Wakanda"], "fields": None}
    )
    assert "Brazil" in out["results"]
    assert "Wakanda" in out["errors"]
    assert "No country found" in out["errors"]["Wakanda"]


@respx.mock
def test_get_country_info_tool_returns_error_dict_not_exception():
    """The LangChain wrapper must NEVER raise — the LLM needs a result it can read."""
    respx.get(f"{BASE}/name/Wakanda").mock(return_value=Response(404))
    out = get_country_info_tool.invoke({"country": "Wakanda", "fields": None})
    assert out == {"error": "not_found", "message": "No country found matching 'Wakanda'."}


@respx.mock
def test_partial_response_handled():
    """REST Countries returns a record with most fields missing — we must not crash."""
    respx.get(f"{BASE}/name/Tinyland").mock(
        return_value=Response(200, json=[{"name": {"common": "Tinyland"}}])
    )
    out = _fetch_country("Tinyland")
    assert out["name_common"] == "Tinyland"
    assert out["capital"] is None
    assert out["population"] is None
    assert out["currencies"] == []
    assert out["languages"] == []
