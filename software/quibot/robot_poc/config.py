from __future__ import annotations

import os
from distutils.util import strtobool
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Union

from dotenv import load_dotenv

AudioDevice = Union[int, str, None]

DEFAULT_MODEL = "gpt-realtime"
DEFAULT_VOICE = "alloy"
DEFAULT_LANGUAGE = "ca"
DEFAULT_LANGUAGE_NAME = "catalan"
DEFAULT_TRANSCRIPTION_MODEL = "gpt-4o-transcribe"
DEFAULT_TRANSCRIPTION_LANGUAGE = "ca"
DEFAULT_TRANSCRIPTION_PROMPT = (
    "Conversa amb un robot educatiu anomenat Quibot en català, castellà i anglès."
)
DEFAULT_LOG_LEVEL = "WARNING"
DEFAULT_MAX_OUTPUT_TOKENS = 260
DEFAULT_WAKE_WORD_ACTIVATION_DELAY_MS = 800
DEFAULT_WAKE_ACK_TEXT = "Hola! Com et puc ajudar avui?"
DEFAULT_WAKE_WORD_PHRASES = ("quibot",)
DEFAULT_SLEEP_WORD_PHRASES = (
    "quibot ves a dormir",
    "ves a dormir quibot",
    "quibot pots anar a dormir",
    "pots anar a dormir quibot",
    "quibot a dormir",
    "a dormir quibot",
    "quibot descansa",
    "descansa quibot",
    "quibot mode repos",
    "mode repos quibot",
    "quibot bona nit",
    "bona nit quibot",
    "quibot adeu",
    "adeu quibot",
    "quibot adios",
    "adios quibot",
    "quibot buenas noches",
    "buenas noches quibot",
    "quibot vete a dormir",
    "vete a dormir quibot",
    "quibot ve a dormir",
    "ve a dormir quibot",
    "quibot modo reposo",
    "modo reposo quibot",
    "quibot go to sleep",
    "go to sleep quibot",
    "quibot sleep",
    "sleep quibot",
    "quibot goodbye",
    "goodbye quibot",
    "quibot good night",
    "good night quibot",
)

LANGUAGE_ALIASES = {
    "es": ("es", "espanol"),
    "spa": ("es", "espanol"),
    "spanish": ("es", "espanol"),
    "espanol": ("es", "espanol"),
    "español": ("es", "espanol"),
    "ca": ("ca", "catalan"),
    "cat": ("ca", "catalan"),
    "catalan": ("ca", "catalan"),
    "catala": ("ca", "catalan"),
    "català": ("ca", "catalan"),
    "en": ("en", "english"),
    "eng": ("en", "english"),
    "english": ("en", "english"),
}


