from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence


# Notebooken (Figur 3) forventer følgende symbols at kunne importere:
# - ORDER
# - GROUP_SPECS
# - COMBO_GROUPS
# - sum_group_for_ym
# - sum_matches_for_ym
# - sum_overenskomst_total_for_ym


SelectionName = "Udvalgte population"


@dataclass(frozen=True)
class MatchSpec:
    overenskomst: str
    stillinger: Sequence[str] | None = None
    klassificeringer: Sequence[str] | None = None
    # Hvordan håndteres rækker hvor 'klassificering' er None?
    # - "require": brug kun rækker hvor klassificering er None (typisk subtotal på stillingsniveau)
    # - "allow": tillad både None og ikke-None, men vælg en ikke-dobbelt-tællende aggregering
    # - "overenskomst_total": brug overenskomst-total (stilling=None, klassificering=None) via sum_overenskomst_total_for_ym
    klass_none_mode: str = "require"


def _as_str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s if s != "" else None


def _row_is_selected_population(row: Mapping[str, Any]) -> bool:
    bm = row.get("_BM")
    if bm is None:
        return True
    return _as_str(bm) == SelectionName


def _ym(row: Mapping[str, Any]) -> str | None:
    return _as_str(row.get("_YM"))


def _get_code(row: Mapping[str, Any], key: str) -> str | None:
    return _as_str(row.get(key))


def _get_value(row: Mapping[str, Any], key: str) -> float:
    v = row.get(key)
    if v is None:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _filter_base(rows: Iterable[Mapping[str, Any]], ym: str) -> list[Mapping[str, Any]]:
    ym = str(ym)
    return [r for r in rows if _ym(r) == ym and _row_is_selected_population(r)]


def _pick_non_doublecounting_subset(
    candidates: list[Mapping[str, Any]],
    stillinger_filter_applied: bool,
) -> list[Mapping[str, Any]]:
    """Vælg et niveau af aggregering for at undgå dobbelt-tælling.

    API'et kan returnere både detaljerede rækker og subtotaler når `totals=True`.
    Vi vælger derfor det mest aggregerede niveau, som stadig matcher filtrene.
    """

    if not candidates:
        return candidates

    # Hvis vi allerede har filtreret på stilling, så er stilling typisk ikke None.
    # Her kan både klass=None (subtotal) og klass!=None (detaljer) findes.
    if stillinger_filter_applied:
        klass_none = [r for r in candidates if _get_code(r, "klassificering") is None]
        if klass_none:
            return klass_none
        return candidates

    # Ellers: prioriter mest aggregerede rækker.
    level0 = [
        r
        for r in candidates
        if _get_code(r, "stilling") is None and _get_code(r, "klassificering") is None
    ]
    if level0:
        return level0

    level1 = [r for r in candidates if _get_code(r, "stilling") is None]
    if level1:
        # Kan være både klass None og klass != None; brug klass None hvis muligt.
        level1_k0 = [r for r in level1 if _get_code(r, "klassificering") is None]
        return level1_k0 or level1

    level2 = [r for r in candidates if _get_code(r, "klassificering") is None]
    if level2:
        return level2

    return candidates


def sum_overenskomst_total_for_ym(
    rows: Iterable[Mapping[str, Any]],
    ym: str,
    *,
    overenskomst: str,
    value_key: str = "fuldtid",
) -> float:
    """Sum på overenskomst-total for en given måned (YM).

    Foretrækker rækker hvor `stilling` og `klassificering` er None (mest aggregeret).
    """

    overenskomst = str(overenskomst)
    base = _filter_base(rows, ym)

    candidates = [
        r
        for r in base
        if _get_code(r, "overenskomst") == overenskomst
        and _get_code(r, "stilling") is None
        and _get_code(r, "klassificering") is None
    ]
    if not candidates:
        # Fallback: vælg det mest aggregerede niveau som findes for overenskomsten.
        candidates = [r for r in base if _get_code(r, "overenskomst") == overenskomst]
        candidates = _pick_non_doublecounting_subset(candidates, stillinger_filter_applied=False)

    return sum(_get_value(r, value_key) for r in candidates)


