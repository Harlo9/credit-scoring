"""Characterization tests for the scoring engine.

These tests pin down the behaviour the engine has today. They are written
against the constants exported by scoring.py rather than against copies of
them: retuning a weight or a threshold in the module should move the tests
with it, and only a change of *behaviour* should break them.

What is deliberately not asserted: the exact score of a given file. Scores
are a consequence of weights and thresholds, both flagged as prototype
assumptions in the module. What is asserted instead is the arithmetic that
turns notes into a score, and the qualitative reading of a file.
"""

import pytest

import scoring
from scoring import compute_score


# --- Helpers -----------------------------------------------------------------

def notes(result):
    """Risk note per criterion label, as the sheet would read them."""
    return {c["critere"]: c["note"] for c in result["detail_criteres"]}


def note_of(result, key):
    """Risk note of one criterion, addressed by its CRITERIA key."""
    return notes(result)[scoring.CRITERIA[key][0]]


def pro(**overrides):
    """A business request that scores well, with the fields under test on top.

    Every criterion is computable, so completeness is 100% and a criterion
    can be knocked out one at a time by passing None. The request states it
    carries no existing debt, so nothing is left for the engine to assume.
    """
    data = {
        "loan_type": "pro",
        "sector": "Services",
        "financing_purpose": "Local commercial",
        "company_age_years": 12,
        "revenue": 1_200_000,
        "net_income": 140_000,
        "previous_net_income": 110_000,
        "caf": 220_000,
        "requested_amount": 150_000,
        "loan_duration_years": 7,
        "existing_debt": 0,
        "existing_debt_annual_payment": 0,
        "down_payment": 45_000,
    }
    data.update(overrides)
    return data


# --- A solid file ------------------------------------------------------------

def test_strong_file_scores_in_the_lowest_risk_band():
    """A complete, profitable, low-leverage file lands on the top band."""
    result = compute_score(pro())

    assert result["niveau_risque"] == "faible"
    assert result["recommandation"] == "accord de principe"
    assert result["completude"] == 100
    assert result["fiabilite"] == "suffisante"
    assert result["criteres_non_evalues"] == []
    assert result["donnees_manquantes"] == []
    assert result["alertes"] == []
    assert all(note == 0 for note in notes(result).values())


def test_strong_file_states_no_assumption():
    """Duration, CAF and existing debt are all given: nothing is assumed."""
    assert compute_score(pro())["hypotheses"] == []


# --- A weak file -------------------------------------------------------------

WEAK = {
    "loan_type": "pro",
    "sector": "Restauration",
    "financing_purpose": "Trésorerie",
    "company_age_years": 0.5,
    "revenue": 200_000,
    "net_income": -15_000,
    "previous_net_income": 20_000,
    "requested_amount": 180_000,
    "existing_debt": 90_000,
    "down_payment": 0,
}


def test_weak_file_scores_in_the_highest_risk_band():
    """Loss-making, young, over-leveraged, no down payment: worst band."""
    result = compute_score(WEAK)

    assert result["niveau_risque"] == "très élevé"
    assert result["recommandation"] == "analyse renforcée, avis défavorable en l'état"
    assert result["score"] < 40


def test_weak_file_raises_every_alert():
    """Negative profit, under a year old and no down payment each alert."""
    alerts = " ".join(compute_score(WEAK)["alertes"])

    assert "Résultat net négatif" in alerts
    assert "moins de 12 mois" in alerts
    assert "Aucun apport" in alerts


def test_negative_cash_flow_is_the_worst_repayment_note():
    """A negative CAF is not a ratio: it is graded straight to 3."""
    result = compute_score(pro(caf=None, net_income=-50_000))
    detail = next(c for c in result["detail_criteres"]
                  if c["critere"] == scoring.CRITERIA["repayment_capacity"][0])

    assert detail["note"] == 3
    assert detail["valeur"] == "CAF négative ou nulle"


# --- The score arithmetic ----------------------------------------------------

