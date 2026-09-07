"""Field-level evaluation of the LLM extraction step.

The scoring engine is deterministic and already covered by pytest. This
script covers the part that is not: how well a local model reads a real
request, and whether its mistakes move the final score.

Requires a running Ollama instance.
"""

import sys
from pathlib import Path

# The project root, so `extraction` and `scoring` are importable no matter
# which directory the script is launched from.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import json
from collections import defaultdict

from extraction import extract, ExtractionError, SCHEMA
from sanitize import scan
from scoring import compute_score, _lookup, SECTOR_RISK, PURPOSE_RISK

DATASET = Path(__file__).parent / "dataset.jsonl"
RESULTS = Path(__file__).parent / "results.md"

# Free-text fields are not compared as strings: "Bâtiment", "BTP" and
# "travaux de maçonnerie" are three correct readings of one sector. What
# matters is the risk note the grid ends up reading from them.
GRID_FIELDS = {"sector": SECTOR_RISK, "financing_purpose": PURPOSE_RISK}

# Relative tolerance on amounts. 419 999 instead of 420 000 is a correct
# reading, not an extraction error.
TOLERANCE = 0.01

VERDICTS = ("ok", "miss", "wrong", "hallucination")


def verdict(field, expected, predicted):
    """Classify one field of one case.

    The four outcomes are not equally bad. A hallucination is the worst:
    the sheet then shows a figure nobody wrote, and the grid scores it as
    fact. A miss only lowers the completeness figure, which is what that
    figure is for.
    """
    if expected is None and predicted is None:
        return "ok"
    if expected is None:
        return "hallucination"
    if predicted is None:
        return "miss"
    if field in GRID_FIELDS:
        table = GRID_FIELDS[field]
        return "ok" if _lookup(expected, table) == _lookup(predicted, table) else "wrong"
    if isinstance(expected, str):
        return "ok" if expected.strip().lower() == str(predicted).strip().lower() else "wrong"
    if expected == 0:
        return "ok" if predicted == 0 else "wrong"
    return "ok" if abs(predicted - expected) / abs(expected) <= TOLERANCE else "wrong"


def evaluate(runs=1):
    """Run every case `runs` times and aggregate the outcomes."""
    cases = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
    total = defaultdict(int)
    per_field = defaultdict(lambda: defaultdict(int))
    deltas, band_changes, crashes, worst = [], 0, [], []

    for case in cases:
        for _ in range(runs):
            try:
                predicted = extract(case["text"])
            except ExtractionError as error:
                crashes.append((case["id"], str(error)))
                continue

            for field in SCHEMA:
                result = verdict(field, case["expected"].get(field), predicted.get(field))
                total[result] += 1
                per_field[field][result] += 1
                if result == "hallucination":
                    worst.append((case["id"], field, predicted.get(field)))

            reference = compute_score({**case["expected"],
                                       "injection_signals": scan(case["text"])})
            obtained = compute_score(predicted)
            if reference["score"] is not None and obtained["score"] is not None:
                deltas.append(abs(reference["score"] - obtained["score"]))
            if reference["recommandation"] != obtained["recommandation"]:
                band_changes += 1

    return {
        "cases": len(cases),
        "runs": runs,
        "total": total,
        "per_field": per_field,
        "deltas": deltas,
        "band_changes": band_changes,
        "crashes": crashes,
        "hallucinations": worst,
    }


def render(r):
    """Format the results as Markdown, ready to paste into the README."""
    fields = sum(r["total"].values())
    accuracy = r["total"]["ok"] / fields * 100 if fields else 0
    mean_delta = sum(r["deltas"]) / len(r["deltas"]) if r["deltas"] else 0
    attempts = r["cases"] * r["runs"]

    lines = [
        f"# Évaluation de l'extraction",
        "",
        f"{r['cases']} demandes annotées à la main, {r['runs']} passage(s) chacune.",
        "",
        "| Métrique | Valeur |",
        "| --- | --- |",
        f"| Justesse par champ | {accuracy:.1f} % ({r['total']['ok']}/{fields}) |",
        f"| Hallucinations | {r['total']['hallucination']} |",
        f"| Champs manqués | {r['total']['miss']} |",
        f"| Valeurs erronées | {r['total']['wrong']} |",
        f"| Écart de score moyen | {mean_delta:.1f} points sur 100 |",
        f"| Recommandation changée | {r['band_changes']}/{attempts} |",
        f"| Extractions en échec | {len(r['crashes'])} |",
        "",
        "## Par champ",
        "",
        "| Champ | OK | Manqué | Erroné | Halluciné |",
        "| --- | --- | --- | --- | --- |",
    ]
    for field in SCHEMA:
        counts = r["per_field"][field]
        lines.append(f"| `{field}` | {counts['ok']} | {counts['miss']} | "
                     f"{counts['wrong']} | {counts['hallucination']} |")

    if r["hallucinations"]:
        lines += ["", "## Hallucinations relevées", ""]
        lines += [f"- `{case}` / `{field}` : {value}" for case, field, value in r["hallucinations"]]

    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate the extraction step.")
    parser.add_argument("--runs", type=int, default=1,
                        help="passes per case; use 3 to see the model's variance")
    report = render(evaluate(parser.parse_args().runs))
    RESULTS.write_text(report, encoding="utf-8")
    print(report)