def sum_group_for_ym(
    rows: Iterable[Mapping[str, Any]],
    ym: str,
    *,
    overenskomst: str,
    stillinger: Sequence[str] | None,
    klassificeringer: Sequence[str] | None,
    klass_none_mode: str = "require",
    value_key: str = "fuldtid",
) -> float:
    """Sum for en gruppe defineret ved overenskomst + (valgfrie) stillinger/klassificeringer."""

    overenskomst = str(overenskomst)
    stillinger_set = {str(s) for s in stillinger} if stillinger else None
    klass_set = {str(k) for k in klassificeringer} if klassificeringer else None

    if klass_none_mode == "overenskomst_total":
        return sum_overenskomst_total_for_ym(rows, ym, overenskomst=overenskomst, value_key=value_key)

    base = _filter_base(rows, ym)

    def matches(r: Mapping[str, Any]) -> bool:
        if _get_code(r, "overenskomst") != overenskomst:
            return False

        stilling = _get_code(r, "stilling")
        if stillinger_set is not None and stilling not in stillinger_set:
            return False

        klass = _get_code(r, "klassificering")

        if klass_set is not None:
            return klass in klass_set

        # klass_set is None
        if klass_none_mode == "require":
            return klass is None

        if klass_none_mode == "allow":
            return True

        raise ValueError(f"Unknown klass_none_mode: {klass_none_mode!r}")

    candidates = [r for r in base if matches(r)]

    # Undgå dobbelt-tælling når vi tillader klass=None og klass!=None.
    if klass_set is None and klass_none_mode == "allow":
        candidates = _pick_non_doublecounting_subset(candidates, stillinger_filter_applied=stillinger_set is not None)

    # Hvis vi 'require' klass=None men der ikke findes sådanne rækker (kan ske afhængigt af API'ets output),
    # så laver vi en konservativ fallback, der stadig undgår dobbelt-tælling.
    if not candidates and klass_set is None and klass_none_mode == "require":
        relaxed = [
            r
            for r in base
            if _get_code(r, "overenskomst") == overenskomst
            and (stillinger_set is None or _get_code(r, "stilling") in stillinger_set)
        ]
        candidates = _pick_non_doublecounting_subset(relaxed, stillinger_filter_applied=stillinger_set is not None)

    return sum(_get_value(r, value_key) for r in candidates)


def sum_matches_for_ym(
    rows: Iterable[Mapping[str, Any]],
    ym: str,
    matches: Sequence[MatchSpec | Mapping[str, Any]],
    *,
    value_key: str = "fuldtid",
) -> float:
    """Sum flere MatchSpec'er for samme måned."""

    total = 0.0
    for m in matches:
        if isinstance(m, Mapping):
            spec = MatchSpec(
                overenskomst=str(m["overenskomst"]),
                stillinger=m.get("stillinger"),
                klassificeringer=m.get("klassificeringer"),
                klass_none_mode=str(m.get("klass_none_mode", "require")),
            )
        else:
            spec = m

        total += sum_group_for_ym(
            rows,
            ym,
            overenskomst=spec.overenskomst,
            stillinger=spec.stillinger,
            klassificeringer=spec.klassificeringer,
            klass_none_mode=spec.klass_none_mode,
            value_key=value_key,
        )
    return total


"""Gruppe-definitioner (Figur 3).

Disse er sat op til at matche skabelonen i de screenshots, du har vedhæftet:

- Læge-grupper er splittet i: Lægelige chefer, Overlæger, Speciallæger, Uddannelseslæger
- Sygeplejersker/Jordemødre/Fysio/Ergo er defineret som kombinationer af stilling + klassificering
"""


# Rækkefølgen til output-tabellen (Figur 3)
ORDER: list[str] = [
    "Akademikere",
    "Lægelige chefer",
    "Overlæger",
    "Speciallæger",
    "Uddannelseslæger",
    "Ledende sygeplejersker",
    "Sygeplejersker",
    "Jordemødre",
    "Fysioterapeuter",
    "Ergoterapeuter",
    "Social- og sundhedsassistenter",
    "Sundhedsadministrativt personale",
    "Socialpædagoger",
    "Omsorgs- og pædagogmedhjælpere m.fl.",
    "Sygehusportører",
    "Rengørings- og husassistenter",
    "Erhvervsuddannede serviceassistenter",
]