def test_score_is_the_weighted_notes_renormalized_over_computed_criteria():
    """The published score is reproducible from the published detail rows.

    This is the contract the sheet relies on: every point traces back to a
    criterion, and a missing criterion is excluded rather than neutralized.
    """
    result = compute_score(pro(down_payment=None, company_age_years=None))

    graded = [c for c in result["detail_criteres"] if c["note"] is not None]
    risk_points = sum(c["note"] * c["poids"] for c in graded)
    active_weight = sum(c["poids"] for c in graded)

    assert result["score"] == round(100 - (risk_points / (active_weight * 3)) * 100)


def test_completeness_is_the_share_of_weight_actually_graded():
    result = compute_score(pro(down_payment=None, company_age_years=None))

    active_weight = sum(c["poids"] for c in result["detail_criteres"]
                        if c["note"] is not None)
    total = sum(scoring.WEIGHTS.values())

    assert result["completude"] == round(active_weight / total * 100)


def test_a_missing_criterion_neither_penalizes_nor_rewards():
    """Dropping a criterion the file scored at 0 leaves the score untouched.

    Weights are renormalized over what could be computed, so the loss shows
    up in completeness, not in the score.
    """
    full = compute_score(pro())
    without_age = compute_score(pro(company_age_years=None))

    assert without_age["score"] == full["score"]
    assert without_age["completude"] < full["completude"]


# --- Missing data ------------------------------------------------------------

def test_ungradable_criterion_is_listed_not_scored():
    result = compute_score(pro(company_age_years=None))

    assert note_of(result, "company_age") is None
    assert scoring.CRITERIA["company_age"][0] in result["criteres_non_evalues"]
    assert "Ancienneté de l'entreprise non communiquée" in result["donnees_manquantes"]


def test_a_request_with_no_figures_is_not_scored_at_all():
    """No criterion computable: the engine returns no score rather than 0."""
    result = compute_score({"loan_type": "pro"})

    assert result["score"] is None
    assert result["niveau_risque"] == "indéterminé"
    assert result["recommandation"] == "demande de pièces complémentaires"
    assert result["completude"] == 0
    assert result["fiabilite"] == "insuffisante"
    assert len(result["criteres_non_evalues"]) == len(scoring.CRITERIA)


def test_cash_flow_label_clears_on_either_caf_or_ebitda():
    """The pair is one piece of information: either figure answers it."""
    label = "CAF réelle non communiquée"

    assert label in scoring.missing_data({})
    assert label not in scoring.missing_data({"caf": 10_000})
    assert label not in scoring.missing_data({"ebitda": 10_000})


INSTALMENT_LABEL = "Annuité réelle de la dette existante inconnue"


@pytest.mark.parametrize("existing_debt", [None, 0])
def test_no_existing_debt_means_no_instalment_is_expected(existing_debt):
    """Nothing to service: the annuity is not a gap in the file."""
    data = pro(existing_debt=existing_debt, existing_debt_annual_payment=None)

    assert INSTALMENT_LABEL not in scoring.missing_data(data)
    assert INSTALMENT_LABEL not in compute_score(data)["donnees_manquantes"]


def test_an_existing_debt_without_its_instalment_is_missing_data():
    data = pro(existing_debt=80_000, existing_debt_annual_payment=None)

    assert INSTALMENT_LABEL in scoring.missing_data(data)
    assert INSTALMENT_LABEL in compute_score(data)["donnees_manquantes"]


def test_an_existing_debt_with_its_instalment_is_complete():
    data = pro(existing_debt=80_000, existing_debt_annual_payment=24_000)

    assert INSTALMENT_LABEL not in scoring.missing_data(data)
    assert INSTALMENT_LABEL not in compute_score(data)["donnees_manquantes"]


DURATION_LABEL = "Durée de prêt souhaitée non communiquée"


@pytest.mark.parametrize("requested_amount", [None, 0])
def test_no_loan_requested_means_no_duration_is_expected(requested_amount):
    """Nothing to spread over a term: the duration is not a gap in the file."""
    data = pro(requested_amount=requested_amount, loan_duration_years=None)

    assert DURATION_LABEL not in scoring.missing_data(data)
    assert DURATION_LABEL not in compute_score(data)["donnees_manquantes"]


