"""
Shared Pydantic schemas.

Kept in their own module so the intent node, the tool layer, and the synthesis
node all import the same definitions — no drift between what one node produces
and what the next consumes.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class CountryField(str, Enum):
    """The set of fields the user can ask about.

    These map 1:1 to the REST Countries API field names where possible, so we
    can pass them straight through as a `?fields=` projection.
    """
    CAPITAL = "capital"
    POPULATION = "population"
    CURRENCIES = "currencies"
    LANGUAGES = "languages"
    REGION = "region"
    SUBREGION = "subregion"
    AREA = "area"
    TIMEZONES = "timezones"
    BORDERS = "borders"
    FLAG = "flag"

    # "all" = the user wants a general overview; we'll fetch a sensible default set.
    ALL = "all"


class IntentResult(BaseModel):
    """What the intent / field-identification node produces."""
    is_country_question: bool = Field(
        ..., description="True if the user is asking about country facts."
    )
    countries: list[str] = Field(
        default_factory=list,
        description="Country names mentioned or referenced (resolved from history).",
    )
    fields: list[CountryField] = Field(
        default_factory=list,
        description="Which country fields the user is asking about. Empty if "
                    "is_country_question is False.",
    )

    def fields_for_api(self) -> list[str]:
        """Return the REST Countries `?fields=` list, expanding ALL to a
        sensible default. Always includes `name` so we can display the country
        name in the answer."""
        if CountryField.ALL in self.fields or not self.fields:
            api_fields = [
                "name", "capital", "population", "currencies", "languages",
                "region", "subregion", "area", "flag",
            ]
        else:
            api_fields = ["name"] + [f.value for f in self.fields if f != CountryField.ALL]
        # Dedupe while preserving order.
        seen: set[str] = set()
        out: list[str] = []
        for f in api_fields:
            if f not in seen:
                seen.add(f)
                out.append(f)
        return out
