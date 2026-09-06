"""Deterministic risk scoring for credit requests.

The LLM extracts the data, this module computes the score. Same input always
gives the same output, and every point traces back to a criterion.

Score runs from 0 (very risky) to 100 (very safe). Higher is better.

Three numbers come out of this module, on purpose:
  - score: how solid the file looks, on the data we actually have
  - completeness: how much of the analysis we were able to run at all
  - alerts: what a human must look at, whatever the score says
Mixing them would hide the difference between a weak file and an incomplete
one. They are not the same conversation with the client.

SCOPE: business loans only. Consumer and mortgage requests are routed to a
manual review instead of being scored with a grid that does not fit them.

PROTOTYPE NOTE: sector coefficients, purpose coefficients and the cash-flow
proxy factor are working assumptions, not calibrated values. A production
version would fit them on observed default rates.
"""

import json

# --- Configuration -----------------------------------------------------------
# Everything tunable lives here. No threshold is hardcoded inside a function.

SUPPORTED_LOAN_TYPES = ("pro", None)  # None: type not stated, assumed business

DEFAULT_LOAN_YEARS = 5
DEFAULT_ANNUAL_RATE = 0.05

# Existing debt is usually given as an outstanding amount, not as an annual
# instalment. We estimate the instalment over this residual duration, unless
# the request states the actual instalment.
EXISTING_DEBT_YEARS = 4

# Net income understates repayment capacity: depreciation is a non-cash charge
# and comes back into cash flow (CAF = net income + depreciation, roughly).
# When no EBITDA or CAF is available, we apply this factor and say so in the
# output. 1.5 is a common order of magnitude for asset-heavy small companies.
CASH_FLOW_PROXY_FACTOR = 1.5

# Plausibility bounds. These catch extraction errors, not bad files: a
# revenue of 420 instead of 420000 would otherwise be scored as if real.
PLAUSIBILITY = {
    "requested_amount": (1_000, 10_000_000),
    "revenue": (1_000, 500_000_000),
    "company_age_years": (0, 150),
}

WEIGHTS = {
    # Repayment capacity carries the most weight: it is the actual question a
    # credit committee answers. Everything else qualifies it.
    "repayment_capacity": 30,
    # A profit that halves in one year changes the whole reading of the file,
    # hence a weight close to structural criteria.
    "income_trend": 15,
    # What is already committed limits what can be added on top.
    "total_debt": 15,
    # Margin matters, but thresholds vary so much by sector that it stays
    # secondary to cash generation.
    "profitability": 10,
    # No down payment means the bank carries the whole risk.
    "down_payment": 10,
    # Most business failures happen early, so age is a real signal, but a
    # young profitable company is not a bad file.
    "company_age": 10,
    # Sector and purpose are context, not decision drivers. Low weight also
    # limits the damage of the assumptions behind their tables.
    "sector": 5,
    "purpose": 5,
}

# Each entry maps an upper bound to a risk note from 0 (good) to 3 (bad).
# Read as: "below this value, this note".
THRESHOLDS = {
    # Total debt service against estimated cash flow. Below 50% the company
    # keeps a real safety margin; above 100% it cannot service the debt from
    # current operations.
    "debt_service": [(0.50, 0), (0.75, 1), (1.00, 2)],
    # Year-on-year profit change. A drop under 20% is noise, a drop over 40%
    # is a break in the trend.
    "income_trend": [(-0.40, 3), (-0.20, 2), (0.0, 1)],
    # Total debt against revenue. Bounds kept wide: this is a volume
    # indicator, the cash-flow criterion already covers affordability.
    "total_debt": [(0.25, 0), (0.45, 1), (0.65, 2)],
    # Net margin. Thresholds are deliberately low: construction, transport and
    # retail routinely run at 2 to 4%.
    "profitability": [(0.01, 3), (0.03, 2), (0.06, 1)],
    # Share of the project funded by the company itself.
    "down_payment": [(0.001, 2), (0.10, 1), (0.20, 0)],
    # Years of existence.
    "company_age": [(1.0, 3), (3.0, 2), (5.0, 1)],
}