# Simple grupper: ét primært match per gruppe
GROUP_SPECS: dict[str, dict[str, Any]] = {
    "Akademikere": {
        "overenskomst": "272",
        "stillinger": None,
        "klassificeringer": None,
        "klass_none_mode": "overenskomst_total",
    },
    "Lægelige chefer": {
        "overenskomst": "066",
        "stillinger": ["06609", "06608", "06610", "06618", "06611", "06620", "06617"],
        "klassificeringer": None,
        "klass_none_mode": "require",
    },
    "Overlæger": {
        "overenskomst": "066",
        "stillinger": ["06605", "06606"],
        "klassificeringer": None,
        "klass_none_mode": "require",
    },
    "Speciallæger": {
        "overenskomst": "113",
        "stillinger": ["11304", "11310"],
        "klassificeringer": None,
        "klass_none_mode": "require",
    },
    "Uddannelseslæger": {
        "overenskomst": "113",
        "stillinger": ["11301", "11308"],
        "klassificeringer": None,
        "klass_none_mode": "require",
    },
    "Ledende sygeplejersker": {
        "overenskomst": "301",
        "stillinger": ["30101"],
        "klassificeringer": None,
        "klass_none_mode": "require",
    },
    "Social- og sundhedsassistenter": {
        "overenskomst": "283",
        "stillinger": ["28305"],
        "klassificeringer": None,
        "klass_none_mode": "require",
    },
    "Sundhedsadministrativt personale": {
        "overenskomst": "055",
        "stillinger": ["05518", "05513", "05514", "05519"],
        "klassificeringer": None,
        "klass_none_mode": "require",
    },
    "Socialpædagoger": {
        "overenskomst": "078",
        "stillinger": ["07801"],
        "klassificeringer": None,
        "klass_none_mode": "require",
    },
    "Omsorgs- og pædagogmedhjælpere m.fl.": {
        "overenskomst": "293",
        "stillinger": None,
        "klassificeringer": None,
        "klass_none_mode": "overenskomst_total",
    },
    "Sygehusportører": {
        "overenskomst": "072",
        "stillinger": None,
        "klassificeringer": None,
        "klass_none_mode": "overenskomst_total",
    },
    "Erhvervsuddannede serviceassistenter": {
        "overenskomst": "285",
        "stillinger": None,
        "klassificeringer": None,
        "klass_none_mode": "overenskomst_total",
    },
}


# Komposit-grupper: flere match der skal summeres.
# (Bruges i notebooken ved: if group_name in combo_groups: sum_matches_for_ym(...))
COMBO_GROUPS: dict[str, list[MatchSpec]] = {
    "Sygeplejersker": [
        MatchSpec(overenskomst="300", stillinger=["30001"], klassificeringer=None, klass_none_mode="require"),
        MatchSpec(overenskomst="300", stillinger=["30023"], klassificeringer=["3002302"], klass_none_mode="require"),
        MatchSpec(overenskomst="300", stillinger=["30024"], klassificeringer=["3002402"], klass_none_mode="require"),
    ],
    "Jordemødre": [
        MatchSpec(overenskomst="296", stillinger=["29608"], klassificeringer=None, klass_none_mode="require"),
        MatchSpec(overenskomst="296", stillinger=["29601"], klassificeringer=["2960103"], klass_none_mode="require"),
        MatchSpec(overenskomst="296", stillinger=["29610"], klassificeringer=["2961003"], klass_none_mode="require"),
    ],
    "Fysioterapeuter": [
        MatchSpec(overenskomst="296", stillinger=["29605"], klassificeringer=None, klass_none_mode="require"),
        MatchSpec(overenskomst="296", stillinger=["29601"], klassificeringer=["2960102"], klass_none_mode="require"),
        MatchSpec(overenskomst="296", stillinger=["29610"], klassificeringer=["2961002"], klass_none_mode="require"),
    ],
    "Ergoterapeuter": [
        MatchSpec(overenskomst="296", stillinger=["29602"], klassificeringer=None, klass_none_mode="require"),
        MatchSpec(overenskomst="296", stillinger=["29601"], klassificeringer=["2960101"], klass_none_mode="require"),
        MatchSpec(overenskomst="296", stillinger=["29610"], klassificeringer=["2961001"], klass_none_mode="require"),
    ],
}


__all__ = [
    "ORDER",
    "GROUP_SPECS",
    "COMBO_GROUPS",
    "MatchSpec",
    "sum_group_for_ym",
    "sum_matches_for_ym",
    "sum_overenskomst_total_for_ym",
]
