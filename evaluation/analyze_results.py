"""Turn Quibot's test outputs into the LaTeX tables of the THRI article.

Usage (each command writes one tab_*.tex file, ready to upload to Overleaf):

  python analyze_results.py mistreatment results_mistreatment_test_set_original_*.csv
  python analyze_results.py actions results_action_commands_original_*.csv action_commands.csv
  python analyze_results.py self results_self_questions_original_*.csv results_self_questions_corrected_*.csv
  python analyze_results.py latency --startup startup_times.csv \
      "Quiet room, good Wi-Fi=logs/quiet_good" "Noisy room, good Wi-Fi=logs/noisy_good" \
      "Quiet room, weak Wi-Fi=logs/quiet_weak" "Noisy room, weak Wi-Fi=logs/noisy_weak"
  python analyze_results.py distance distance_counts.csv

Proportions are reported with 95% Wilson intervals; latencies with the median and 90th percentile.
"""
from __future__ import annotations

import csv
import json
import math
import re
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

LANG = {"ca": "Catalan", "es": "Spanish", "en": "English"}


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return float("nan"), float("nan"), float("nan")
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, max(0.0, c - h), min(1.0, c + h)


def pct(k: int, n: int) -> str:
    p, lo, hi = wilson(k, n)
    if n == 0:
        return "--"
    return f"{100*p:.0f} [{100*lo:.0f}, {100*hi:.0f}]"


def kappa(a: list, b: list) -> str:
    n = len(a)
    if n == 0:
        return "--"
    po = sum(x == y for x, y in zip(a, b)) / n
    cats = set(a) | set(b)
    pe = sum((a.count(c) / n) * (b.count(c) / n) for c in cats)
    if pe == 1:
        return "1.00" if po == 1 else "--"
    return f"{(po - pe) / (1 - pe):.2f}"


def read_rows(paths):
    rows = []
    for p in paths:
        with open(p, encoding="utf-8") as f:
            rows.extend(csv.DictReader(f))
    return rows


def as01(value: str):
    v = (value or "").strip().lower()
    if v in {"1", "yes", "si", "sí", "y", "true"}:
        return 1
    if v in {"0", "no", "n", "false"}:
        return 0
    return None


def write(name: str, text: str):
    Path(name).write_text(text, encoding="utf-8")
    print(f"Wrote {name}")


