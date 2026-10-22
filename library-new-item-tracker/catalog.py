"""The libraries being tracked, and one fetcher for each.

To add a library: write (or reuse) a fetcher that returns tracker.Items with
library set to a new key, register it below, and add the key to
tracker.LIBRARY_NAMES.
"""
import bibliocommons
import vega

# Each fetcher takes `known`: {record id: branches stored by earlier runs} for its library.
FETCHERS = {
    "mvpl": lambda known: vega.fetch(),
    "sunnyvale": lambda known: bibliocommons.fetch("sunnyvale", known=known),
    "sclibrary": lambda known: bibliocommons.fetch("sclibrary", known=known),
    "sccl": lambda known: bibliocommons.fetch("sccl", known=known),
}


def fetch_all(known=None):
    """Returns ({library: [Item]}, {library: error text}); one failure doesn't stop the rest.

    known: {library: {record id: stored branches}}, see tracker.known_branches().
    """
    known = known or {}
    results, errors = {}, {}
    for key, fetch in FETCHERS.items():
        try:
            results[key] = fetch(known.get(key, {}))
        except Exception as e:
            errors[key] = f"{type(e).__name__}: {e}"
    return results, errors


if __name__ == "__main__":
    # Diagnostic: python catalog.py
    import time
    results, errors = fetch_all()
    for key, found in results.items():
        print(f"{key}: {len(found)} PS5 games; first 4: "
              f"{[(i.title, i.branches) for i in found[:4]]}")
    for key, err in errors.items():
        print(f"{key}: FAILED {err}")
