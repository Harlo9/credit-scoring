"""Summary sheet for a scored credit request.

Split of responsibilities, same principle as the scoring engine:
  - the code classifies and words the strengths and vigilance points, from
    the notes the engine produced
  - the LLM only writes the two free-text sections and the document list

An 8B model proved unable to keep a criterion out of both lists at once, and
kept copying the internal verdict words into the sheet. Classification is a
rule, not a writing task, so it belongs in the code.
"""

import json
import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(
    base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
    api_key="ollama",
)

MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

# Notes 0 and 1 are strengths, 2 and 3 are vigilance points.
STRENGTH_MAX_NOTE = 1

MAX_STRENGTHS = 3
MAX_VIGILANCE = 6

SCHEMA = {
    "synthese": (str,),
    "justification": (str,),
    "pieces_a_demander": (list,),
}

SYSTEM_PROMPT = """Tu es analyste crédit dans une banque. Tu rédiges deux paragraphes de la fiche de synthèse d'une demande de financement professionnel, à destination d'un chargé de clientèle.

Le score, le niveau de risque, la recommandation, les points forts et les points de vigilance sont déjà établis. Tu ne les recalcules pas, tu ne les contestes pas, tu ne les réécris pas.

RÈGLES ABSOLUES
- Tu n'utilises que les données fournies. Aucune information extérieure, aucun chiffre inventé.
- Tu écris en français professionnel, en phrases courtes.
- Tu ne mentionnes jamais le fonctionnement interne du moteur : pas de poids, pas de notes, pas de pondération.
- Tu renvoies uniquement le JSON, sans texte avant ou après, sans balises Markdown.

SCHÉMA DE SORTIE
{
  "synthese": "2 à 3 phrases : qui demande quoi, pour quel montant, et où se situe le dossier",
  "justification": "2 à 3 phrases expliquant la recommandation à partir des points de vigilance les plus lourds",
  "pieces_a_demander": ["documents à réclamer, un par ligne, formulation courte"]
}

PRÉCISIONS
- pieces_a_demander nomme toujours un document existant, jamais un champ de données. Exemples : "liasse fiscale des deux derniers exercices", "tableau d'amortissement de l'emprunt en cours", "plan de financement précisant la durée souhaitée". Ne jamais écrire "CAF réelle" ou "durée de prêt".
- La dernière phrase de la synthèse situe le dossier : score obtenu et orientation retenue. Jamais de formule de remplissage du type "dans un contexte de financement professionnel"."""


class ReportError(Exception):
    """Raised when the model output cannot be parsed or validated."""


def _clean(raw):
    """Strip Markdown fences and any prose around the JSON object."""
    text = raw.strip()
    if "```" in text:
        parts = text.split("```")
        if len(parts) > 1:
            text = parts[1]
            if text.startswith("json"):
                text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ReportError("No JSON object found in model output")
    return text[start : end + 1].strip()


def _validate(data):
    """Check every expected key is present with the right type."""
    if not isinstance(data, dict):
        raise ReportError("Model output is not a JSON object")
    for key, allowed in SCHEMA.items():
        if key not in data:
            raise ReportError(f"Missing key: {key}")
        if not isinstance(data[key], allowed):
            raise ReportError(f"Wrong type for {key}: {type(data[key]).__name__}")
    return {key: data[key] for key in SCHEMA}


def build_highlights(analysis):
    """Split criteria into strengths and vigilance points, deterministically.

    Vigilance also carries the missing values: an absent figure is a risk,
    not a neutral blank. Worst criteria come first, missing data last.
    """
    scored = [c for c in analysis["detail_criteres"] if c.get("phrase")]

    strengths = [c["phrase"] for c in scored if c["note"] <= STRENGTH_MAX_NOTE]

    weak = sorted(
        [c for c in scored if c["note"] > STRENGTH_MAX_NOTE],
        key=lambda c: (-c["note"], -c["poids"]),
    )
    vigilance = [c["phrase"] for c in weak] + analysis.get("donnees_manquantes", [])

    return strengths[:MAX_STRENGTHS], vigilance[:MAX_VIGILANCE]


def _build_input(data, analysis, strengths, vigilance):
    """Assemble the payload sent to the model."""
    return {
        "donnees_extraites": {k: v for k, v in data.items() if v is not None},
        "resultat": {
            "score_sur_100": analysis["score"],
            "niveau_risque": analysis["niveau_risque"],
            "recommandation": analysis["recommandation"],
            "completude_donnees": f"{analysis['completude']}%",
            "points_forts": strengths,
            "points_vigilance": vigilance,
            "donnees_manquantes": analysis.get("donnees_manquantes", []),
        },
    }


def _call_model(payload):
    """Send the request to the local model and return the raw text answer."""
    response = client.chat.completions.create(
        model=MODEL,
        temperature=0.2,  # slight freedom on wording, none on the figures
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "Rédige la synthèse et la justification à partir de "
                "ces éléments :\n\n" + json.dumps(payload, indent=2, ensure_ascii=False),
            },
        ],
    )
    return response.choices[0].message.content


def write_report(data, analysis):
    """Build the summary sheet for a scored credit request.

    Args:
        data: The dict returned by extraction.extract().
        analysis: The dict returned by scoring.compute_score().

    Returns:
        A flat dict ready for the Streamlit view.

    Raises:
        ReportError: If the model output stays invalid after one retry.
    """
    strengths, vigilance = build_highlights(analysis)
    payload = _build_input(data, analysis, strengths, vigilance)

    last_error = None
    for _ in range(2):  # one initial call, one retry
        try:
            written = _validate(json.loads(_clean(_call_model(payload))))
            break
        except (json.JSONDecodeError, ReportError) as error:
            last_error = error
    else:
        raise ReportError(f"Invalid model output after retry: {last_error}")

    return {
        "score": analysis["score"],
        "niveau_risque": analysis["niveau_risque"],
        "recommandation": analysis["recommandation"],
        "completude": analysis["completude"],
        "fiabilite": analysis["fiabilite"],
        "synthese": written["synthese"],
        "points_forts": strengths,
        "points_vigilance": vigilance,
        "justification": written["justification"],
        "pieces_a_demander": written["pieces_a_demander"],
        "hypotheses": analysis["hypotheses"],
        "alertes": analysis["alertes"],
        "detail_criteres": analysis["detail_criteres"],
    }


if __name__ == "__main__":
    from scoring import compute_score

    SAMPLE = {
        "loan_type": "pro",
        "requested_amount": 65000,
        "financing_purpose": "Véhicule utilitaire et matériel",
        "down_payment": 0,
        "existing_debt": 40000,
        "company_type": "SARL",
        "sector": "Bâtiment",
        "company_age_years": 3,
        "revenue": 420000,
        "net_income": 18000,
        "previous_net_income": 35000,
        "ebitda": None,
        "caf": None,
        "loan_duration_years": None,
        "existing_debt_annual_payment": None,
    }

    print(json.dumps(
        write_report(SAMPLE, compute_score(SAMPLE)),
        indent=2,
        ensure_ascii=False,
    ))