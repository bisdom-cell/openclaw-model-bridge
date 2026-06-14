"""librarybot.py — a fictional third-party Agent Runtime ("LibraryBot").

This file is NOT part of openclaw-model-bridge. It is the audited source of a
deliberately tiny, *external* consumer project that exists only to prove the
published `openclaw-ontology-engine` wheel can govern a brand-new project —
installed from a wheel into an isolated venv, with NO PYTHONPATH back to the
monorepo (contrast with examples/minimal_consumer, which uses an editable /
PYTHONPATH install).

LibraryBot's domain (catalog / checkout / holds) is intentionally distinct from
both the bridge (data_clean / web_fetch) and WeatherBot (get_forecast). The
constants below are referenced by this project's OWN
ontology/governance_ontology.yaml invariants — that is what makes the
file_contains / python_assert checks meaningful when the engine audits *this*
project (ONTOLOGY_PROJECT_ROOT points here, not at the monorepo).
"""

# Hard limit declared by INV-LIBRARY-CHECKOUT (policy max-books-per-checkout).
MAX_BOOKS_PER_CHECKOUT = 5

# Whitelist enforced by INV-LIBRARY-GENRES.
ALLOWED_GENRES = ("fiction", "nonfiction", "reference")


def search_catalog(query, genre=None):
    """Return stub catalog hits for a query, optionally filtered by genre."""
    if genre is not None and genre not in ALLOWED_GENRES:
        raise ValueError(f"genre must be one of {ALLOWED_GENRES}, got {genre!r}")
    return [{"title": f"{query} — vol {i}", "genre": genre or "fiction"} for i in range(1, 3)]


def checkout_book(books):
    """Check out up to MAX_BOOKS_PER_CHECKOUT books.

    Enforces the same limit that INV-LIBRARY-CHECKOUT asserts on, so the
    runtime python_assert check (inspect.getsource) finds MAX_BOOKS_PER_CHECKOUT
    referenced inside this function body.
    """
    if len(books) > MAX_BOOKS_PER_CHECKOUT:
        raise ValueError(
            f"at most {MAX_BOOKS_PER_CHECKOUT} books per checkout, got {len(books)}"
        )
    return [{"book": b, "status": "checked_out"} for b in books]


def place_hold(isbn):
    """Place a hold on a single title (custom tool, proxy-intercepted)."""
    return {"isbn": isbn, "status": "on_hold"}
