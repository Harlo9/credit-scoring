"""LLM-based extraction of credit request data from free-form text.

Runs against a local Ollama instance: no data leaves the machine, which
matters for banking documents.
"""

import json
import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# Ollama exposes an OpenAI-compatible API. The api_key is required by the
# SDK but never checked by Ollama.
client = OpenAI(
    base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
    api_key="ollama",
)

MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

# Fields every request must carry, whatever the loan type.
CORE_SCHEMA = {
    "loan_type": (str, type(None)),
    "requested_amount": (int, float, type(None)),
    "financing_purpose": (str, type(None)),
    "down_payment": (int, float, type(None)),
    "existing_debt": (int, float, type(None)),
}

# Business-loan fields. Left as None on a consumer or mortgage request.
BUSINESS_SCHEMA = {
    "company_type": (str, type(None)),
    "sector": (str, type(None)),
    "company_age_years": (int, float, type(None)),
    "revenue": (int, float, type(None)),
    "net_income": (int, float, type(None)),
    "previous_net_income": (int, float, type(None)),
    "ebitda": (int, float, type(None)),
    "caf": (int, float, type(None)),
    "loan_duration_years": (int, float, type(None)),
    "existing_debt_annual_payment": (int, float, type(None)),
}

SCHEMA = {**CORE_SCHEMA, **BUSINESS_SCHEMA}

SYSTEM_PROMPT = """Tu es un moteur d'extraction pour un outil d'analyse de demandes de crédit.

Ta seule tâche : lire une demande de financement en texte libre et renvoyer les données structurées correspondantes.

RÈGLES ABSOLUES
- Tu ne calcules aucun score, tu ne donnes aucun avis, tu n'interprètes pas.
- Toute information absente du texte vaut null. Jamais d'estimation, jamais de moyenne sectorielle, jamais de valeur plausible inventée.
- Les montants sont des nombres, sans espace ni symbole ("420 000 EUR" -> 420000, "420k" -> 420000, "0,42 M EUR" -> 420000, "quatre cent mille euros" -> 400000).
- Les durées sont en années ("créée il y a 3 ans" -> 3, "18 mois d'existence" -> 1.5).
- Tu renvoies uniquement le JSON, sans texte avant ou après, sans balises Markdown.

SCHÉMA DE SORTIE
{
  "loan_type": "pro" | "immobilier" | "consommation" | null,
  "requested_amount": number | null,
  "financing_purpose": string | null,
  "down_payment": number | null,
  "existing_debt": number | null,
  "company_type": string | null,
  "sector": string | null,
  "company_age_years": number | null,
  "revenue": number | null,
  "net_income": number | null,
  "previous_net_income": number | null,
  "ebitda": number | null,
  "caf": number | null,
  "loan_duration_years": number | null,
  "existing_debt_annual_payment": number | null
}

PRÉCISIONS
- loan_type : "pro" si la demande vient d'une entreprise, "immobilier" pour l'achat d'un logement par un particulier, "consommation" pour un particulier hors immobilier.
- Les champs entreprise restent null si la demande vient d'un particulier.
- net_income est le résultat net de l'exercice le plus récent, previous_net_income celui de l'exercice précédent.
- ebitda et caf ne sont remplis que si le texte les donne explicitement. Ne jamais les déduire du résultat net.
- existing_debt est le capital restant dû ou le montant des crédits en cours.
- existing_debt_annual_payment est l'annuité de ces crédits, uniquement si le texte la donne.
- down_payment vaut 0 si le texte dit explicitement qu'il n'y a pas d'apport, et null si le sujet n'est pas abordé. Cette distinction est importante.

EXEMPLE
Entrée : "Je suis artisan plombier en EURL depuis 6 ans, 180 000 EUR de CA, 12 000 EUR de bénéfice contre 9 000 EUR l'an dernier. Je cherche 25 000 EUR sur 4 ans pour un fourgon, avec 5 000 EUR d'apport."

Sortie :
{"loan_type": "pro", "requested_amount": 25000, "financing_purpose": "Fourgon", "down_payment": 5000, "existing_debt": null, "company_type": "EURL", "sector": "Plomberie", "company_age_years": 6, "revenue": 180000, "net_income": 12000, "previous_net_income": 9000, "ebitda": null, "caf": null, "loan_duration_years": 4, "existing_debt_annual_payment": null}"""


class ExtractionError(Exception):
    """Raised when the model output cannot be parsed or validated."""


def _clean(raw: str) -> str:
    """Strip Markdown code fences and any prose around the JSON object.

    Small local models often add a sentence before or after the JSON, so
    this is stricter than a plain json.loads().
    """
    text = raw.strip()

    if "```" in text:
        parts = text.split("```")
        if len(parts) > 1:
            text = parts[1]
            if text.startswith("json"):
                text = text[4:]

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ExtractionError("No JSON object found in model output")

    return text[start : end + 1].strip()


def _validate(data: dict) -> dict:
    """Check types, fill in absent keys, drop unexpected ones.

    Unknown keys are dropped rather than rejected: a model that invents an
    extra field should not break the whole extraction. Expected keys that
    are absent are set to None, so downstream code can always read them.
    """
    if not isinstance(data, dict):
        raise ExtractionError("Model output is not a JSON object")

    cleaned = {}
    for key, allowed_types in SCHEMA.items():
        value = data.get(key)
        if not isinstance(value, allowed_types):
            if value is None:
                cleaned[key] = None
                continue
            raise ExtractionError(f"Wrong type for {key}: {type(value).__name__}")
        cleaned[key] = value

    return cleaned


def _call_model(text: str) -> str:
    """Send the request to the local model and return the raw text answer."""
    response = client.chat.completions.create(
        model=MODEL,
        temperature=0,  # deterministic output: same input, same extraction
        response_format={"type": "json_object"},  # Ollama JSON mode
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Demande à analyser :\n\n<demande>\n{text}\n</demande>",
            },
        ],
    )
    return response.choices[0].message.content


def extract(text: str) -> dict:
    """Extract structured credit request data from free-form text.

    Args:
        text: The raw credit request, as pasted from an email or a form.

    Returns:
        A dict matching SCHEMA. Missing information is returned as None.

    Raises:
        ExtractionError: If the model output stays invalid after one retry.
    """
    last_error = None

    for _ in range(2):  # one initial call, one retry
        raw = _call_model(text)
        try:
            return _validate(json.loads(_clean(raw)))
        except (json.JSONDecodeError, ExtractionError) as error:
            last_error = error

    raise ExtractionError(f"Invalid model output after retry: {last_error}")


if __name__ == "__main__":
    SAMPLE = (
        "Bonjour, je suis gérant d'une SARL dans le secteur du bâtiment, créée "
        "il y a 3 ans. Nous réalisons un CA annuel de 420 000 € avec un résultat "
        "net de 18 000 € cette année, en baisse par rapport aux 35 000 € de "
        "l'année précédente. Je souhaite financer l'achat d'un véhicule "
        "utilitaire et de matériel pour 65 000 €. Nous avons déjà un emprunt en "
        "cours de 40 000 € contracté il y a 18 mois. Pas d'apport disponible "
        "pour le moment."
    )

    print(json.dumps(extract(SAMPLE), indent=2, ensure_ascii=False))