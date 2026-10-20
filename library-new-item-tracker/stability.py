"""Temporary: do repeated catalog searches return the same set of games?"""
import sys
import bibliocommons as bc

for slug in ("sccl", "sclibrary"):
    for sort in ("newly_acquired", "title"):
        runs = []
        for n in range(3):
            ids = set()
            for q in bc.QUERIES:
                ids |= {b["id"] for b in bc.search_bibs(slug, q, sort=sort) if bc.is_ps5_game(b)}
            runs.append(ids)
        union = set().union(*runs)
        always = set.intersection(*runs)
        print(f"{slug} sort={sort}: sizes={[len(r) for r in runs]} union={len(union)} "
              f"in-every-run={len(always)} flickering={len(union - always)}", flush=True)
