from __future__ import annotations

SUPPORTED_RESPONSE_LANGUAGES = ("ca", "es", "en")

RESPONSE_LANGUAGE_ENGLISH_NAMES = {
    "ca": "Catalan",
    "es": "Spanish",
    "en": "English",
}

TRANSCRIPTION_RETRY_TEXTS = {
    "ca": "No t'he entès bé. Pots repetir-ho?",
    "es": "No te he entendido bien. ¿Puedes repetirlo?",
    "en": "I didn't understand you clearly. Could you repeat that?",
}

SLEEP_FAREWELL_TEXTS = {
    "ca": "Adeu, a reveure.",
    "es": "Adiós, hasta la próxima.",
    "en": "Goodbye, see you next time.",
}

ACTIVE_LANGUAGE_INSTRUCTIONS = {
    "ca": "Respon exclusivament en català. No incloguis paraules de cap altre idioma.",
    "es": "Responde exclusivamente en español. No incluyas palabras de ningún otro idioma.",
    "en": "Reply only in English. Do not include words in any other language.",
}


def transcription_retry_text(language: str) -> str:
    return TRANSCRIPTION_RETRY_TEXTS.get(language, TRANSCRIPTION_RETRY_TEXTS["ca"])


def sleep_farewell_text(language: str) -> str:
    return SLEEP_FAREWELL_TEXTS.get(language, SLEEP_FAREWELL_TEXTS["ca"])


def response_language_english_name(language: str) -> str:
    return RESPONSE_LANGUAGE_ENGLISH_NAMES.get(language, RESPONSE_LANGUAGE_ENGLISH_NAMES["ca"])


def active_language_instruction(language: str) -> str:
    return ACTIVE_LANGUAGE_INSTRUCTIONS.get(language, ACTIVE_LANGUAGE_INSTRUCTIONS["ca"])