def test_a_requested_amount_without_its_duration_is_missing_data():
    data = pro(requested_amount=150_000, loan_duration_years=None)

    assert DURATION_LABEL in scoring.missing_data(data)
    assert DURATION_LABEL in compute_score(data)["donnees_manquantes"]


def test_a_requested_amount_with_its_duration_is_complete():
    data = pro(requested_amount=150_000, loan_duration_years=7)

    assert DURATION_LABEL not in scoring.missing_data(data)
    assert DURATION_LABEL not in compute_score(data)["donnees_manquantes"]


def test_a_value_with_no_prerequisite_is_always_expected():
    """Only the two conditional labels are gated; the rest always apply."""
    labels = scoring.missing_data({})

    assert "Chiffre d'affaires non communiqué" in labels
    assert "Résultat net non communiqué" in labels
    assert "CAF réelle non communiquée" in labels


def test_missing_data_keeps_the_order_of_the_label_table():
    """report.build_highlights lists these last and truncates: order matters."""
    labels = scoring.missing_data({"existing_debt": 80_000,
                                   "requested_amount": 150_000})
    expected = [label for _, label in scoring.MISSING_DATA_LABELS]

    assert labels == expected


def test_assumptions_are_stated_when_figures_are_missing():
    result = compute_score(pro(caf=None, loan_duration_years=None,
                               existing_debt=80_000,
                               existing_debt_annual_payment=None))
    stated = " ".join(result["hypotheses"])

    assert "CAF estimée" in stated
    assert f"{scoring.DEFAULT_LOAN_YEARS} ans" in stated
    assert f"amorti sur {scoring.EXISTING_DEBT_YEARS} ans" in stated


# --- Zero is not the same as absent ------------------------------------------

def test_zero_down_payment_is_graded_absent_down_payment_is_not():
    """The distinction the extraction prompt insists on, enforced here.

    A stated "no down payment" is a graded criterion and an alert; a down
    payment nobody mentioned is missing data and stays out of the score.
    """
    stated = compute_score(pro(down_payment=0))
    absent = compute_score(pro(down_payment=None))

    assert note_of(stated, "down_payment") is not None
    assert "Aucun apport" in " ".join(stated["alertes"])
    assert "Apport non renseigné" not in stated["donnees_manquantes"]

    assert note_of(absent, "down_payment") is None
    assert absent["alertes"] == []
    assert "Apport non renseigné" in absent["donnees_manquantes"]
    assert absent["completude"] < stated["completude"]


def test_zero_net_income_is_graded_absent_net_income_is_not():
    """Zero profit is strong information; no profit figure is none at all."""
    stated = compute_score(pro(caf=None, net_income=0))
    absent = compute_score(pro(caf=None, net_income=None))

    assert note_of(stated, "profitability") is not None
    assert "Résultat net non communiqué" not in stated["donnees_manquantes"]

    assert note_of(absent, "profitability") is None
    assert "Résultat net non communiqué" in absent["donnees_manquantes"]


def test_num_keeps_zero_and_drops_non_numbers():
    assert scoring._num({"x": 0}, "x") == 0
    assert scoring._num({"x": 0.0}, "x") == 0.0
    assert scoring._num({"x": None}, "x") is None
    assert scoring._num({}, "x") is None
    assert scoring._num({"x": "40000"}, "x") is None


# --- The completeness floor --------------------------------------------------

def gradable_only(*keys):
    """A file where only the given criteria can be graded."""
    blanks = {"sector": None, "financing_purpose": None, "company_age_years": None,
              "down_payment": None, "previous_net_income": None, "caf": None,
              "revenue": None, "net_income": None, "requested_amount": None}
    needed = {
        "repayment_capacity": {"caf": 200_000, "requested_amount": 150_000},
        "income_trend": {"net_income": 140_000, "previous_net_income": 110_000},
        "total_debt": {"revenue": 1_200_000, "requested_amount": 150_000},
        "profitability": {"revenue": 1_200_000, "net_income": 140_000},
        "company_age": {"company_age_years": 12},
        "down_payment": {"down_payment": 45_000, "requested_amount": 150_000},
        "sector": {"sector": "Services"},
        "purpose": {"financing_purpose": "Local commercial"},
    }
    data = pro(**blanks)
    for key in keys:
        data.update(needed[key])
    return data


