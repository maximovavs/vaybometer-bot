#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cyprus macro visual identity policy.

A deterministic, side-effect-free layer that sits *above* the existing
``selected_scene`` / ``composition`` / ``visual_archetype`` pipeline. It groups the
existing Cyprus scene families into a small number of macro families so the channel
does not show the same kind of place too often, even when the underlying scene,
composition and archetype all differ.

Macro identity never replaces ``visual_archetype`` and never selects a scene: the
existing weather-aware selection stays authoritative. This module only classifies
what was already selected and answers "is this macro family over-used right now".
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping


# Technical (non-geographic) macro identities.
CYPRUS_MACRO_LOCAL_COVER = "local_cover"
CYPRUS_MACRO_UNKNOWN = "unknown"

# The local informative cover is a renderer, not a place, so it carries its own
# technical macro identity and must never occupy a real-scenery macro window.
CYPRUS_LOCAL_COVER_SCENES = frozenset({"local_informative_cover", CYPRUS_MACRO_LOCAL_COVER})

# Authoritative scene -> macro family mapping. Every existing Cyprus scene family
# (coastal and inland) is covered exactly once.
CYPRUS_SCENE_MACRO_FAMILIES: dict[str, str] = {
    # rocky natural coast
    "rocky_cove_overlook": "rocky_natural_coast",
    "open_sea_cliffs": "rocky_natural_coast",
    "protected_bay": "rocky_natural_coast",
    "windy_exposed_coast": "rocky_natural_coast",
    "quiet_blue_lagoon": "rocky_natural_coast",
    # open sandy coast
    "long_sandy_beach": "open_sandy_coast",
    "open_beach_horizon": "open_sandy_coast",
    # urban seafront
    "coastal_promenade": "urban_seafront",
    "coastal_urban_rooftop": "urban_seafront",
    "beach_cafe_terrace": "urban_seafront",
    # harbour / marina
    "marina_walkway": "harbour_marina",
    "small_harbour": "harbour_marina",
    "harbour_pier_waterlevel": "harbour_marina",
    "breakwater_coast": "harbour_marina",
    # mountain / inland landscape
    "mountain_coast_view": "mountain_inland",
    "troodos_landscape": "mountain_inland",
    "dry_inland_landscape": "mountain_inland",
    # inland urban
    "inland_urban_rooftop": "urban_inland",
    # village / cultural
    "inland_village": "village_cultural",
    # salt lake flatland
    "salt_lake_landscape": "salt_lake_flatland",
}

CYPRUS_MACRO_FAMILIES: tuple[str, ...] = (
    "rocky_natural_coast",
    "open_sandy_coast",
    "urban_seafront",
    "harbour_marina",
    "mountain_inland",
    "urban_inland",
    "village_cultural",
    "salt_lake_flatland",
)

# G.1 activates exactly one macro guard: at most two open sandy coast publications
# within the last five real visual publications.
CYPRUS_MACRO_RECENT_WINDOW = 5
CYPRUS_MACRO_LIMITS: dict[str, int] = {
    "open_sandy_coast": 2,
}

# Reject reason emitted by the dedup macro gate. Deliberately distinct from the
# existing scene/composition reasons because it is a hard gate with no LRU bypass.
CYPRUS_MACRO_COOLDOWN_REASON = "scene_macro_cooldown"


def cyprus_scene_macro_family(scene_family: object) -> str:
    """Return the macro family for a scene family name.

    The local informative cover maps to ``local_cover``; anything empty or unknown
    maps deterministically to ``unknown``.
    """
    scene = str(scene_family or "").strip()
    if not scene:
        return CYPRUS_MACRO_UNKNOWN
    if scene in CYPRUS_LOCAL_COVER_SCENES:
        return CYPRUS_MACRO_LOCAL_COVER
    return CYPRUS_SCENE_MACRO_FAMILIES.get(scene, CYPRUS_MACRO_UNKNOWN)


def cyprus_macro_family_from_entry(entry: Mapping[str, Any]) -> str:
    """Macro family of a history entry, deriving it for legacy entries.

    Entries written before G.1 have no ``scene_macro_family`` field, so the macro is
    derived from ``selected_scene`` and stays backward compatible.
    """
    if not isinstance(entry, Mapping):
        return CYPRUS_MACRO_UNKNOWN
    explicit = str(entry.get("scene_macro_family") or "").strip()
    if explicit:
        return explicit
    return cyprus_scene_macro_family(entry.get("selected_scene"))


def is_local_cover_entry(entry: Mapping[str, Any]) -> bool:
    """True when the entry is a local informative cover rather than real scenery."""
    if not isinstance(entry, Mapping):
        return False
    if str(entry.get("selected_scene") or "").strip() in CYPRUS_LOCAL_COVER_SCENES:
        return True
    return str(entry.get("scene_macro_family") or "").strip() == CYPRUS_MACRO_LOCAL_COVER


def recent_real_visual_entries(
    history: Iterable[Mapping[str, Any]],
    limit: int = CYPRUS_MACRO_RECENT_WINDOW,
) -> list[Mapping[str, Any]]:
    """Last ``limit`` real visual publications, oldest first.

    Local informative covers are excluded, so a run of provider outages cannot flush
    the macro window and let an over-used macro family return early.
    """
    real = [entry for entry in history if isinstance(entry, Mapping) and not is_local_cover_entry(entry)]
    if limit <= 0:
        return []
    return real[-limit:]


def count_recent_macro_family(
    history: Iterable[Mapping[str, Any]],
    macro_family: str,
    limit: int = CYPRUS_MACRO_RECENT_WINDOW,
) -> int:
    """How many of the last real publications carry this macro family."""
    target = str(macro_family or "").strip()
    if not target or target in {CYPRUS_MACRO_UNKNOWN, CYPRUS_MACRO_LOCAL_COVER}:
        return 0
    return sum(
        1
        for entry in recent_real_visual_entries(history, limit)
        if cyprus_macro_family_from_entry(entry) == target
    )


def macro_family_is_saturated(
    history: Iterable[Mapping[str, Any]],
    macro_family: str,
    limit: int = CYPRUS_MACRO_RECENT_WINDOW,
) -> bool:
    """True when this macro family already reached its cap in the recent window."""
    target = str(macro_family or "").strip()
    cap = CYPRUS_MACRO_LIMITS.get(target)
    if cap is None:
        return False
    return count_recent_macro_family(history, target, limit) >= cap


def blocked_macro_families(
    history: Iterable[Mapping[str, Any]],
    limit: int = CYPRUS_MACRO_RECENT_WINDOW,
) -> tuple[str, ...]:
    """Macro families that are over-used in the recent real-publication window."""
    entries = list(history)
    return tuple(
        family
        for family in CYPRUS_MACRO_FAMILIES
        if macro_family_is_saturated(entries, family, limit)
    )


__all__ = [
    "CYPRUS_LOCAL_COVER_SCENES",
    "CYPRUS_MACRO_COOLDOWN_REASON",
    "CYPRUS_MACRO_FAMILIES",
    "CYPRUS_MACRO_LIMITS",
    "CYPRUS_MACRO_LOCAL_COVER",
    "CYPRUS_MACRO_RECENT_WINDOW",
    "CYPRUS_MACRO_UNKNOWN",
    "CYPRUS_SCENE_MACRO_FAMILIES",
    "blocked_macro_families",
    "count_recent_macro_family",
    "cyprus_macro_family_from_entry",
    "cyprus_scene_macro_family",
    "is_local_cover_entry",
    "macro_family_is_saturated",
    "recent_real_visual_entries",
]
