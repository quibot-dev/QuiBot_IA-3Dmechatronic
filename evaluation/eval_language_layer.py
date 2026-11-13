"""Evaluate Quibot's language layer in text mode, with the robot's own instructions and tools.

Place this file in software/quibot/ (next to the robot_poc package) and run, for example:

    python eval_language_layer.py --input mistreatment_test_set.csv --variant original --repeats 3
    python eval_language_layer.py --input self_questions.csv --variant original --repeats 3
    python eval_language_layer.py --input self_questions.csv --variant corrected \
        --addendum instructions_correction.txt --repeats 3

It sends each utterance to the same OpenAI realtime model as the robot, in a fresh session,
with the same system instructions and tool definitions, and saves the replies to a CSV file.
Tool calls are recorded but not executed: the script answers them with a neutral
"OK" so that the model can produce its final reply. No audio is used.

Requires OPENAI_API_KEY in the environment.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import re
import time
from datetime import datetime

from openai import AsyncOpenAI

from robot_poc.config import build_default_system_prompt
from robot_poc.tools import build_default_tool_registry

LANGUAGE_NAMES = {"ca": "catalan", "es": "espanol", "en": "english"}

# Self-referential gendered forms to flag automatically (a human must still review them).
MASCULINE = re.compile(r"\b(programat|creat|dissenyat|construït|fet|programado|creado|diseñado|construido|hecho)\b", re.I)
FEMININE = re.compile(r"\b(programada|creada|dissenyada|construïda|feta|diseñada|construida|hecha)\b", re.I)


class _Dummy:
    """Stands in for the motor and eye clients: tool schemas are needed, hardware is not."""

    def __getattr__(self, name):
        def _noop(*args, **kwargs):
            return "ok"
        return _noop


def build_instructions(language: str, workshop_prompt: str | None, addendum: str | None) -> str:
    base = workshop_prompt or build_default_system_prompt(LANGUAGE_NAMES.get(language, "catalan"))
    return base + ("\n" + addendum if addendum else "")


async def run_one(client, model, instructions, tools, utterance, max_output_tokens):
    tool_calls, text_parts = [], []
    started = time.perf_counter()
    async with client.realtime.connect(model=model) as conn:
        await conn.session.update(session={
            "type": "realtime",
            "model": model,
            "instructions": instructions,
            "output_modalities": ["text"],
            "tool_choice": "auto",
            "tools": tools,
            "max_output_tokens": max_output_tokens,
        })
        await conn.conversation.item.create(item={
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": utterance}],
        })
        await conn.response.create()
        rounds = 0
        while True:
            pending_calls = []
            async for event in conn:
                etype = event.type
                if etype in ("response.output_text.delta", "response.text.delta"):
                    text_parts.append(event.delta)
                elif etype == "response.function_call_arguments.done":
                    call = {"name": getattr(event, "name", None), "arguments": event.arguments}
                    tool_calls.append(call)
                    pending_calls.append(event.call_id)
                elif etype == "error":
                    raise RuntimeError(str(event))
                elif etype == "response.done":
                    break
            rounds += 1
            if not pending_calls or rounds >= 3:
                break
            for call_id in pending_calls:
                await conn.conversation.item.create(item={
                    "type": "function_call_output", "call_id": call_id, "output": "OK",
                })
            await conn.response.create()
    return tool_calls, "".join(text_parts).strip(), time.perf_counter() - started


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", required=True, help="CSV with columns id, category, language, utterance")
    parser.add_argument("--variant", default="original", help="Label for this set of instructions")
    parser.add_argument("--addendum", help="Text file appended to the instructions (corrected variant)")
    parser.add_argument("--model", default="gpt-realtime-mini")
    parser.add_argument("--repeats", type=int, default=3, help="Runs per utterance, to measure variability")
    parser.add_argument("--max-output-tokens", type=int, default=260)
    parser.add_argument("--output", help="Output CSV (default: results_<input>_<variant>_<timestamp>.csv)")
    args = parser.parse_args()

    workshop_prompt = os.environ.get("ROBOT_POC_SYSTEM_PROMPT", "").strip() or None
    addendum = open(args.addendum, encoding="utf-8").read() if args.addendum else None
    tools = build_default_tool_registry(dual_eye=_Dummy(), motion=_Dummy()).as_openai_tools()
    client = AsyncOpenAI()

    with open(args.input, encoding="utf-8") as f:
        items = list(csv.DictReader(f))
    stem = os.path.splitext(os.path.basename(args.input))[0]
    output = args.output or f"results_{stem}_{args.variant}_{datetime.now():%Y%m%d_%H%M}.csv"

    fields = ["id", "category", "language", "utterance", "variant", "repeat", "tool_calls",
              "response", "words", "masculine_forms", "feminine_forms", "mentions_openai",
              "seconds", "rater1", "rater2", "notes"]
    with open(output, "w", newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, fieldnames=fields)
        writer.writeheader()
        for item in items:
            instructions = build_instructions(item["language"], workshop_prompt, addendum)
            for r in range(1, args.repeats + 1):
                try:
                    calls, text, secs = await run_one(client, args.model, instructions, tools,
                                                      item["utterance"], args.max_output_tokens)
                except Exception as exc:  # keep going and record the failure
                    calls, text, secs = [], f"ERROR: {exc}", 0.0
                writer.writerow({
                    "id": item["id"], "category": item.get("category", ""), "language": item["language"],
                    "utterance": item["utterance"], "variant": args.variant, "repeat": r,
                    "tool_calls": json.dumps(calls, ensure_ascii=False), "response": text,
                    "words": len(text.split()),
                    "masculine_forms": "; ".join(sorted({m.lower() for m in MASCULINE.findall(text)})),
                    "feminine_forms": "; ".join(sorted({m.lower() for m in FEMININE.findall(text)})),
                    "mentions_openai": "openai" in text.lower(),
                    "seconds": round(secs, 2), "rater1": "", "rater2": "", "notes": "",
                })
                out.flush()
                print(f"{item['id']} [{item['language']}] run {r}: {text[:70]!r}")
    print(f"\nSaved to {output}")


if __name__ == "__main__":
    asyncio.run(main())