def test_completeness_at_the_floor_is_still_reliable():
    """The floor is inclusive: exactly COMPLETENESS_FLOOR passes."""
    result = compute_score(gradable_only(
        "repayment_capacity", "income_trend", "total_debt", "profitability"))

    assert result["completude"] == scoring.COMPLETENESS_FLOOR
    assert result["fiabilite"] == "suffisante"
    assert result["recommandation"] == "accord de principe"


def test_below_the_floor_the_score_stops_carrying_the_decision():
    """The file still scores, but the routing asks for documents instead.

    The risk level keeps reading the score: the two are different statements,
    one about the file, one about the data behind it.
    """
    result = compute_score(gradable_only(
        "repayment_capacity", "total_debt", "company_age", "sector", "purpose"))

    assert result["completude"] < scoring.COMPLETENESS_FLOOR
    assert result["fiabilite"] == "insuffisante"
    assert result["recommandation"] == "demande de pièces complémentaires"
    assert result["score"] is not None
    assert result["niveau_risque"] == "faible"


# --- Out of scope ------------------------------------------------------------

@pytest.mark.parametrize("loan_type", ["immobilier", "consommation"])
def test_non_business_requests_are_refused_not_scored(loan_type):
    result = compute_score(pro(loan_type=loan_type))

    assert result["score"] is None
    assert result["niveau_risque"] == "non évalué"
    assert result["recommandation"] == "traitement manuel : grille non applicable"
    assert result["fiabilite"] == "non applicable"
    assert result["detail_criteres"] == []
    assert loan_type in result["alertes"][0]


@pytest.mark.parametrize("loan_type", scoring.SUPPORTED_LOAN_TYPES)
def test_supported_loan_types_are_scored(loan_type):
    """An unstated type is assumed to be a business request, and graded."""
    assert compute_score(pro(loan_type=loan_type))["score"] is not None


def test_out_of_scope_result_carries_the_same_keys_as_a_scored_one():
    """The Streamlit view reads both paths: neither may be missing a key."""
    assert set(compute_score(pro(loan_type="immobilier"))) == set(compute_score(pro()))


def test_out_of_scope_reports_no_missing_data():
    """Nothing was graded, so nothing is reported as missing for the grid."""
    assert compute_score(pro(loan_type="immobilier"))["donnees_manquantes"] == []


# --- Existing debt -----------------------------------------------------------

def test_existing_debt_is_amortized_into_the_debt_service():
    principal, outstanding = 150_000, 80_000
    expected = (scoring.annual_payment(principal)
                + scoring.annual_payment(outstanding,
                                         years=scoring.EXISTING_DEBT_YEARS))

    service = scoring.debt_service({"requested_amount": principal,
                                    "existing_debt": outstanding})

    assert service == pytest.approx(expected)


def test_a_stated_instalment_replaces_the_amortization_estimate():
    """When the real annuity is known, the residual-duration guess is dropped."""
    service = scoring.debt_service({"requested_amount": 150_000,
                                    "existing_debt": 80_000,
                                    "existing_debt_annual_payment": 12_000})

    assert service == pytest.approx(scoring.annual_payment(150_000) + 12_000)


def test_existing_debt_weighs_on_both_capacity_and_leverage():
    without = compute_score(pro())
    with_debt = compute_score(pro(existing_debt=600_000,
                                  existing_debt_annual_payment=None))

    assert note_of(with_debt, "repayment_capacity") > note_of(without, "repayment_capacity")
    assert note_of(with_debt, "total_debt") > note_of(without, "total_debt")
    assert with_debt["score"] < without["score"]


def test_a_zero_outstanding_adds_nothing():
    """0 is a real answer here, and costs nothing in debt service."""
    alone = scoring.debt_service({"requested_amount": 150_000})

    assert scoring.debt_service({"requested_amount": 150_000,
                                 "existing_debt": 0}) == pytest.approx(alone)


def test_debt_service_needs_an_amount():
    assert scoring.debt_service({"existing_debt": 80_000}) is None