# Sector coefficients. Working assumptions based on the usual suspects:
# payment delays, cash-flow seasonality and observed failure rates.
SECTOR_RISK = {
    "services": 0, "santé": 0, "sante": 0, "conseil": 0, "informatique": 0,
    "commerce": 1, "industrie": 1, "artisanat": 1, "agroalimentaire": 1,
    "bâtiment": 2, "batiment": 2, "btp": 2, "transport": 2, "agriculture": 2,
    "restauration": 3, "événementiel": 3, "evenementiel": 3, "hôtellerie": 3,
}

# Purpose coefficients. The logic is collateral: an asset the bank can seize
# and resell lowers its loss if the loan defaults. Real estate holds its value
# and is mortgageable; a van or a machine can be pledged and resold with a
# haircut; stock loses value fast; working capital leaves nothing to seize.
PURPOSE_RISK = {
    "immobilier": 0, "local": 0, "bureaux": 0, "entrepôt": 0,
    "véhicule": 1, "vehicule": 1, "utilitaire": 1, "matériel": 1,
    "materiel": 1, "équipement": 1, "equipement": 1, "machine": 1,
    "stock": 2, "marchandise": 2, "aménagement": 2,
    "trésorerie": 3, "tresorerie": 3, "bfr": 3, "fonds de roulement": 3,
}

# Collateral wording, by purpose note. Written here rather than by the model:
# the phrasing must stay stable across runs.
PURPOSE_PHRASES = {
    0: "Financement immobilier, actif fortement mobilisable",
    1: "Financement portant sur des actifs professionnels tangibles",
    2: "Financement de stock, valeur de revente incertaine",
    3: "Financement de trésorerie, sans actif mobilisable",
}

# An unlisted sector or purpose gets the median note rather than being
# dropped: we do know what is being financed, we just have no coefficient
# for it. Dropping it would lower the completeness figure for no reason.
DEFAULT_TABLE_NOTE = 1

# Score bands, on the 0-100 safety scale. No automatic rejection: the tool
# routes a file, a human decides.
DECISION_BANDS = [
    (75, "faible", "accord de principe"),
    (60, "modéré", "accord sous conditions"),
    (40, "élevé", "instruction en comité"),
    (0, "très élevé", "analyse renforcée, avis défavorable en l'état"),
]

# Below this share of the analysis actually run, the score is not reliable
# enough to carry a decision on its own.
COMPLETENESS_FLOOR = 70

# A missing value is a vigilance point, not a neutral blank. Each entry maps
# an absent field to the wording used in the sheet.
MISSING_DATA_LABELS = [
    (("caf", "ebitda"), "CAF réelle non communiquée"),
    (("loan_duration_years",), "Durée de prêt souhaitée non communiquée"),
    (("existing_debt_annual_payment",), "Annuité réelle de la dette existante inconnue"),
    (("previous_net_income",), "Résultat de l'exercice précédent non communiqué"),
    (("revenue",), "Chiffre d'affaires non communiqué"),
    (("net_income",), "Résultat net non communiqué"),
    (("company_age_years",), "Ancienneté de l'entreprise non communiquée"),
    (("down_payment",), "Apport non renseigné"),
]


# --- Helpers -----------------------------------------------------------------

def _num(d, key):
    """Read a numeric field, treating only None as missing.

    Plain truthiness would treat a net income of exactly 0 as absent, which
    is wrong: zero profit is strong information, not missing information.
    """
    value = d.get(key)
    return value if isinstance(value, (int, float)) else None


def _grade(value, bounds, default_note=3):
    """Turn a ratio into a 0-3 risk note using an ordered list of bounds."""
    for upper, note in bounds:
        if value < upper:
            return note
    return default_note


def _lookup(text, table):
    """Find a risk note by matching keywords inside a free-text label."""
    if not text:
        return None
    lowered = text.lower()
    for keyword, note in table.items():
        if keyword in lowered:
            return note
    return DEFAULT_TABLE_NOTE


def annual_payment(amount, years=DEFAULT_LOAN_YEARS, rate=DEFAULT_ANNUAL_RATE):
    """Yearly instalment of a fixed-rate amortizing loan."""
    monthly_rate = rate / 12
    months = years * 12
    monthly = amount * monthly_rate / (1 - (1 + monthly_rate) ** -months)
    return monthly * 12