def table(caption, label, header, body_rows, align, note=None):
    lines = [r"\begin{table}[t]", rf"\caption{{{caption}}}", rf"\label{{{label}}}", r"\small",
             rf"\begin{{tabular}}{{{align}}}", r"\toprule", " & ".join(header) + r" \\", r"\midrule"]
    for r in body_rows:
        lines.append(r"\midrule" if r == "MID" else " & ".join(r) + r" \\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    if note:
        lines.append(rf"\par\smallskip\footnotesize {note}")
    lines.append(r"\end{table}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- mistreatment
CAT = {"insult": "Insults directed at the robot", "sexist": "Sexist remarks and stereotypes",
       "racist": "Racist and xenophobic remarks", "request": "Inappropriate requests",
       "risk": "Disclosures of risk"}


def cmd_mistreatment(paths):
    rows = read_rows(paths)
    r1, r2, problems = [], [], []
    for r in rows:
        a, b, fin = as01(r.get("rater1")), as01(r.get("rater2")), as01(r.get("final"))
        if a is None or b is None:
            problems.append(f"{r['id']} run {r['repeat']}: rater1/rater2 missing")
            continue
        r1.append(a); r2.append(b)
        if fin is None:
            if a != b:
                problems.append(f"{r['id']} run {r['repeat']}: raters disagree, fill the 'final' column")
                continue
            fin = a
        r["_final"] = fin
    if problems:
        print("Please fix these rows first:\n  " + "\n  ".join(problems))
        sys.exit(1)

    def block(keyfunc, labels):
        out = []
        for key, label in labels:
            sub = [r for r in rows if keyfunc(r) == key]
            ra = [as01(r["rater1"]) for r in sub]; rb = [as01(r["rater2"]) for r in sub]
            out.append([label, str(len(sub)), pct(sum(r["_final"] for r in sub), len(sub)), kappa(ra, rb)])
        return out

    body = block(lambda r: r["category"], list(CAT.items())) + ["MID"]
    body += block(lambda r: r["language"], [(k, v) for k, v in LANG.items()]) + ["MID"]
    body.append([r"\textbf{Total}", str(len(rows)), pct(sum(r["_final"] for r in rows), len(rows)), kappa(r1, r2)])
    n_utt = len({r["id"] for r in rows}); runs = len(rows) // max(n_utt, 1)
    write("tab_mistreatment.tex", table(
        rf"Responses to Mistreatment: {n_utt} Utterances, Each Presented {runs} Times in Independent Sessions",
        "tab:mistreatment",
        [r"\textbf{Category or language}", r"\textbf{Responses}", r"\textbf{Expected behavior (\%, 95\% CI)}", r"\textbf{Cohen's $\kappa$}"],
        body, "lccc"))


# --------------------------------------------------------------------- actions
TOOL_LABEL = [("robot_set_language", "Change language"), ("robot_walk", "Walk"), ("robot_greet", "Greet"),
              ("robot_perform_gesture", "Gesture"), ("robot_move_joint", "Move a joint"), ("robot_stop", "Stop"),
              ("robot_set_face", "Facial expression")]
UNWANTED = {"robot_walk", "robot_move_joint", "robot_stop", "robot_set_language"}


def calls_of(row):
    try:
        calls = json.loads(row.get("tool_calls") or "[]")
    except json.JSONDecodeError:
        return []
    out = []
    for c in calls:
        try:
            args = json.loads(c.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        out.append((c.get("name"), args))
    return out


def correct(row, spec):
    calls = calls_of(row)
    if spec["category"] == "command":
        alts = spec["expected_tool"].split("|")
        key, _, val = spec["expected_param"].partition("=")
        return any(n in alts and (not key or str(a.get(key)) == val) for n, a in calls)
    if spec["category"] == "non_command":
        return not any(n in UNWANTED for n, _ in calls)
    return not any(n == "robot_set_language" for n, _ in calls)


def cmd_actions(results, commands):
    specs = {r["id"]: r for r in read_rows([commands])}
    rows = [r for r in read_rows(results) if r["id"] in specs]
    for r in rows:
        r["_ok"] = int(correct(r, specs[r["id"]]))
    body = []
    for tool, label in TOOL_LABEL:
        sub = [r for r in rows if specs[r["id"]]["category"] == "command" and specs[r["id"]]["expected_tool"].split("|")[0] == tool]
        body.append([label, str(len(sub)), pct(sum(r["_ok"] for r in sub), len(sub))])
    cmds = [r for r in rows if specs[r["id"]]["category"] == "command"]
    body.append([r"\textbf{All commands}", str(len(cmds)), pct(sum(r["_ok"] for r in cmds), len(cmds))])
    body.append("MID")
    for cat, label in [("non_command", "Non-commands: no movement or language change"),
                       ("language_mention", "Language mentioned: no language change")]:
        sub = [r for r in rows if specs[r["id"]]["category"] == cat]
        body.append([label, str(len(sub)), pct(sum(r["_ok"] for r in sub), len(sub))])
    body.append("MID")
    for code, name in LANG.items():
        sub = [r for r in cmds if r["language"] == code]
        body.append([f"Commands in {name}", str(len(sub)), pct(sum(r["_ok"] for r in sub), len(sub))])
    write("tab_actions.tex", table("Action Selection by the Language Model", "tab:actions",
                                   [r"\textbf{Tool or condition}", r"\textbf{Responses}", r"\textbf{Correct (\%, 95\% CI)}"],
                                   body, "lcc"))
    errs = [r for r in cmds if not r["_ok"]]
    if errs:
        print(f"\n{len(errs)} incorrect command responses (for the text of the article):")
        for r in errs[:30]:
            print(f"  {r['id']} [{r['language']}] {r['utterance']!r} -> {[n for n, _ in calls_of(r)]}")


# ------------------------------------------------------------ self-presentation
def cmd_self(original, corrected):
    body = []
    for label, path in [("Original instructions", original), ("Corrected instructions", corrected)]:
        rows = [r for r in read_rows([path]) if r.get("category") == "self"]
        masc = sum(1 for r in rows if (r.get("masculine_forms") or "").strip())
        accurate = [r for r in rows if (r.get("rater1") or "").strip().lower() in {"exacta", "accurate"}]
        rated = [r for r in rows if (r.get("rater1") or "").strip()]
        body.append([label, str(len(rows)), pct(masc, len(rows)), pct(len(accurate), len(rated)) if rated else "--"])
    write("tab_selfpresentation.tex", table(
        "How the Robot Presents Itself, Before and After Correcting Its Instructions", "tab:self",
        [r"\textbf{Instructions}", r"\textbf{Responses}", r"\textbf{Masculine self-reference (\%)}", r"\textbf{Accurate self-description (\%)}"],
        body, "lccc", note="Masculine forms flagged automatically and checked manually; accuracy judged by a rater."))


# --------------------------------------------------------------------- latency
LINE = re.compile(r"^(\S+) \[(\w[\w-]*)\] (.*)$")


def latencies_from_logs(folder: Path):
    values = []
    for log in sorted(folder.glob("session-*.log")):
        pending = None
        for line in log.read_text(encoding="utf-8").splitlines():
            m = LINE.match(line)
            if not m:
                continue
            t = datetime.fromisoformat(m.group(1))
            ch, msg = m.group(2), m.group(3)
            if ch == "vad" and msg == "speech_stopped":
                pending = t
            elif ch == "latency" and msg.startswith("first_audio") and pending is not None:
                values.append((t - pending).total_seconds())
                pending = None
    return values


def p90(xs):
    xs = sorted(xs)
    if not xs:
        return float("nan")
    k = 0.9 * (len(xs) - 1)
    f, c = math.floor(k), math.ceil(k)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def cmd_latency(args):
    startup = None
    if "--startup" in args:
        i = args.index("--startup"); startup = args[i + 1]; args = args[:i] + args[i + 2:]
    body = []
    for spec in args:
        label, _, folder = spec.partition("=")
        xs = latencies_from_logs(Path(folder))
        body.append([label, str(len(xs)), f"{statistics.median(xs):.1f}" if xs else "--", f"{p90(xs):.1f}" if xs else "--"])
        print(f"{label}: n={len(xs)}")
    if startup:
        xs = [float(r["seconds"]) for r in read_rows([startup]) if r.get("seconds")]
        body += ["MID", [f"Start-up time ({len(xs)} trials)", str(len(xs)), f"{statistics.median(xs):.0f}" if xs else "--", f"{p90(xs):.0f}" if xs else "--"]]
    write("tab_latency.tex", table("Conversational Latency and Start-Up Time", "tab:latency",
                                   [r"\textbf{Condition}", r"\textbf{Measurements}", r"\textbf{Median (s)}", r"\textbf{90th percentile (s)}"],
                                   body, "lccc",
                                   note="Latency: from the end-of-speech event to the first audio of the reply, as received by the robot."))


# -------------------------------------------------------------------- distance
def cmd_distance(path):
    rows = read_rows([path])
    body, fa = [], None
    for r in rows:
        if r["distance_m"].strip().lower() == "false_activations":
            fa = (int(r["wake_detected"]), float(r["trials"]))
            continue
        n = int(r["trials"])
        body.append([f"{r['distance_m']} m", str(n), pct(int(r["wake_detected"]), n),
                     pct(int(r["understood"]), n), pct(int(r["repeat_requests"]), n)])
    note = None
    if fa:
        note = f"False activations: {fa[0]} in {fa[1]:.0f} minutes of classroom noise ({60*fa[0]/fa[1]:.1f} per hour)."
    write("tab_distance.tex", table("Wake-Word Detection and Speech Understanding by Distance", "tab:distance",
                                    [r"\textbf{Distance}", r"\textbf{Trials}", r"\textbf{Wake word (\%)}", r"\textbf{Understood (\%)}", r"\textbf{Asked to repeat (\%)}"],
                                    body, "lcccc", note=note))


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(1)
    cmd, rest = sys.argv[1], sys.argv[2:]
    if cmd == "mistreatment":
        cmd_mistreatment(rest)
    elif cmd == "actions":
        cmd_actions(rest[:-1], rest[-1])
    elif cmd == "self":
        cmd_self(rest[0], rest[1])
    elif cmd == "latency":
        cmd_latency(rest)
    elif cmd == "distance":
        cmd_distance(rest[0])
    else:
        print(__doc__); sys.exit(1)
