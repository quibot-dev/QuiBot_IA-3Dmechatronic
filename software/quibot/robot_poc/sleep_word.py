from __future__ import annotations

from typing import Optional, Sequence

from .wake_word import QUIBOT_ALIASES, normalize_text


def match_sleep_word(transcript: str, phrases: Sequence[str]) -> Optional[str]:
    normalized_transcript = f" {normalize_text(transcript)} "
    for phrase in phrases:
        for alias in _phrase_aliases(phrase):
            if alias and f" {alias} " in normalized_transcript:
                return phrase
    return None


def _phrase_aliases(phrase: str) -> tuple[str, ...]:
    normalized_phrase = normalize_text(phrase)
    phrase_variants = {normalized_phrase}
    if "adeu" in normalized_phrase.split():
        phrase_variants.add(normalized_phrase.replace("adeu", "a deu"))

    aliases = set(phrase_variants)

    for variant in phrase_variants:
        if "quibot" not in variant:
            continue
        aliases.update(variant.replace("quibot", quibot_alias) for quibot_alias in QUIBOT_ALIASES)
        aliases.update(_swap_quibot_position_aliases(variant))

    return tuple(sorted(alias for alias in aliases if alias))


def _swap_quibot_position_aliases(normalized_phrase: str) -> set[str]:
    tokens = normalized_phrase.split()
    if not tokens:
        return set()

    aliases = set()
    if tokens[0] == "quibot" and len(tokens) > 1:
        command = " ".join(tokens[1:])
        aliases.update(_with_quibot_aliases(command))
    if tokens[-1] == "quibot" and len(tokens) > 1:
        command = " ".join(tokens[:-1])
        aliases.update(_with_quibot_aliases(command))
    return aliases


def _with_quibot_aliases(command: str) -> set[str]:
    return {
        candidate
        for quibot_alias in QUIBOT_ALIASES
        for candidate in (f"{quibot_alias} {command}", f"{command} {quibot_alias}")
    }