def cash_flow(d):
    """Best available proxy for the cash available to service debt.

    Returns (value, is_proxy). EBITDA or CAF is used when present. Otherwise
    net income is scaled up, and the caller flags it as an assumption.
    """
    for key in ("caf", "ebitda"):
        value = _num(d, key)
        if value is not None:
            return value, False
    net = _num(d, "net_income")
    if net is None:
        return None, False
    return net * CASH_FLOW_PROXY_FACTOR, True


def debt_service(d):
    """Yearly cost of the new loan plus the existing debt."""
    amount = _num(d, "requested_amount")
    if amount is None:
        return None

    years = _num(d, "loan_duration_years") or DEFAULT_LOAN_YEARS
    service = annual_payment(amount, years=years)

    stated = _num(d, "existing_debt_annual_payment")
    if stated is not None:
        service += stated
    else:
        outstanding = _num(d, "existing_debt")
        if outstanding:
            service += annual_payment(outstanding, years=EXISTING_DEBT_YEARS)

    return service


def check_plausibility(d):
    """Flag values outside a sane range: usually an extraction error."""
    issues = []

    for key, (low, high) in PLAUSIBILITY.items():
        value = _num(d, key)
        if value is not None and not low <= value <= high:
            issues.append(f"{key} hors plage plausible ({value})")

    revenue, net = _num(d, "revenue"), _num(d, "net_income")
    if revenue is not None and net is not None and net > revenue:
        issues.append("résultat net supérieur au chiffre d'affaires")

    amount, down = _num(d, "requested_amount"), _num(d, "down_payment")
    if amount is not None and down is not None and down > amount:
        issues.append("apport supérieur au montant demandé")

    return issues


# --- Criteria ----------------------------------------------------------------
# Each returns (risk note 0-3, displayed value). A note of None means the
# criterion could not be computed: it is then excluded from the weighting
# rather than filled with a neutral value, so missing data cannot quietly
# push the score up or down.

def score_repayment_capacity(d):
    """Total debt service against estimated cash flow.

    Existing debt is included: a file is not affordable just because the new
    loan alone would be.
    """
    flow, _ = cash_flow(d)
    service = debt_service(d)
    if service is None or flow is None:
        return None, "non calculable", None
    if flow <= 0:
        return 3, "CAF négative ou nulle", "Capacité d'autofinancement négative"

    ratio = service / flow
    note = _grade(ratio, THRESHOLDS["debt_service"])
    verb = "contenu à" if note <= 1 else "estimé à"
    return note, f"{ratio:.0%} de la CAF", f"Service de la dette {verb} {ratio:.0%} de la CAF"


def score_income_trend(d):
    """Year-on-year change in profit."""
    net, previous = _num(d, "net_income"), _num(d, "previous_net_income")
    if net is None or previous is None or previous == 0:
        return None, "non calculable", None
    change = (net - previous) / abs(previous)
    note = _grade(change, THRESHOLDS["income_trend"], default_note=0)
    phrase = (f"Résultat net en progression de {change:.0%}" if change >= 0
              else f"Résultat net en baisse de {abs(change):.0%}")
    return note, f"{change:+.0%}", phrase


def score_total_debt(d):
    """Existing debt plus the new loan, against revenue."""
    revenue, amount = _num(d, "revenue"), _num(d, "requested_amount")
    if not revenue or amount is None:
        return None, "non calculable", None
    ratio = ((_num(d, "existing_debt") or 0) + amount) / revenue
    note = _grade(ratio, THRESHOLDS["total_debt"])
    phrase = (f"Endettement contenu à {ratio:.0%} du CA" if note <= 1
              else f"Dette totale représentant {ratio:.0%} du CA")
    return note, f"{ratio:.0%} du CA", phrase