def test_a_zero_duration_falls_back_to_the_default_term():
    assert scoring.debt_service(
        {"requested_amount": 150_000, "loan_duration_years": 0}
    ) == pytest.approx(scoring.annual_payment(150_000))


def test_annual_payment_covers_more_than_the_principal():
    """A fixed-rate amortizing loan repays capital plus interest."""
    total = scoring.annual_payment(100_000, years=5, rate=0.05) * 5

    assert total > 100_000
    assert scoring.annual_payment(100_000, years=4) > scoring.annual_payment(100_000, years=8)


# --- Threshold edges ---------------------------------------------------------
# _grade reads its bounds as "strictly below this value, this note", so a
# value sitting exactly on a bound falls into the next tier down.

@pytest.mark.parametrize("value, expected", [
    (0.49, 0), (0.50, 1),    # exactly half the CAF is already the second tier
    (0.74, 1), (0.75, 2),
    (0.99, 2), (1.00, 3),    # debt service equal to cash flow: worst note
])
def test_debt_service_bounds(value, expected):
    assert scoring._grade(value, scoring.THRESHOLDS["debt_service"]) == expected


@pytest.mark.parametrize("value, expected", [
    (-0.50, 3), (-0.40, 2),
    (-0.21, 2), (-0.20, 1),
    (-0.01, 1), (0.0, 0),    # a flat result is graded as favourable
    (0.30, 0),
])
def test_income_trend_bounds(value, expected):
    assert scoring._grade(value, scoring.THRESHOLDS["income_trend"],
                          default_note=0) == expected


@pytest.mark.parametrize("value, expected", [
    (0.24, 0), (0.25, 1),
    (0.44, 1), (0.45, 2),
    (0.64, 2), (0.65, 3),
])
def test_total_debt_bounds(value, expected):
    assert scoring._grade(value, scoring.THRESHOLDS["total_debt"]) == expected


@pytest.mark.parametrize("value, expected", [
    (0.0, 3), (0.01, 2),
    (0.029, 2), (0.03, 1),
    (0.059, 1), (0.06, 0),
])
def test_profitability_bounds(value, expected):
    assert scoring._grade(value, scoring.THRESHOLDS["profitability"],
                          default_note=0) == expected


@pytest.mark.parametrize("value, expected", [
    (0.9, 3), (1.0, 2),
    (2.9, 2), (3.0, 1),
    (4.9, 1), (5.0, 0),
])
def test_company_age_bounds(value, expected):
    assert scoring._grade(value, scoring.THRESHOLDS["company_age"],
                          default_note=0) == expected


@pytest.mark.parametrize("value, expected", [
    (0.0, 2),       # no down payment at all
    (0.0999, 2),    # under 10%
    (0.10, 1),      # 10% opens the middle tier
    (0.1999, 1),
    (0.20, 0),      # 20% is the bar: fully favourable from here on
    (0.50, 0),
])
def test_down_payment_bounds(value, expected):
    """Three tiers, and this table runs the other way: higher is better."""
    assert scoring._grade(value, scoring.THRESHOLDS["down_payment"],
                          default_note=0) == expected


@pytest.mark.parametrize("down_payment, expected", [
    (0, 2),
    (14_985, 2),     # 9.99% of 150 000
    (15_000, 1),     # exactly 10%
    (29_985, 1),     # 19.99%
    (30_000, 0),     # exactly 20%
    (60_000, 0),     # well over 20%
])
def test_down_payment_tiers_through_the_engine(down_payment, expected):
    """The same three tiers, read end to end off a scored request."""
    result = compute_score(pro(requested_amount=150_000,
                               down_payment=down_payment))

    assert note_of(result, "down_payment") == expected


@pytest.mark.parametrize("down_payment, note, shown", [
    (14_985, 2, "9%"),      # 9.99%: graded under 10%, and never shown as 10%
    (15_000, 1, "10%"),     # exactly 10%
    (29_985, 1, "19%"),
    (30_000, 0, "20%"),
    (45_000, 0, "30%"),
])
def test_the_displayed_ratio_never_rounds_up_across_a_threshold(
        down_payment, note, shown):
    """A file graded as under 10% must not print "10%" on the sheet."""
    result = compute_score(pro(requested_amount=150_000,
                               down_payment=down_payment))
    detail = next(c for c in result["detail_criteres"]
                  if c["critere"] == scoring.CRITERIA["down_payment"][0])

    assert detail["note"] == note
    assert detail["valeur"] == shown
    assert shown in detail["phrase"]