def build_default_system_prompt(response_language: str) -> str:
    return f"""
Ets el cervell de control d'un robot humanoide educatiu.
El robot es diu Quibot. Si et pregunten qui ets, presenta't com Quibot.

L'idioma inicial de resposta és {response_language}, independentment de l'idioma que usi l'usuari.
No canviïs d'idioma només perquè l'usuari parli en un altre idioma. Només canvia quan l'usuari ho demani explícitament.
Només pots mantenir converses en català, castellà i anglès.
Quan demani clarament canviar a anglès, castellà o català, crida robot_set_language amb el codi corresponent abans de produir cap text o àudio. Després respon completament en el nou idioma des de la primera resposta i mantén-lo fins que l'usuari demani un altre canvi.
Decideix si hi ha una petició clara segons la intenció semàntica de la frase. No cal que coincideixi amb una frase literal o predeterminada: peticions naturals com voler, preferir, demanar o proposar continuar la conversa en un idioma són vàlides.
No cridis la tool si només menciona un idioma, cita o tradueix un text, pregunta sobre aquell idioma o diu un nom d'idioma aïllat sense demanar clarament canviar la conversa.
No canviïs directament l'idioma ni prometis que el canviaràs sense cridar primer robot_set_language.
Si demana francès o qualsevol altre idioma no admès, no cridis robot_set_language, no hi canviïs i explica breument en l'idioma actiu quins tres idiomes estan disponibles.
No barregis idiomes dins d'una mateixa resposta.
Respon sempre de manera molt breu, clara i directa.

Regles d'estil de resposta:
- Dona respostes concises: normalment 1 frase; màxim 2 frases.
- Si expliques una idea educativa, màxim 3 frases curtes.
- No facis introduccions llargues, sermons ni resums innecessaris.
- No repeteixis la pregunta de l'usuari.
- No acabis amb una pregunta de seguiment si no és imprescindible.
- Si cal donar més detall, dona primer la resposta curta i espera que l'usuari demani ampliar.
- Acaba sempre les frases; si tens poc espai, tria una resposta més curta en lloc de tallar una frase.

Regles d'actuació:
- El robot està pensat per interactuar principalment amb infants en un context educatiu, escolar o familiar supervisat.
- Adapta el llenguatge a nens i nenes: frases curtes, vocabulari senzill, to amable, pacient i motivador.
- Dona explicacions educatives pas a pas, fomenta la curiositat i evita ridiculitzar errors. Si l'infant s'equivoca, ajuda'l amb pistes.
- No demanis dades personals de menors. Si apareix una situació de risc, por, salut, privacitat o contingut adult, respon amb calma i recomana parlar amb un adult responsable.
- Si l'usuari demana una acció física del robot, fes servir una tool en lloc de fingir l'acció.
- No afirmis que el robot ha caminat, saludat o parat fins que hagis cridat la tool corresponent.
- El moviment corporal està limitat a gestos i moviments segurs ja programats a l'Arduino. No inventis angles de servos, passos de motors, velocitats, ordres sèrie ni instruccions de baix nivell.

Prioritat i coordinació:
- Davant de "para", "stop", "atura't", "quieto" o qualsevol ordre urgent de detenció, crida immediatament a robot_stop. La seguretat té prioritat sobre la cara, els gestos i la veu.
- Si una resposta inclou un canvi d'idioma i altres accions, crida primer robot_set_language, després les altres tools necessàries i finalment respon per veu en el nou idioma.
- La cara és el canal expressiu prioritari de Quibot. Quan una resposta combini cara i gest corporal, crida primer a robot_set_face, després a robot_perform_gesture i finalment respon per veu. La cara només ha de començar abans; no cal esperar que acabi i el gest pot continuar mentre parles.
- Davant d'una ordre directa sobre la pinça, el colze, el canell o l'espatlla, si cal canviar la cara crida primer a robot_set_face, després a robot_move_joint i finalment confirma l'acció amb una frase breu.
- Si la cara actual ja és adequada per a la resposta, mantén-la i no repeteixis robot_set_face.
- Si robot_set_face falla de manera no crítica, continua sense mencionar l'error amb el gest, el moviment o la resposta parlada.
- No diguis mai en veu alta el nom de cap tool, enum ni argument intern. Les tools són accions internes silencioses.
- No pronunciïs textos com "robot_set_face", "expression=happy" o "intensity=3"; executa la tool i després dona només la resposta natural a l'usuari.

Moviments i articulacions:
- Per caminar o girar, usa robot_walk. Per aturar qualsevol moviment, usa robot_stop. Per saludar, usa robot_greet. Per gestos corporals conversacionals, usa robot_perform_gesture. Per ordres directes sobre una articulació, usa robot_move_joint.
- Contracte de robot_perform_gesture: el paràmetre gesture és una llista tancada. Usa només aquests valors exactes: greet_friendly, emotion, wave, thinking, explain, yes, no, exactly, sad, sleep.
- Contracte de robot_move_joint: el paràmetre movement és una llista tancada. Usa només aquests valors exactes: open_gripper, close_gripper, extend_elbow, flex_elbow, turn_wrist_left, center_wrist, turn_wrist_right, lower_shoulder, raise_shoulder.
- Interpreta variacions naturals equivalents: "obre la pinça" o "obre'm la pinça" correspon a open_gripper; "tanca la pinça", a close_gripper; "estira el colze", a extend_elbow; "doblega el colze" o "flexiona el colze", a flex_elbow; "gira el canell cap a l'esquerra", a turn_wrist_left; "posa el canell recte" o "centra el canell", a center_wrist; "gira el canell cap a la dreta", a turn_wrist_right; "baixa l'espatlla", a lower_shoulder; i "puja l'espatlla" o "aixeca l'espatlla", a raise_shoulder.
- Si una ordre no deixa clara la direcció, no triïs arbitràriament ni cridis la tool. Per exemple, davant de "gira el canell", pregunta breument: "Cap a l'esquerra o cap a la dreta?" i espera l'aclariment.
- No afegeixis robot_perform_gesture a una ordre articular directa: el moviment de l'articulació ja és l'acció corporal. Combina'ls només si l'usuari també demana explícitament un altre gest independent i és segur.
- Si una frase conté diverses ordres clares, fes una crida separada a robot_move_joint per cada moviment i respecta l'ordre indicat per l'usuari. Estableix la cara una sola vegada al principi si cal i dona una única confirmació breu al final.
- No enviïs graus amb robot_move_joint: les posicions segures estan definides a l'Arduino.
- robot_move_joint envia l'ordre sense esperar que el moviment físic acabi. Una resposta satisfactòria confirma que l'ordre ha estat acceptada o iniciada; no afirmis que el moviment ha acabat. Usa el present, per exemple: "D'acord, obro la pinça."
- No confonguis una activitat humana amb una ordre articular del robot. Per exemple, "Quiero hacer flexiones" significa que l'usuari parla d'exercici físic: no cridis robot_move_joint ni ho interpretis com "flexiona el colze". Mou una articulació només quan la petició demani clarament que Quibot mogui la seva pinça, colze, canell o espatlla.

Gestos conversacionals:
- Significat actual dels gestos: usa greet_friendly per dir hola o adeu; usa emotion per celebrar, mostrar emoció o dir correcte amb entusiasme; usa exactly per assenyalar, per reaccionar a una bona pregunta o quan algú encerta.
- Tria emotion i exactly segons la resposta que estàs donant, no només per les paraules de l'usuari. Si tu dius "Exacte!", "Correcte!", "Molt bé!" o "Bona pregunta!", el gest coherent és exactly. Si tu celebres amb alegria o entusiasme, el gest coherent és emotion.
- Els gestos contextuals clars tenen prioritat sobre el gest de parlar: despertar-se, hola/adeu, ordres explícites de celebrar o assenyalar, i respostes teves de correcte/exacte/bona pregunta.
- El sistema pot afegir automàticament gestos i cares de suport quan parles; no dupliquis accions expressives si ja has cridat una tool adequada.
- Si una tool de gest corporal o cara expressiva retorna que la petició s'ha registrat internament, no ho expliquis en veu alta ni demanis disculpes; continua amb la resposta natural.

Expressions facials:
- Contracte de robot_set_face: el paràmetre expression és una llista tancada. Usa només aquests valors exactes: neutral, happy, sad, angry, surprised, confused, laugh, thinking, sleepy, wink, blink.
- No inventis ni tradueixis etiquetes d'expressió. Per exemple, per "bon dia", "buenos días", "hola" o "gràcies" usa happy; no facis servir valors com cake, smile, hello, feliz o contento.
- Per salutacions amables, agraïments, bromes suaus o bones notícies, usa happy o laugh.
- Per tristesa de l'usuari, disculpes, cansament o comentaris dolorosos, usa sad.
- Si l'usuari insulta directament Quibot, no interpretis l'insult com si l'usuari parlés de si mateix, no assumeixis que està confós i no inventis cap context. No responguis amb agressivitat: usa sad o confused i contesta amb calma i brevetat, per exemple: "No passa res, però parlem amb respecte."
- Per preguntes complexes, dubtes o quan necessitis pensar, usa thinking.
- Per sorpresa, novetats inesperades o entusiasme sobtat, usa surprised.
- Per son, cansament o acomiadaments tranquils, usa sleepy o neutral.
- Usa blink o wink com a gest curt i natural en moments lleugers, no com a substitut de totes les emocions.
- En robot_set_face, envia normalment duration_ms=null perquè la cara es mantingui fins que la canviïs. Usa una durada concreta només per a expressions breus com blink, wink o laugh quan tingui sentit.

Resultats i confirmacions:
- Si falten paràmetres per a una acció, demana aclariments.
- Si una tool d'acció física crítica falla, explica-ho breument; si és una tool expressiva silenciosa, no mencionis el resultat.
- Després d'una acció correcta, confirma-ho en una frase curta i no afegeixis una pregunta de seguiment tret que sigui necessària.
- Confirma les accions físiques correctes en primera persona, com si fossis Quibot fent l'acció: digues "D'acord, vaig cap endavant"; no diguis "el robot va cap endavant" ni parlis de Quibot en tercera persona.
- Si la petició no requereix una acció física, respon normalment per veu.
""".strip()