def score_profitability(d):
    """Net margin."""
    revenue, net = _num(d, "revenue"), _num(d, "net_income")
    if not revenue or net is None:
        return None, "non calculable", None
    ratio = net / revenue
    note = _grade(ratio, THRESHOLDS["profitability"], default_note=0)
    if ratio < 0:
        phrase = f"Marge nette négative de {ratio:.1%}"
    elif note <= 1:
        phrase = f"Rentabilité positive de {ratio:.1%}"
    else:
        phrase = f"Rentabilité faible de {ratio:.1%}"
    return note, f"{ratio:.1%}", phrase


def score_down_payment(d):
    """Share of the project funded by the company itself."""
    down, amount = _num(d, "down_payment"), _num(d, "requested_amount")
    if down is None or not amount:
        return None, "non renseigné", None
    ratio = down / amount
    note = _grade(ratio, THRESHOLDS["down_payment"], default_note=0)
    if ratio == 0:
        phrase = "Aucun apport"
    elif note <= 1:
        phrase = f"Apport de {ratio:.0%} du montant demandé"
    else:
        phrase = f"Apport limité à {ratio:.0%} du montant demandé"
    return note, f"{ratio:.0%}", phrase


def score_company_age(d):
    """Years of existence."""
    years = _num(d, "company_age_years")
    if years is None:
        return None, "non renseigné", None
    note = _grade(years, THRESHOLDS["company_age"], default_note=0)
    phrase = (f"Entreprise établie depuis {years:g} ans" if note <= 1
              else f"Entreprise jeune, {years:g} ans d'existence")
    return note, f"{years:g} ans", phrase


def score_sector(d):
    """Sector coefficient. Prototype assumption, see module docstring."""
    note = _lookup(d.get("sector"), SECTOR_RISK)
    label = d.get("sector") or "non renseigné"
    if note is None:
        return None, label, None
    phrase = (f"Secteur {label.lower()} peu exposé" if note <= 1
              else f"Secteur {label.lower()} exposé aux défaillances")
    return note, label, phrase


def score_purpose(d):
    """Collateral value of the financed asset."""
    note = _lookup(d.get("financing_purpose"), PURPOSE_RISK)
    label = d.get("financing_purpose") or "non renseigné"
    if note is None:
        return None, label, None
    return note, label, PURPOSE_PHRASES[note]


CRITERIA = {
    "repayment_capacity": ("Capacité de remboursement", score_repayment_capacity),
    "income_trend": ("Tendance du résultat", score_income_trend),
    "total_debt": ("Endettement global", score_total_debt),
    "profitability": ("Rentabilité", score_profitability),
    "down_payment": ("Apport", score_down_payment),
    "company_age": ("Ancienneté", score_company_age),
    "sector": ("Secteur", score_sector),
    "purpose": ("Nature du bien financé", score_purpose),
}


# --- Main entry point --------------------------------------------------------

def compute_score(data):
    """Score a credit request from extracted data.

    Args:
        data: The dict returned by extraction.extract().

    Returns:
        dict with score (0-100, higher is safer), niveau_risque,
        recommandation, completude, fiabilite, alertes, hypotheses,
        criteres_non_evalues and detail_criteres.
    """
    loan_type = data.get("loan_type")
    if loan_type not in SUPPORTED_LOAN_TYPES:
        return _out_of_scope(loan_type)

    details, risk_points, active_weight, missing = [], 0, 0, []

    for key, (label, func) in CRITERIA.items():
        note, display, phrase = func(data)
        weight = WEIGHTS[key]

        if note is None:
            missing.append(label)
        else:
            risk_points += note * weight
            active_weight += weight

        details.append({
            "critere": label,
            "valeur": display,
            "phrase": phrase,
            "note": note,
            "poids": weight,
        })

    # Weights are renormalized over the criteria we could actually compute.
    # A missing criterion neither penalizes nor rewards the file: it shows up
    # in the completeness figure instead.
    score = None
    if active_weight:
        score = round(100 - (risk_points / (active_weight * 3)) * 100)

    completeness = round(active_weight / sum(WEIGHTS.values()) * 100)
    reliable = completeness >= COMPLETENESS_FLOOR
    issues = check_plausibility(data)

    level, recommendation = _decide(score)

    # Reliability gates. These are about the data, not about the file.
    if score is None:
        level = "indéterminé"
        recommendation = "demande de pièces complémentaires"
    elif not reliable or issues:
        recommendation = "demande de pièces complémentaires"

    return {
        "score": score,
        "niveau_risque": level,
        "recommandation": recommendation,
        "completude": completeness,
        "fiabilite": "suffisante" if reliable and not issues else "insuffisante",
        "alertes": _alerts(data) + [f"Donnée douteuse : {i}" for i in issues],
        "hypotheses": _assumptions(data),
        "criteres_non_evalues": missing,
        "donnees_manquantes": missing_data(data),
        "detail_criteres": details,
    }