def test_floor_pct_rounds_down_and_drops_a_trailing_zero():
    assert scoring._floor_pct(0.0999) == "9%"
    assert scoring._floor_pct(0.10) == "10%"
    assert scoring._floor_pct(0.0) == "0%"
    assert scoring._floor_pct(1.0) == "100%"
    assert scoring._floor_pct(0.0459, decimals=1) == "4.5%"
    assert scoring._floor_pct(0.06, decimals=1) == "6%"


def test_floor_pct_floors_the_signed_ratio():
    """Flooring the signed value is what keeps it inside its own tier.

    A drop of 19.99% shows as "20%", and both grade to the same note: the
    bounds are read as "strictly below", so landing on one does not cross it.
    """
    assert scoring._floor_pct(-0.075, decimals=1) == "-7.5%"
    assert scoring._floor_pct(-0.1999) == "-20%"
    assert scoring._grade(-0.20, scoring.THRESHOLDS["income_trend"],
                          default_note=0) == scoring._grade(
        -0.1999, scoring.THRESHOLDS["income_trend"], default_note=0)


@pytest.mark.parametrize("ratio", [0.29, 0.07, 0.58, 0.83, 0.29 / 1])
def test_floor_pct_is_not_fooled_by_binary_representation(ratio):
    """0.29 * 100 is 28.999999999999996: the floor must not print "28%"."""
    assert scoring._floor_pct(ratio) == f"{round(ratio * 100)}%"


@pytest.mark.parametrize("caf", [400_000, 60_000, 45_000, 31_000, 22_000])
def test_the_repayment_ratio_is_floored_onto_its_own_tier(caf):
    """Spans every tier of the debt-service table as the CAF shrinks."""
    data = pro(caf=caf)
    result = compute_score(data)
    detail = next(c for c in result["detail_criteres"]
                  if c["critere"] == scoring.CRITERIA["repayment_capacity"][0])
    ratio = scoring.debt_service(data) / caf

    assert detail["valeur"] == f"{scoring._floor_pct(ratio)} de la CAF"
    assert float(detail["valeur"].split("%")[0]) / 100 <= ratio
    assert detail["note"] == scoring._grade(
        float(detail["valeur"].split("%")[0]) / 100, scoring.THRESHOLDS["debt_service"])


@pytest.mark.parametrize("debt, note, shown", [
    (249_000, 0, "24%"),   # under 25% of revenue, never shown as "25%"
    (250_000, 1, "25%"),
    (449_000, 1, "44%"),
    (650_000, 3, "65%"),
])
def test_the_leverage_ratio_never_rounds_up_across_a_threshold(debt, note, shown):
    result = compute_score(pro(revenue=1_000_000, requested_amount=debt))
    detail = next(c for c in result["detail_criteres"]
                  if c["critere"] == scoring.CRITERIA["total_debt"][0])

    assert detail["note"] == note
    assert detail["valeur"] == f"{shown} du CA"
    assert shown in detail["phrase"]


@pytest.mark.parametrize("net, note, shown", [
    (-75_000, 3, "-7.5%"),
    (9_900, 3, "0.9%"),    # under 1% margin, never shown as "1.0%"
    (10_000, 2, "1%"),
    (29_900, 2, "2.9%"),
    (60_000, 0, "6%"),
])
def test_the_margin_never_rounds_up_across_a_threshold(net, note, shown):
    result = compute_score(pro(revenue=1_000_000, net_income=net, caf=500_000))
    detail = next(c for c in result["detail_criteres"]
                  if c["critere"] == scoring.CRITERIA["profitability"][0])

    assert detail["note"] == note
    assert detail["valeur"] == shown
    assert shown in detail["phrase"]