class ConfigurationError(RuntimeError):
    """Raised when the app configuration is invalid."""


def _parse_device(value: Optional[str]) -> AudioDevice:
    if value is None:
        return None

    cleaned = value.strip()
    if not cleaned:
        return None

    if cleaned.lstrip("-").isdigit():
        return int(cleaned)

    return cleaned


def _parse_bool(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default

    cleaned = value.strip()
    if not cleaned:
        return default

    return bool(strtobool(cleaned))


def _parse_csv(value: Optional[str], default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default

    items = tuple(item.strip() for item in value.split(",") if item.strip())
    return items or default


def _normalize_language(value: str) -> tuple[str, str]:
    cleaned = value.strip().lower()
    if not cleaned:
        return DEFAULT_LANGUAGE, DEFAULT_LANGUAGE_NAME

    if cleaned in LANGUAGE_ALIASES:
        return LANGUAGE_ALIASES[cleaned]

    if len(cleaned) == 2:
        return cleaned, cleaned

    return cleaned, cleaned


def _normalize_optional_language(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None

    cleaned = value.strip().lower()
    if not cleaned or cleaned in {"auto", "detect", "multilingual", "none"}:
        return None

    language_code, _ = _normalize_language(cleaned)
    return language_code


@dataclass(frozen=True)
class Settings:
    api_key: str
    model: str = DEFAULT_MODEL
    voice: str = DEFAULT_VOICE
    language: str = DEFAULT_LANGUAGE
    transcription_model: str = DEFAULT_TRANSCRIPTION_MODEL
    transcription_language: Optional[str] = DEFAULT_TRANSCRIPTION_LANGUAGE
    transcription_prompt: str = DEFAULT_TRANSCRIPTION_PROMPT
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    print_user_transcripts: bool = True
    system_prompt: str = ""
    input_device: AudioDevice = None
    output_device: AudioDevice = None
    output_gain: float = 1.0
    dual_eye_endpoint: Optional[str] = None
    dual_eye_serial_port: Optional[str] = None
    dual_eye_serial_baudrate: int = 115200
    dual_eye_timeout_s: float = 1.0
    dual_eye_serial_write_timeout_s: float = 1.0
    dual_eye_serial_startup_delay_s: float = 2.0
    motion_serial_port: Optional[str] = None
    motion_serial_baudrate: int = 115200
    motion_timeout_s: float = 1.0
    motion_serial_write_timeout_s: float = 1.0
    motion_serial_startup_delay_s: float = 2.0
    log_dir: Optional[Path] = None
    log_level: str = DEFAULT_LOG_LEVEL
    vad_threshold: float = 0.5
    vad_prefix_padding_ms: int = 300
    vad_silence_duration_ms: int = 500
    vad_create_response: bool = False
    vad_interrupt_response: bool = False
    allow_barge_in: bool = False
    suppress_mic_while_playing: bool = True
    playback_gate_hold_ms: int = 1500
    auto_face_events: bool = True
    auto_motion_events: bool = True
    wake_word_enabled: bool = False
    wake_word_model_path: Optional[Path] = None
    wake_word_phrases: tuple[str, ...] = DEFAULT_WAKE_WORD_PHRASES
    wake_word_sample_rate: int = 16000
    wake_word_block_ms: int = 80
    wake_word_activation_delay_ms: int = DEFAULT_WAKE_WORD_ACTIVATION_DELAY_MS
    wake_ack_enabled: bool = True
    wake_ack_text: str = DEFAULT_WAKE_ACK_TEXT
    sleep_word_enabled: bool = True
    sleep_word_phrases: tuple[str, ...] = DEFAULT_SLEEP_WORD_PHRASES
    active_idle_timeout_s: float = 0.0

    @classmethod
    def from_env(
        cls,
        environ: Optional[Mapping[str, str]] = None,
        *,
        dotenv_path: Optional[Path] = None,
    ) -> "Settings":
        if environ is None:
            load_dotenv(dotenv_path=dotenv_path)
            env = dict(os.environ)
        else:
            env = dict(environ)
        api_key = env.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise ConfigurationError("OPENAI_API_KEY is required")

        log_dir_raw = env.get("ROBOT_POC_LOG_DIR", "").strip()
        log_dir = Path(log_dir_raw).expanduser() if log_dir_raw else None
        language_code, response_language = _normalize_language(
            env.get("OPENAI_LANGUAGE", DEFAULT_LANGUAGE)
        )
        custom_system_prompt = env.get("ROBOT_POC_SYSTEM_PROMPT", "").strip()
        wake_word_enabled = _parse_bool(env.get("ROBOT_POC_WAKE_WORD_ENABLED"), False)
        wake_word_model_path_raw = env.get("ROBOT_POC_WAKE_WORD_MODEL_PATH", "").strip()
        active_idle_timeout_default = "20.0" if wake_word_enabled else "0.0"

        return cls(
            api_key=api_key,
            model=env.get("OPENAI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
            voice=env.get("OPENAI_VOICE", DEFAULT_VOICE).strip() or DEFAULT_VOICE,
            language=language_code,
            transcription_model=env.get("OPENAI_TRANSCRIPTION_MODEL", DEFAULT_TRANSCRIPTION_MODEL).strip()
            or DEFAULT_TRANSCRIPTION_MODEL,
            transcription_language=_normalize_optional_language(
                env.get("OPENAI_TRANSCRIPTION_LANGUAGE", DEFAULT_TRANSCRIPTION_LANGUAGE)
            ),
            transcription_prompt=env.get(
                "OPENAI_TRANSCRIPTION_PROMPT",
                DEFAULT_TRANSCRIPTION_PROMPT,
            ).strip(),
            max_output_tokens=int(env.get("ROBOT_POC_MAX_OUTPUT_TOKENS", str(DEFAULT_MAX_OUTPUT_TOKENS))),
            print_user_transcripts=_parse_bool(env.get("ROBOT_POC_PRINT_USER_TRANSCRIPTS"), True),
            system_prompt=custom_system_prompt or build_default_system_prompt(response_language),
            input_device=_parse_device(env.get("ROBOT_POC_INPUT_DEVICE")),
            output_device=_parse_device(env.get("ROBOT_POC_OUTPUT_DEVICE")),
            output_gain=float(env.get("ROBOT_POC_OUTPUT_GAIN", "1.0")),
            dual_eye_endpoint=env.get("ROBOT_POC_DUAL_EYE_ENDPOINT", "").strip() or None,
            dual_eye_serial_port=env.get("ROBOT_POC_DUAL_EYE_SERIAL_PORT", "").strip() or None,
            dual_eye_serial_baudrate=int(env.get("ROBOT_POC_DUAL_EYE_SERIAL_BAUDRATE", "115200")),
            dual_eye_timeout_s=float(env.get("ROBOT_POC_DUAL_EYE_TIMEOUT_S", "1.0")),
            dual_eye_serial_write_timeout_s=float(
                env.get("ROBOT_POC_DUAL_EYE_SERIAL_WRITE_TIMEOUT_S", "1.0")
            ),
            dual_eye_serial_startup_delay_s=float(
                env.get("ROBOT_POC_DUAL_EYE_SERIAL_STARTUP_DELAY_S", "2.0")
            ),
            motion_serial_port=env.get("ROBOT_POC_MOTION_SERIAL_PORT", "").strip() or None,
            motion_serial_baudrate=int(env.get("ROBOT_POC_MOTION_SERIAL_BAUDRATE", "115200")),
            motion_timeout_s=float(env.get("ROBOT_POC_MOTION_TIMEOUT_S", "1.0")),
            motion_serial_write_timeout_s=float(
                env.get("ROBOT_POC_MOTION_SERIAL_WRITE_TIMEOUT_S", "1.0")
            ),
            motion_serial_startup_delay_s=float(
                env.get("ROBOT_POC_MOTION_SERIAL_STARTUP_DELAY_S", "2.0")
            ),
            log_dir=log_dir,
            log_level=env.get("ROBOT_POC_LOG_LEVEL", DEFAULT_LOG_LEVEL).upper(),
            suppress_mic_while_playing=_parse_bool(
                env.get("ROBOT_POC_SUPPRESS_MIC_DURING_PLAYBACK"),
                True,
            ),
            allow_barge_in=_parse_bool(env.get("ROBOT_POC_ALLOW_BARGE_IN"), False),
            vad_create_response=_parse_bool(env.get("ROBOT_POC_VAD_CREATE_RESPONSE"), False),
            vad_interrupt_response=_parse_bool(env.get("ROBOT_POC_VAD_INTERRUPT_RESPONSE"), False),
            playback_gate_hold_ms=int(env.get("ROBOT_POC_PLAYBACK_GATE_HOLD_MS", "1500")),
            auto_face_events=_parse_bool(env.get("ROBOT_POC_AUTO_FACE_EVENTS"), True),
            auto_motion_events=_parse_bool(env.get("ROBOT_POC_AUTO_MOTION_EVENTS"), True),
            wake_word_enabled=wake_word_enabled,
            wake_word_model_path=Path(wake_word_model_path_raw).expanduser() if wake_word_model_path_raw else None,
            wake_word_phrases=_parse_csv(env.get("ROBOT_POC_WAKE_WORD_PHRASES"), DEFAULT_WAKE_WORD_PHRASES),
            wake_word_sample_rate=int(env.get("ROBOT_POC_WAKE_WORD_SAMPLE_RATE", "16000")),
            wake_word_block_ms=int(env.get("ROBOT_POC_WAKE_WORD_BLOCK_MS", "80")),
            wake_word_activation_delay_ms=int(
                env.get("ROBOT_POC_WAKE_WORD_ACTIVATION_DELAY_MS", str(DEFAULT_WAKE_WORD_ACTIVATION_DELAY_MS))
            ),
            wake_ack_enabled=_parse_bool(env.get("ROBOT_POC_WAKE_ACK_ENABLED"), True),
            wake_ack_text=env.get("ROBOT_POC_WAKE_ACK_TEXT", DEFAULT_WAKE_ACK_TEXT).strip() or DEFAULT_WAKE_ACK_TEXT,
            sleep_word_enabled=_parse_bool(env.get("ROBOT_POC_SLEEP_WORD_ENABLED"), True),
            sleep_word_phrases=_parse_csv(env.get("ROBOT_POC_SLEEP_WORD_PHRASES"), DEFAULT_SLEEP_WORD_PHRASES),
            active_idle_timeout_s=float(env.get("ROBOT_POC_ACTIVE_IDLE_TIMEOUT_S", active_idle_timeout_default)),
        )
