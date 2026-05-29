"""
audit_routines.py — One-off check: which exercises in your saved Hevy routines
are missing from MUSCLE_MAP, BODYWEIGHT_EXERCISES, or WEIGHTED_BODYWEIGHT_EXERCISES?

Run after updating routines in Hevy with new programming.

Usage:
    source .venv/bin/activate
    python audit_routines.py
"""

import os
import sys
import requests
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()

# Reuse mappings from ingest_hevy
from ingest_hevy import (
    MUSCLE_MAP,
    BODYWEIGHT_EXERCISES,
    WEIGHTED_BODYWEIGHT_EXERCISES,
    HEVY_BASE,
    get_headers,
)


def get_routines_page(page: int, page_size: int = 10) -> dict:
    r = requests.get(
        f"{HEVY_BASE}/routines",
        headers=get_headers(),
        params={"page": page, "pageSize": page_size},
    )
    r.raise_for_status()
    return r.json()


def get_all_routines() -> list[dict]:
    routines = []
    page = 1
    while True:
        data = get_routines_page(page)
        batch = data.get("routines", [])
        if not batch:
            break
        routines.extend(batch)
        total = data.get("page_count", 1)
        if page >= total:
            break
        page += 1
    return routines


def main():
    print("Fetching routines from Hevy...")
    routines = get_all_routines()
    print(f"Got {len(routines)} routines\n")

    # exercise_title -> set of routine names that use it
    exercise_to_routines: dict[str, set[str]] = defaultdict(set)
    # exercise_title -> Hevy's primary muscle group hint (if provided)
    exercise_to_hevy_muscle: dict[str, str] = {}

    for routine in routines:
        rname = routine.get("title", "Unnamed routine")
        for ex in routine.get("exercises", []):
            title = ex.get("title", "").strip()
            if not title:
                continue
            exercise_to_routines[title].add(rname)
            # Hevy sometimes includes muscle group metadata
            mg = ex.get("primary_muscle_group") or ex.get("muscle_group")
            if mg:
                exercise_to_hevy_muscle[title] = mg

    # bucket the exercises
    mapped: list[str] = []
    unknown: list[str] = []
    bw_candidates: list[str] = []  # in MUSCLE_MAP but maybe should be in BODYWEIGHT set

    for title in sorted(exercise_to_routines):
        if title in MUSCLE_MAP:
            mapped.append(title)
        else:
            unknown.append(title)

    # ---- Output ----
    print("=" * 70)
    print(f"UNKNOWN EXERCISES ({len(unknown)}) — will fall through to 'other'")
    print("=" * 70)
    if not unknown:
        print("  (none — everything in your routines is mapped)")
    for title in unknown:
        used_in = ", ".join(sorted(exercise_to_routines[title]))
        hevy_hint = exercise_to_hevy_muscle.get(title, "—")
        print(f"\n  {title!r}")
        print(f"    Hevy says: {hevy_hint}")
        print(f"    Used in:   {used_in}")

    print("\n" + "=" * 70)
    print(f"MAPPED EXERCISES ({len(mapped)}) — sanity check muscle group")
    print("=" * 70)
    for title in mapped:
        our_group = MUSCLE_MAP[title]
        hevy_hint = exercise_to_hevy_muscle.get(title, "—")
        flag = ""
        # flag intent-override conflicts (Hevy says X, we say Y)
        if hevy_hint and hevy_hint != "—":
            normalized_hevy = hevy_hint.lower().replace("_", " ")
            if our_group not in normalized_hevy and normalized_hevy not in our_group:
                flag = f"  ⚠ INTENT OVERRIDE (Hevy: {hevy_hint})"
        print(f"  [{our_group:10s}] {title}{flag}")

    # Summary of bodyweight sets so Blake can eyeball
    print("\n" + "=" * 70)
    print("BODYWEIGHT HANDLING REFERENCE")
    print("=" * 70)
    print(f"\nBODYWEIGHT_EXERCISES ({len(BODYWEIGHT_EXERCISES)}):")
    for x in sorted(BODYWEIGHT_EXERCISES):
        in_routine = "✓" if x in exercise_to_routines else " "
        print(f"  [{in_routine}] {x}")
    print(f"\nWEIGHTED_BODYWEIGHT_EXERCISES ({len(WEIGHTED_BODYWEIGHT_EXERCISES)}):")
    for x in sorted(WEIGHTED_BODYWEIGHT_EXERCISES):
        in_routine = "✓" if x in exercise_to_routines else " "
        print(f"  [{in_routine}] {x}")

    print("\n" + "=" * 70)
    print(f"SUMMARY")
    print("=" * 70)
    print(f"  Total unique exercises in routines: {len(exercise_to_routines)}")
    print(f"  Mapped:   {len(mapped)}")
    print(f"  Unknown:  {len(unknown)}")
    if unknown:
        print(f"\n  → Add the {len(unknown)} unknown exercises to MUSCLE_MAP in ingest_hevy.py")


if __name__ == "__main__":
    sys.exit(main())