@pytest.mark.parametrize("net, previous, note, shown, wording", [
    (81_000, 100_000, 1, "-19%", "Résultat net en baisse de 19%"),
    (80_000, 100_000, 1, "-20%", "Résultat net en baisse de 20%"),
    (79_000, 100_000, 2, "-21%", "Résultat net en baisse de 21%"),
    (127_000, 100_000, 0, "+27%", "Résultat net en progression de 27%"),
    # A decimal only where a whole point would overstate the drop.
    (20_000, 22_000, 1, "-9.1%", "Résultat net en baisse de 9.1%"),
    (100_500, 100_000, 0, "+0.5%", "Résultat net en progression de 0.5%"),
])
def test_the_income_trend_keeps_its_sign_and_rounds_down(
        net, previous, note, shown, wording):
    result = compute_score(pro(net_income=net, previous_net_income=previous))
    detail = next(c for c in result["detail_criteres"]
                  if c["critere"] == scoring.CRITERIA["income_trend"][0])

    assert detail["note"] == note
    assert detail["valeur"] == shown
    assert detail["phrase"] == wording


def test_every_displayed_ratio_grades_to_the_note_printed_next_to_it():
    """The invariant behind the flooring, checked across the whole grid.

    Re-grading the figure the sheet shows must give back the note the sheet
    shows: no criterion may print a percentage that reads as another tier.
    """
    tables = {
        "repayment_capacity": ("debt_service", 3),
        "total_debt": ("total_debt", 3),
        "profitability": ("profitability", 0),
        "down_payment": ("down_payment", 0),
        "income_trend": ("income_trend", 0),
    }
    for amount in range(1_000, 400_000, 1_723):
        result = compute_score(pro(requested_amount=amount, down_payment=amount // 7,
                                   caf=None, net_income=amount // 9,
                                   previous_net_income=40_000, revenue=900_000))
        for key, (table, default) in tables.items():
            detail = next(c for c in result["detail_criteres"]
                          if c["critere"] == scoring.CRITERIA[key][0])
            if detail["note"] is None:
                continue
            shown = float(detail["valeur"].split("%")[0].split()[0]) / 100
            assert scoring._grade(shown, scoring.THRESHOLDS[table],
                                  default_note=default) == detail["note"], (
                key, detail["valeur"], detail["note"])


def test_down_payment_note_only_improves_with_the_ratio():
    """No tier is skipped and none is unreachable between 0 and 100%."""
    seen = [scoring._grade(r / 100, scoring.THRESHOLDS["down_payment"],
                           default_note=0) for r in range(0, 101)]

    assert set(seen) == {0, 1, 2}
    assert seen == sorted(seen, reverse=True)


@pytest.mark.parametrize("score, level", [
    (100, "faible"), (75, "faible"), (74, "modéré"),
    (60, "modéré"), (59, "élevé"),
    (40, "élevé"), (39, "très élevé"), (0, "très élevé"),
])
def test_decision_band_edges(score, level):
    """Bands are inclusive on their floor."""
    assert scoring._decide(score)[0] == level


def test_no_score_routes_to_a_request_for_documents():
    assert scoring._decide(None) == ("indéterminé", "demande de pièces complémentaires")


# --- Cash flow ---------------------------------------------------------------

def test_caf_wins_over_ebitda_which_wins_over_the_proxy():
    assert scoring.cash_flow({"caf": 100, "ebitda": 200, "net_income": 300}) == (100, False)
    assert scoring.cash_flow({"ebitda": 200, "net_income": 300}) == (200, False)


def test_net_income_is_scaled_up_and_flagged_as_a_proxy():
    value, is_proxy = scoring.cash_flow({"net_income": 300})

    assert value == pytest.approx(300 * scoring.CASH_FLOW_PROXY_FACTOR)
    assert is_proxy is True


def test_no_cash_flow_figure_is_not_a_proxy():
    assert scoring.cash_flow({}) == (None, False)


# --- Plausibility ------------------------------------------------------------

def test_out_of_range_values_are_flagged_as_extraction_errors():
    """A revenue of 420 instead of 420 000 must not be scored as if real."""
    issues = scoring.check_plausibility({"revenue": 420, "requested_amount": 500})

    assert any("revenue" in issue for issue in issues)
    assert any("requested_amount" in issue for issue in issues)