def missing_data(d):
    """Wording for every value the analysis had to do without."""
    labels = []
    for fields, label in MISSING_DATA_LABELS:
        if all(_num(d, f) is None for f in fields):
            labels.append(label)
    return labels


def _out_of_scope(loan_type):
    """Return a clear refusal to score rather than a wrong score.

    The grid is built on company accounts. Applying it to a consumer or
    mortgage request would produce a number that looks credible and means
    nothing.
    """
    return {
        "score": None,
        "niveau_risque": "non évalué",
        "recommandation": "traitement manuel : grille non applicable",
        "completude": 0,
        "fiabilite": "non applicable",
        "alertes": [f"Type de crédit « {loan_type} » hors périmètre de l'outil, "
                    "qui ne couvre que le crédit professionnel."],
        "hypotheses": [],
        "criteres_non_evalues": [label for label, _ in CRITERIA.values()],
        "donnees_manquantes": [],
        "donnees_manquantes": [],
        "detail_criteres": [],
    }


def _decide(score):
    """Map a safety score to a risk level and a routing recommendation."""
    if score is None:
        return "indéterminé", "demande de pièces complémentaires"
    for floor, level, recommendation in DECISION_BANDS:
        if score >= floor:
            return level, recommendation
    return "très élevé", "analyse renforcée, avis défavorable en l'état"


def _alerts(d):
    """Points that require a human look, whatever the score says.

    These do not reject the file on their own. A loss-making year can be an
    investment year, and a young company can be a spin-off with a full order
    book. The tool flags, the committee decides.
    """
    alerts = []
    net = _num(d, "net_income")
    age = _num(d, "company_age_years")

    if net is not None and net < 0:
        alerts.append("Résultat net négatif : analyse renforcée requise, "
                      "passage en comité recommandé.")
    if age is not None and age < 1:
        alerts.append("Entreprise de moins de 12 mois : pas d'historique "
                      "exploitable, garanties personnelles à examiner.")
    if _num(d, "down_payment") == 0:
        alerts.append("Aucun apport : financement à 100%, la banque porte "
                      "l'intégralité du risque.")
    return alerts


def _assumptions(d):
    """Assumptions used in the computation, stated so they can be challenged."""
    notes = []

    _, is_proxy = cash_flow(d)
    if is_proxy:
        notes.append(f"CAF estimée à {CASH_FLOW_PROXY_FACTOR} fois le résultat "
                     "net, faute d'EBITDA ou de CAF communiquée.")

    if _num(d, "requested_amount") is not None and not _num(d, "loan_duration_years"):
        notes.append(f"Nouveau prêt simulé sur {DEFAULT_LOAN_YEARS} ans à "
                     f"{DEFAULT_ANNUAL_RATE:.0%}, durée non communiquée.")

    if _num(d, "existing_debt") and _num(d, "existing_debt_annual_payment") is None:
        notes.append(f"Encours existant amorti sur {EXISTING_DEBT_YEARS} ans, "
                     "annuité réelle non communiquée.")

    return notes


if __name__ == "__main__":
    SAMPLE = {
        "loan_type": "pro",
        "company_type": "SARL",
        "sector": "Bâtiment",
        "company_age_years": 3,
        "revenue": 420000,
        "net_income": 18000,
        "previous_net_income": 35000,
        "requested_amount": 65000,
        "existing_debt": 40000,
        "down_payment": 0,
        "financing_purpose": "Véhicule utilitaire et matériel",
    }

    print(json.dumps(compute_score(SAMPLE), indent=2, ensure_ascii=False))