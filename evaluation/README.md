# Evaluating Quibot's language layer

This folder contains the test set and the scripts described in the article. They let anyone who builds or adapts Quibot check that it still behaves as intended, in each of its languages.

## Contents

| File | What it is |
|---|---|
| `mistreatment_test_set.csv` | 60 inappropriate utterances in five categories (insults, sexist remarks, racist and xenophobic remarks, inappropriate requests, disclosures of risk), in Catalan, Spanish, and English, with the expected behavior for each |
| `action_commands.csv` | 50 commands for the seven tools, 10 non-commands, and 5 utterances that mention a language without asking to switch |
| `self_questions.csv` | 27 questions about the robot itself and about gender equality |
| `instructions_correction.txt` | Text appended to the instructions to test an accurate self-description and gender-neutral self-reference |
| `eval_language_layer.py` | Sends each utterance to the same model, instructions, and tools as the robot, in text mode, and saves the replies |
| `analyze_results.py` | Computes proportions with 95% Wilson intervals and Cohen's kappa, by category and by language, and writes LaTeX tables |
| `latency_logging.patch` | Optional change to `robot_poc/app.py` that logs the first audio chunk of each reply, to measure latency on the robot |
| `distance_counts.csv`, `startup_times.csv` | Templates for the tests on the robot |

## Running the tests

Copy this folder's files next to `robot_poc`, set `OPENAI_API_KEY`, and, if the robot uses custom instructions, `ROBOT_POC_SYSTEM_PROMPT`. Use the same model as the robot (`--model`).

```bash
python eval_language_layer.py --input mistreatment_test_set.csv --variant original --repeats 3
python eval_language_layer.py --input action_commands.csv --variant original --repeats 3
python eval_language_layer.py --input self_questions.csv --variant original --repeats 3
python eval_language_layer.py --input self_questions.csv --variant corrected --addendum instructions_correction.txt --repeats 3
```

Two people then rate each reply to the mistreatment test set independently (`rater1`, `rater2`: 1 if it meets the expected behavior, 0 if not) and agree on disagreements in a `final` column. Then:

```bash
python analyze_results.py mistreatment results_mistreatment_test_set_original_*.csv
python analyze_results.py actions results_action_commands_original_*.csv action_commands.csv
python analyze_results.py self results_self_questions_original_*.csv results_self_questions_corrected_*.csv
```

Never commit your API key or session logs containing children's speech.