def test_internally_inconsistent_figures_are_flagged():
    assert scoring.check_plausibility(
        {"revenue": 100_000, "net_income": 150_000}
    ) == ["résultat net supérieur au chiffre d'affaires"]

    assert scoring.check_plausibility(
        {"requested_amount": 50_000, "down_payment": 90_000}
    ) == ["apport supérieur au montant demandé"]


def test_a_plausible_file_raises_nothing():
    assert scoring.check_plausibility(pro()) == []


def test_a_doubtful_figure_downgrades_reliability_not_the_score():
    """The score still stands; it is the data behind it that is questioned."""
    result = compute_score(pro(revenue=420))

    assert result["fiabilite"] == "insuffisante"
    assert result["recommandation"] == "demande de pièces complémentaires"
    assert result["score"] is not None
    assert any("Donnée douteuse" in alert for alert in result["alertes"])


# --- Lookup tables -----------------------------------------------------------

def test_lookup_matches_a_keyword_inside_a_free_text_label():
    assert scoring._lookup("Bâtiment et travaux publics", scoring.SECTOR_RISK) == 2
    assert scoring._lookup("Achat d'un véhicule utilitaire", scoring.PURPOSE_RISK) == 1


def test_an_unlisted_label_gets_the_median_note_rather_than_being_dropped():
    """We know what is financed, we just have no coefficient: not missing."""
    assert scoring._lookup("Cryptomonnaie", scoring.SECTOR_RISK) == scoring.DEFAULT_TABLE_NOTE

    result = compute_score(pro(sector="Cryptomonnaie"))
    assert note_of(result, "sector") == scoring.DEFAULT_TABLE_NOTE
    assert result["completude"] == 100


def test_an_absent_label_is_missing_data():
    assert scoring._lookup(None, scoring.SECTOR_RISK) is None
    assert scoring._lookup("", scoring.SECTOR_RISK) is None
    assert note_of(compute_score(pro(sector=None)), "sector") is None


def test_purpose_wording_is_fixed_by_the_note():
    """The phrasing is written in code so it stays stable across runs."""
    result = compute_score(pro(financing_purpose="Trésorerie"))
    detail = next(c for c in result["detail_criteres"]
                  if c["critere"] == scoring.CRITERIA["purpose"][0])

    assert detail["phrase"] == scoring.PURPOSE_PHRASES[detail["note"]]

def test_lookup_keeps_the_worst_matching_keyword():
    """A label matching several keywords is graded on the riskiest one."""
    assert scoring._lookup("Aménagement de mon local commercial",
                           scoring.PURPOSE_RISK) == 2
    assert scoring._lookup("Commerce de matériaux de bâtiment",
                           scoring.SECTOR_RISK) == 2

# --- Determinism -------------------------------------------------------------

def test_the_same_input_always_gives_the_same_output():
    assert compute_score(pro()) == compute_score(pro())


def test_scoring_does_not_mutate_the_extracted_data():
    data = pro()
    before = dict(data)

    compute_score(data)

    assert data == before

def test_injection_signals_block_the_recommendation():
    """A flagged request never gets an automatic green light."""
    data = {
        "loan_type": "pro", "sector": "Conseil", "company_age_years": 10,
        "revenue": 5_000_000, "net_income": 900_000,
        "previous_net_income": 800_000, "requested_amount": 80_000,
        "down_payment": 20_000, "financing_purpose": "Matériel",
        "injection_signals": ["Instruction détectée dans le texte"],
    }
    result = scoring.compute_score(data)
    assert result["recommandation"] == "demande de pièces complémentaires"
    assert result["fiabilite"] == "insuffisante"


def test_missing_loan_type_without_company_data_is_out_of_scope():
    """No company figures means no grid, whatever the model failed to say."""
    data = {
        "loan_type": None, "requested_amount": 280_000,
        "financing_purpose": "Résidence principale", "down_payment": 30_000,
        "existing_debt": 8_000,
    }
    assert scoring.compute_score(data)["recommandation"] == \
        "traitement manuel : grille non applicable"