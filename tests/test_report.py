"""Tests for the deterministic half of the summary sheet.

build_highlights() is the part of report.py that runs without the model: it
classifies and orders what the sheet says about each criterion. The two
free-text sections are the model's job and are not tested here.
"""

import pytest

import report
from report import build_highlights
from scoring import compute_score


# --- Helpers -----------------------------------------------------------------

def criterion(note, phrase="Critère", poids=10):
    """One row of detail_criteres, as the engine emits it."""
    return {"critere": phrase, "valeur": "—", "phrase": phrase,
            "note": note, "poids": poids}


def analysis(*criteria, donnees_manquantes=()):
    return {"detail_criteres": list(criteria),
            "donnees_manquantes": list(donnees_manquantes)}


# --- The three bands ---------------------------------------------------------

def test_a_favourable_criterion_is_a_strength():
    strengths, vigilance = build_highlights(analysis(criterion(0, "Favorable")))

    assert strengths == ["Favorable"]
    assert vigilance == []


@pytest.mark.parametrize("note", [2, 3])
def test_an_unfavourable_criterion_is_a_vigilance_point(note):
    strengths, vigilance = build_highlights(analysis(criterion(note, "Défavorable")))

    assert strengths == []
    assert vigilance == ["Défavorable"]


def test_a_merely_correct_criterion_carries_neither_list():
    """A note of 1 is not an argument for the file, nor one against it.

    It stays in the criteria table, which is where a reader can find it.
    """
    strengths, vigilance = build_highlights(analysis(criterion(1, "Correct")))

    assert strengths == []
    assert vigilance == []


def test_the_bands_cover_every_note_exactly_once():
    strengths, vigilance = build_highlights(analysis(
        criterion(0, "note 0"), criterion(1, "note 1"),
        criterion(2, "note 2"), criterion(3, "note 3"),
    ))

    assert strengths == ["note 0"]
    assert vigilance == ["note 3", "note 2"]
    assert "note 1" not in strengths + vigilance


# --- Ordering and truncation -------------------------------------------------

def test_vigilance_puts_the_worst_criteria_first_then_the_heaviest():
    strengths, vigilance = build_highlights(analysis(
        criterion(2, "moyen léger", poids=5),
        criterion(3, "grave", poids=5),
        criterion(2, "moyen lourd", poids=30),
    ))

    assert vigilance == ["grave", "moyen lourd", "moyen léger"]


def test_missing_data_comes_last_in_the_vigilance_list():
    """An absent figure is a risk, but a graded criterion outranks it."""
    _, vigilance = build_highlights(analysis(
        criterion(3, "grave"), donnees_manquantes=["Chiffre d'affaires non communiqué"]))

    assert vigilance == ["grave", "Chiffre d'affaires non communiqué"]


def test_an_ungraded_criterion_is_not_worded_at_all():
    """No phrase means the criterion could not be computed: nothing to say."""
    row = criterion(None, "jamais affiché")
    row["phrase"] = None

    assert build_highlights(analysis(row)) == ([], [])


def test_both_lists_are_capped():
    result = analysis(
        *[criterion(0, f"fort {i}") for i in range(10)],
        *[criterion(3, f"faible {i}") for i in range(10)],
        donnees_manquantes=[f"manquant {i}" for i in range(10)],
    )
    strengths, vigilance = build_highlights(result)

    assert len(strengths) == report.MAX_STRENGTHS
    assert len(vigilance) == report.MAX_VIGILANCE


# --- Read off a real scored request ------------------------------------------

def test_a_middling_down_payment_is_sold_as_neither():
    """10% of the amount clears the floor without reaching the 20% bar.

    The file is built so that only two criteria are gradable, well under the
    truncation cap: the apport is absent from both lists because of its band,
    not because it was cut.
    """
    result = compute_score({
        "loan_type": "pro",
        "revenue": 1_200_000,
        "requested_amount": 150_000,
        "loan_duration_years": 7,
        "down_payment": 15_000,
        "existing_debt": 0,
        "existing_debt_annual_payment": 0,
    })
    apport = next(c for c in result["detail_criteres"] if c["critere"] == "Apport")
    strengths, vigilance = build_highlights(result)

    assert apport["note"] == 1
    assert apport["phrase"] == "Apport de 10% du montant demandé"
    assert len(strengths) < report.MAX_STRENGTHS
    assert apport["phrase"] not in strengths
    assert apport["phrase"] not in vigilance


def test_a_down_payment_above_the_bar_is_a_strength():
    result = compute_score({
        "loan_type": "pro",
        "revenue": 1_200_000,
        "requested_amount": 150_000,
        "loan_duration_years": 7,
        "down_payment": 30_000,
        "existing_debt": 0,
        "existing_debt_annual_payment": 0,
    })
    apport = next(c for c in result["detail_criteres"] if c["critere"] == "Apport")

    assert apport["note"] == 0
    assert apport["phrase"] in build_highlights(result)[0]


def test_no_down_payment_is_a_vigilance_point():
    result = compute_score({
        "loan_type": "pro",
        "revenue": 1_200_000,
        "requested_amount": 150_000,
        "loan_duration_years": 7,
        "down_payment": 0,
        "existing_debt": 0,
        "existing_debt_annual_payment": 0,
    })
    apport = next(c for c in result["detail_criteres"] if c["critere"] == "Apport")

    assert apport["note"] == 2
    assert apport["phrase"] == "Aucun apport"
    assert apport["phrase"] in build_highlights(result)[1]


def test_an_unscored_request_has_nothing_to_highlight():
    assert build_highlights(compute_score({"loan_type": "immobilier"})) == ([], [])
