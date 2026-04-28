"""Tests for IntentResult / CountryField."""
from __future__ import annotations

import os

os.environ.setdefault("GROQ_API_KEY", "dummy")

from app.schemas import CountryField, IntentResult


def test_specific_fields_projection():
    intent = IntentResult(
        is_country_question=True,
        countries=["Germany"],
        fields=[CountryField.POPULATION, CountryField.CAPITAL],
    )
    out = intent.fields_for_api()
    assert "name" in out
    assert "population" in out
    assert "capital" in out
    assert "currencies" not in out  # not requested


def test_all_expands_to_default_set():
    intent = IntentResult(
        is_country_question=True,
        countries=["Brazil"],
        fields=[CountryField.ALL],
    )
    out = intent.fields_for_api()
    assert {"name", "capital", "population", "currencies", "languages"}.issubset(out)


def test_empty_fields_treated_as_all():
    intent = IntentResult(is_country_question=True, countries=["Brazil"], fields=[])
    out = intent.fields_for_api()
    # No specific request → return the default set
    assert "population" in out
    assert "capital" in out


def test_invalid_field_string_rejected():
    """Pydantic must reject unknown field names so the LLM can't poison the API."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        IntentResult(
            is_country_question=True,
            countries=["Brazil"],
            fields=["nuclear_codes"],  # not a valid CountryField
        )


def test_dedup_in_projection():
    intent = IntentResult(
        is_country_question=True,
        countries=["Japan"],
        fields=[CountryField.CAPITAL, CountryField.CAPITAL, CountryField.POPULATION],
    )
    out = intent.fields_for_api()
    assert out.count("capital") == 1
