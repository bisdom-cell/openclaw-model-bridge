"""weatherbot.py — minimal consumer project file (the "second project").

This is NOT part of openclaw-model-bridge. It is a deliberately tiny, fictional
agent runtime ("WeatherBot") that exists only to prove the ontology-engine can
govern a *different* project via config-injection (ONTOLOGY_CONFIG_DIR +
ONTOLOGY_PROJECT_ROOT).

The constants below are referenced by this project's own
ontology/governance_ontology.yaml invariants — that is what makes the
file_contains / python_assert checks meaningful when the engine audits *this*
project (not openclaw-model-bridge).
"""

# Hard limit declared by INV-WEATHER-CITIES (policy max-cities-per-query).
MAX_CITIES_PER_QUERY = 3

# Whitelist enforced by INV-WEATHER-UNITS.
ALLOWED_UNITS = ("celsius", "fahrenheit")


def get_forecast(cities, unit="celsius", days=1):
    """Return a stub forecast for up to MAX_CITIES_PER_QUERY cities.

    Enforces the same limit that INV-WEATHER-CITIES asserts on, so the
    runtime python_assert check (inspect.getsource) finds MAX_CITIES_PER_QUERY
    referenced inside this function body.
    """
    if len(cities) > MAX_CITIES_PER_QUERY:
        raise ValueError(
            f"at most {MAX_CITIES_PER_QUERY} cities per query, got {len(cities)}"
        )
    if unit not in ALLOWED_UNITS:
        raise ValueError(f"unit must be one of {ALLOWED_UNITS}, got {unit!r}")
    return [{"city": c, "unit": unit, "days": days, "forecast": "sunny"} for c in cities]


def get_current_temp(city, unit="celsius"):
    """Return a stub current temperature for one city."""
    if unit not in ALLOWED_UNITS:
        raise ValueError(f"unit must be one of {ALLOWED_UNITS}, got {unit!r}")
    return {"city": city, "unit": unit, "temp": 21}
