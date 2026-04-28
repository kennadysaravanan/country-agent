"""
Typed errors.

Tools and services raise these so the agent layer can distinguish between
"the user asked about a country we couldn't find" (recoverable, surface to
user) and "the upstream API is down" (transient, retry or 503).
"""


class CountryAgentError(Exception):
    """Base class. Every custom error in this app inherits from this."""


class CountryNotFoundError(CountryAgentError):
    """REST Countries returned 404 for the given name."""


class CountriesAPIError(CountryAgentError):
    """REST Countries returned 5xx, 403, or unreachable."""


class InvalidResponseError(CountryAgentError):
    """REST Countries returned a 200 with an unexpected payload shape."""
