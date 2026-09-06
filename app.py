"""Streamlit front-end for the credit request analysis tool.

Pipeline: extract() reads the free-form request, compute_score() grades it,
write_report() words the sheet. The two model calls are the slow part, so the
extracted data is kept in session state: correcting a value and clicking
"Recalculer" replays the scoring and the sheet only.
"""

import json
from contextlib import nullcontext
from html import escape

import streamlit as st

import scoring
from extraction import ExtractionError, extract
from report import ReportError, write_report
from scoring import compute_score

# --- Visual identity ---------------------------------------------------------
#
# One style sheet, injected once, right after set_page_config(). Nothing else
# in this file writes CSS.
#
# Rules of the design: an off-white ground, slate grey text, and a single
# deep blue accent reserved for section titles and interactive elements. The
# blue never carries a business meaning.
#
# Meaning is carried by three semantic bands -- green, amber, red -- each
# declared once below as a set of four tones: a saturated graphic tone for
# fills (ring, rule, dot), a darker ink tone for text on a pale ground, a
# hairline, and a very pale wash for card backgrounds. Nothing outside this
# block holds a colour value.
#
# Where the bands are used, and nowhere else: the score card (the most
# coloured element of the page), the strengths and vigilance cards, the
# criteria dots, the alerts box, and the missing fields of the form.
#
# Hierarchy is fixed and has three ranks: the score figure, then the
# recommendation on its own line, then everything else at body size. Nothing
# outside the score card is allowed into the first two ranks.
#
# Rhythm is three gaps -- inside a block, between blocks, between sections --
# and air always grows outwards, so blocks group by their own spacing.
#
# Prose stops at a reading measure; only the cards and the criteria table use
# the full page width.
#
# Cards -- highlights, assumptions, alerts, empty and waiting states -- are
# all the same object: tinted ground, hairline, discreet radius, coloured rule
# down the left edge. No shadows.
#
# The palette mirrors .streamlit/config.toml. Sizes and spacings come from the
# scales below and nowhere else.

CSS = """
<style>
:root {
    /* Forces light form controls even when the OS is in dark mode. */
    color-scheme: light;

    /* Palette. Mirrors .streamlit/config.toml. */
    --c-bg: #F7F7F4;
    --c-surface: #FFFFFF;
    --c-border: #E3E3DD;
    --c-text: #33404D;
    --c-muted: #6C7784;
    --c-accent: #1B3B6F;
    --c-track: #E7E7E1;
    --c-neutral-pale: #F1F1ED;

    /* Semantic bands. Four tones each: graphic (fills), ink (text on the
       pale wash, dark enough to stay readable), line (hairline), pale
       (background wash). These carry meaning and never decorate. */
    --c-good: #3F8A5F;
    --c-good-ink: #2E6042;
    --c-good-line: #C3DBCB;
    --c-good-pale: #EFF6F1;
    /* One step below favourable: the "correct" appraisal of a criterion. */
    --c-good-mid: #86B79A;

    --c-watch: #C9871B;
    --c-watch-ink: #8A5C10;
    --c-watch-line: #EDD9A9;
    --c-watch-pale: #FDF6E7;

    --c-bad: #B3392A;
    --c-bad-ink: #8E2C20;
    --c-bad-line: #EBC6C1;
    --c-bad-pale: #FBEFEC;

    /* One family, two weights. */
    --font: "Inter", "Segoe UI", system-ui, -apple-system, "Helvetica Neue", Arial, sans-serif;
    --w-regular: 400;
    --w-bold: 600;

    /* Three text sizes. The two emphatic sizes -- the score figure and the
       recommendation -- are derived from the scale, never picked by hand. */
    --fs-sm: 0.8125rem;
    --fs-base: 0.9375rem;
    --fs-lg: 1.125rem;
    --fs-reco: calc(var(--fs-lg) * 1.25);
    --fs-score: calc(var(--fs-lg) * 3);

    /* One spacing scale, 4px based. No arbitrary margins anywhere. */
    --sp-1: 4px;
    --sp-2: 8px;
    --sp-3: 16px;
    --sp-4: 24px;
    --sp-5: 32px;
    --sp-6: 48px;

    /* Rhythm: three values, all drawn from the scale above. Air grows
       outwards -- tight inside a block, wider between blocks, widest between
       sections -- and no gap on the page comes from anywhere else. */
    --gap-tight: var(--sp-2);
    --gap-block: var(--sp-3);
    --gap-section: var(--sp-6);

    /* Reading measure. Prose stops here. The page stays wide for the cards
       and the criteria table, which need the room; sentences do not. */
    --measure: 66ch;

    /* Component geometry. */
    --radius: 6px;
    --rule: 1px;
    --liseret: 3px;
    --ring-size: 148px;
    --ring-hole: 112px;
    --dot: 9px;
}

/* --- Base ---------------------------------------------------------------- */

html, body, .stApp, .stApp button, .stApp input, .stApp textarea,
.stApp select { font-family: var(--font); }

.stApp { background: var(--c-bg); color: var(--c-text); }

.stMainBlockContainer, .block-container {
    max-width: 1120px;
    padding-top: calc(var(--sp-5) * 3);  /* clears the fixed app header */
    padding-bottom: calc(var(--sp-5) * 2);
}

.stApp p, .stApp li, .stApp label, .stApp div { font-size: var(--fs-base); }

.stApp h1 {
    font-size: var(--fs-lg);
    font-weight: var(--w-bold);
    color: var(--c-accent);
    letter-spacing: 0.01em;
    padding: 0;
    margin: 0 0 var(--sp-2) 0;
}

.stApp h2, .stApp h3, .stApp h4 {
    font-size: var(--fs-base);
    font-weight: var(--w-bold);
    color: var(--c-text);
    padding: 0;
    margin: 0;
}

.stApp hr { margin: var(--gap-section) 0; border-color: var(--c-border); }

.stApp [data-testid="stCaptionContainer"], .stApp .stCaption {
    font-size: var(--fs-sm);
    color: var(--c-muted);
}

/* Section title: the one place the accent colour carries meaning. */
.stApp p.section-title {
    font-size: var(--fs-sm);
    font-weight: var(--w-bold);
    color: var(--c-accent);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    /* Padding, not margin: Streamlit's flex blocks collapse the margin. */
    padding-top: var(--gap-section);
    margin: 0 0 var(--gap-block) 0;
}

/* Prose is capped at the reading measure: a three-sentence summary must not
   be dragged across the full width of the page. */
.prose {
    margin: 0 0 var(--gap-block) 0;
    max-width: var(--measure);
    line-height: 1.6;
}

/* --- Lists --------------------------------------------------------------- */

ul.list { margin: 0; padding-left: var(--sp-3); }
ul.list li { margin-bottom: var(--gap-tight); line-height: 1.5; max-width: var(--measure); }
ul.list li:last-child { margin-bottom: 0; }
.stApp p.empty { margin: 0; font-size: var(--fs-sm); color: var(--c-muted); }

/* --- Cards --------------------------------------------------------------- */

.card {
    background: var(--c-surface);
    border: var(--rule) solid var(--c-border);
    border-radius: var(--radius);
    padding: var(--sp-4);
    height: 100%;
}

/* Band classes. Set on any element that carries a meaning, they bind the
   four tones of one band to generic names so the components below never
   name a colour. Applied by score_band() and by the highlight cards. */
.band--good { --band: var(--c-good); --band-ink: var(--c-good-ink);
              --band-line: var(--c-good-line); --band-pale: var(--c-good-pale); }
.band--watch { --band: var(--c-watch); --band-ink: var(--c-watch-ink);
               --band-line: var(--c-watch-line); --band-pale: var(--c-watch-pale); }
.band--bad { --band: var(--c-bad); --band-ink: var(--c-bad-ink);
             --band-line: var(--c-bad-line); --band-pale: var(--c-bad-pale); }
/* No band: an unscored file must render as plainly as a scored one. */
.band--none { --band: var(--c-muted); --band-ink: var(--c-text);
              --band-line: var(--c-border); --band-pale: var(--c-surface); }

/* Left rule and pale wash, semantic. Green for strengths, amber for
   vigilance points. */
.card--band {
    border-left: var(--liseret) solid var(--band);
    border-color: var(--band-line);
    border-left-color: var(--band);
    background: var(--band-pale);
}

.stApp p.card-title {
    font-size: var(--fs-sm);
    font-weight: var(--w-bold);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin: 0 0 var(--gap-block) 0;
}
.card--band .card-title { color: var(--band-ink); }

/* Side-by-side cards, equal height, stacked on a narrow screen. */
.grid-2 {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
    gap: var(--gap-block);
}

/* --- Score card ---------------------------------------------------------- */

/* The score is the most coloured -- and the largest -- element of the page:
   its band tints the figure, the ring, the recommendation and the card
   itself. Everything below it is deliberately quieter. */
.score-card {
    display: flex;
    align-items: center;
    gap: var(--sp-5);
    padding: var(--sp-5);
    background: var(--band-pale);
    border-color: var(--band-line);
    border-left: var(--liseret) solid var(--band);
}

.ring {
    flex: 0 0 auto;
    width: var(--ring-size);
    height: var(--ring-size);
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    background: conic-gradient(
        var(--band) calc(var(--ring-pct) * 3.6deg),
        var(--c-track) 0
    );
}

.ring-inner {
    width: var(--ring-hole);
    height: var(--ring-hole);
    border-radius: 50%;
    background: var(--c-surface);
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
}

.ring-value {
    font-size: var(--fs-score);
    font-weight: var(--w-bold);
    line-height: 1;
    letter-spacing: -0.02em;
    color: var(--band);
}

.ring-max { font-size: var(--fs-sm); color: var(--c-muted); }

.score-meta { flex: 1 1 auto; min-width: 0; }

/* Second in the reading order and second in size: the recommendation gets a
   line of its own above the metadata rows, so a one-second glance at the
   page lands on the figure and then on this, and on nothing else. */
.stApp p.score-reco {
    font-size: var(--fs-reco);
    font-weight: var(--w-bold);
    line-height: 1.25;
    color: var(--band-ink);
    max-width: var(--measure);
    margin: 0 0 var(--sp-4) 0;
}

/* Supporting figures, third rank: small labels, no colour of their own. */
.meta { margin: 0 0 var(--gap-block) 0; }
.meta > div {
    display: flex;
    gap: var(--sp-3);
    padding: var(--sp-2) 0;
    border-bottom: var(--rule) solid var(--band-line);
}
.meta > div:last-child { border-bottom: 0; }
.meta dt { flex: 0 0 200px; color: var(--c-muted); margin: 0; }
.meta dd { margin: 0; }
/* The risk level is the wording of the score: same band, same colour. */
.meta dd.band { font-weight: var(--w-bold); color: var(--band-ink); }

/* Completeness bar: neutral grey on purpose. It measures how much of the
   analysis could be run, which is neither good nor bad news, so it takes no
   band -- and no accent blue either, which carries no meaning here. Kept to
   a hairline: it belongs to the third rank, and a full bar at 100% must not
   pull the eye away from the figure and the recommendation. */
.meter {
    height: calc(var(--sp-1) / 2);
    border-radius: var(--radius);
    background: var(--c-surface);
    overflow: hidden;
}
.meter span { display: block; height: 100%; background: var(--c-muted); }

/* Eyebrow: it says what the figure is, so it sits above it, small. */
.stApp p.score-caption {
    font-size: var(--fs-sm);
    color: var(--c-muted);
    letter-spacing: 0.02em;
    margin: 0 0 var(--sp-1) 0;
}

/* --- Criteria table ------------------------------------------------------ */

.table-wrap { overflow-x: auto; }

table.criteria {
    width: 100%;
    border-collapse: collapse;
    font-size: var(--fs-base);
}
table.criteria th {
    border: 0;
    text-align: left;
    font-size: var(--fs-sm);
    font-weight: var(--w-bold);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--c-muted);
    padding: 0 var(--sp-3) var(--sp-2) var(--sp-2);
    border-bottom: var(--rule) solid var(--c-border);
    white-space: nowrap;
}
table.criteria td {
    border: 0;
    padding: var(--sp-2) var(--sp-3) var(--sp-2) var(--sp-2);
    vertical-align: middle;
}
/* Alternating rows instead of eight rules across the table: the wash is the
   palest neutral in the palette, enough to guide the eye down a column and
   not enough to be read as a state. */
table.criteria tbody tr:nth-child(even) { background: var(--c-neutral-pale); }
table.criteria th:last-child, table.criteria td:last-child { padding-right: var(--sp-2); }
/* Figures right, so notes and weights line up as two straight columns. */
table.criteria .num { text-align: right; white-space: nowrap; }

/* One dot per appraisal: green favourable, pale green correct, amber
   unfavourable, red very unfavourable. Dot and note travel as one
   right-aligned unit, and the note reserves a fixed width, so the dots stand
   in a straight column whatever glyph the note is -- a digit or a dash. */
.appraisal-inner {
    display: inline-flex;
    align-items: center;
    gap: var(--sp-2);
}
.appraisal-inner .note { min-width: 1em; text-align: right; }

.dot {
    flex: 0 0 auto;
    display: inline-block;
    width: var(--dot);
    height: var(--dot);
    border-radius: 50%;
    background: var(--c-track);
}
.dot--good { background: var(--c-good); }
.dot--fair { background: var(--c-good-mid); }
.dot--watch { background: var(--c-watch); }
.dot--bad { background: var(--c-bad); }

/* --- Boxes --------------------------------------------------------------- */

/* Assumptions and alerts are cards too, and are built from the same parts as
   the ones above: tinted ground, hairline, discreet radius, coloured rule
   down the left edge. No shadow anywhere on the page. */
.note-box, .alert-box {
    border: var(--rule) solid var(--c-border);
    border-radius: var(--radius);
    padding: var(--sp-3) var(--sp-4);
    margin-bottom: var(--gap-block);
}

/* Assumptions: a calculation choice, not a problem. A neutral grey ground
   and the institutional blue rule -- no semantic band, so it cannot be
   mistaken for an alert at a glance. */
.note-box {
    background: var(--c-neutral-pale);
    border-left: var(--liseret) solid var(--c-accent);
}

/* Alerts: a problem to raise. Red ground, red rule. */
.alert-box {
    background: var(--c-bad-pale);
    border-color: var(--c-bad-line);
    border-left: var(--liseret) solid var(--c-bad);
}

.stApp p.box-title {
    font-size: var(--fs-sm);
    font-weight: var(--w-bold);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin: 0 0 var(--gap-tight) 0;
    color: var(--c-muted);
}
.alert-box .box-title { color: var(--c-bad-ink); }

/* --- Empty and waiting states -------------------------------------------- */

/* The page before an analysis, and the page during one, are built from the
   same parts as the cards: same ground, same hairline, same radius, same
   blue rule as the assumptions box. Neither state is allowed to read as a
   gap in the design. */
.empty-state, .stApp [data-testid="stSpinner"] {
    background: var(--c-surface);
    border: var(--rule) solid var(--c-border);
    border-left: var(--liseret) solid var(--c-accent);
    border-radius: var(--radius);
    padding: var(--sp-3) var(--sp-4);
    margin-top: var(--gap-block);
}
.stApp p.empty-state-text {
    margin: 0;
    color: var(--c-muted);
    max-width: var(--measure);
}
/* Streamlit sizes the spinner to its text; the panel spans the column so the
   waiting state occupies exactly the slot the empty state just left. */
.stApp [data-testid="stSpinner"] { width: 100%; box-sizing: border-box; }
.stApp [data-testid="stSpinner"] > div { gap: var(--sp-3); align-items: center; }
.stApp [data-testid="stSpinner"] p,
.stApp [data-testid="stSpinner"] > div > div {
    font-size: var(--fs-sm);
    color: var(--c-muted);
    margin: 0;
}

/* --- Streamlit widgets --------------------------------------------------- */

.stApp [data-testid="stForm"] {
    background: var(--c-surface);
    border: var(--rule) solid var(--c-border);
    border-radius: var(--radius);
    padding: var(--sp-4);
}

.stApp [data-testid="stExpander"] details {
    background: var(--c-surface);
    border: var(--rule) solid var(--c-border);
    border-radius: var(--radius);
}
.stApp [data-testid="stExpander"] { margin-bottom: var(--gap-block); }
/* Folded sections are secondary controls: same weight and colour as the
   section titles, one size below the body text. */
.stApp [data-testid="stExpander"] summary {
    font-weight: var(--w-bold);
    color: var(--c-accent);
}
.stApp [data-testid="stExpander"] summary p { font-size: var(--fs-sm); }

/* Column headings inside the folded sections ("Demande", "Entreprise",
   "Poids des critères"): written as bold markdown, read as eyebrows, so they
   sit in the same register as the section titles above. */
.stApp [data-testid="stExpander"] [data-testid="stMarkdownContainer"] p:has(> strong:only-child) {
    font-size: var(--fs-sm);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--c-accent);
    margin: 0 0 var(--gap-tight) 0;
}

.stApp .stButton button,
.stApp .stFormSubmitButton button,
.stApp .stDownloadButton button {
    border-radius: var(--radius);
    font-weight: var(--w-bold);
    font-size: var(--fs-base);
    padding: var(--sp-2) var(--sp-4);
}

/* Secondary actions -- downloading the sheet -- stand one size and one
   weight below the two primary buttons that actually drive the page. */
.stApp .stButton button[kind="secondary"],
.stApp .stButton button[data-testid="stBaseButton-secondary"],
.stApp .stDownloadButton button {
    font-size: var(--fs-sm);
    font-weight: var(--w-regular);
    padding: var(--sp-1) var(--sp-3);
    color: var(--c-text);
    border-color: var(--c-border);
}

.stApp textarea, .stApp input, .stApp [data-baseweb="select"] > div {
    border-radius: var(--radius) !important;
    font-size: var(--fs-base) !important;
}

.stApp label p { font-size: var(--fs-sm) !important; color: var(--c-muted); }

/* Two columns of fields whose labels are not the same length: a minimum
   height per field drops every control onto the same baseline across both
   columns, so the form reads as one grid rather than two loose lists. */
.stApp [data-testid="stForm"] [data-testid="stSelectbox"],
.stApp [data-testid="stForm"] [data-testid="stTextInput"],
.stApp [data-testid="stForm"] [data-testid="stNumberInput"],
.stApp [data-testid="stForm"] .stSelectbox,
.stApp [data-testid="stForm"] .stTextInput,
.stApp [data-testid="stForm"] .stNumberInput {
    display: flex;
    flex-direction: column;
    justify-content: flex-end;
    min-height: 68px;
}
.stApp [data-testid="stForm"] [data-testid="stCaptionContainer"] {
    margin-bottom: var(--gap-block);
}

/* Missing fields: an amber wash on the control itself. A suffix on the label
   is easy to miss in a two-column form; the wash carries across the control,
   its steppers and its label, which makes the holes in the file countable
   down both columns at a glance. edit_field() wraps those widgets in a keyed
   container, which Streamlit renders as an st-key-missing-* class. */
.stApp [class*="st-key-missing-"] [data-baseweb="input"],
.stApp [class*="st-key-missing-"] [data-baseweb="base-input"],
.stApp [class*="st-key-missing-"] [data-baseweb="select"] > div,
.stApp [class*="st-key-missing-"] input,
.stApp [class*="st-key-missing-"] [data-testid="stNumberInputStepUp"],
.stApp [class*="st-key-missing-"] [data-testid="stNumberInputStepDown"] {
    background: var(--c-watch-pale) !important;
}
.stApp [class*="st-key-missing-"] [data-baseweb="input"],
.stApp [class*="st-key-missing-"] [data-baseweb="select"] > div {
    border-color: var(--c-watch) !important;
}
.stApp [class*="st-key-missing-"] label p { color: var(--c-watch-ink); }

/* Streamlit's own alerts, aligned on the palette and on the card geometry. */
.stApp [data-testid="stAlert"] {
    border-radius: var(--radius);
    font-size: var(--fs-base);
    padding: var(--sp-3) var(--sp-4);
}
</style>
"""

# Sample request, copied from the __main__ block of extraction.py.
SAMPLE_REQUEST = (
    "Bonjour, je suis gérant d'une SARL dans le secteur du bâtiment, créée "
    "il y a 3 ans. Nous réalisons un CA annuel de 420 000 € avec un résultat "
    "net de 18 000 € cette année, en baisse par rapport aux 35 000 € de "
    "l'année précédente. Je souhaite financer l'achat d'un véhicule "
    "utilitaire et de matériel pour 65 000 €. Nous avons déjà un emprunt en "
    "cours de 40 000 € contracté il y a 18 mois. Pas d'apport disponible "
    "pour le moment."
)

LOAN_TYPES = [None, "pro", "immobilier", "consommation"]

# Editable fields, in display order. The keys are exactly those of
# extraction.SCHEMA; the kind drives the widget used to edit the value.
REQUEST_FIELDS = [
    ("loan_type", "Type de crédit", "choice"),
    ("requested_amount", "Montant demandé (€)", "money"),
    ("financing_purpose", "Objet du financement", "text"),
    ("down_payment", "Apport (€)", "money"),
    ("existing_debt", "Encours de dette existante (€)", "money"),
    ("loan_duration_years", "Durée souhaitée (années)", "years"),
    ("existing_debt_annual_payment", "Annuité de la dette existante (€)", "money"),
]

COMPANY_FIELDS = [
    ("company_type", "Forme juridique", "text"),
    ("sector", "Secteur", "text"),
    ("company_age_years", "Ancienneté (années)", "years"),
    ("revenue", "Chiffre d'affaires (€)", "money"),
    ("net_income", "Résultat net (€)", "signed_money"),
    ("previous_net_income", "Résultat net N-1 (€)", "signed_money"),
    ("ebitda", "EBITDA (€)", "signed_money"),
    ("caf", "CAF (€)", "signed_money"),
]

ALL_FIELDS = REQUEST_FIELDS + COMPANY_FIELDS

# Colour band of a score, keyed on the risk level DECISION_BANDS carries.
# The thresholds themselves stay in scoring.py: moving a band there moves the
# colour with it, and nothing has to be edited here.
RISK_BANDS = {
    "faible": "good",
    "modéré": "watch",
    "élevé": "watch",
    "très élevé": "bad",
}

# Criterion appraisal, by risk note: favourable, correct, unfavourable, very
# unfavourable. Four dots, one per note, so the table reads without the
# figures. report.build_highlights reads the same scale: "good" is what it
# lists as a strength, "watch" and "bad" what it lists as a point to watch,
# and a "fair" criterion appears in this table only.
NOTE_BANDS = {0: "good", 1: "fair", 2: "watch", 3: "bad"}


# --- Grid configuration (expert mode) ----------------------------------------

@st.cache_resource
def default_grid_config():
    """Snapshot of the shipped grid, taken before any expert-mode override.

    Cached so it survives reruns: app.py is re-executed on every interaction,
    while the scoring module keeps whatever values were pushed into it.
    """
    return {
        "weights": dict(scoring.WEIGHTS),
        "cash_flow_proxy_factor": scoring.CASH_FLOW_PROXY_FACTOR,
        "default_loan_years": scoring.DEFAULT_LOAN_YEARS,
        "default_annual_rate": scoring.DEFAULT_ANNUAL_RATE,
    }


def apply_grid_config():
    """Push the expert-mode settings into the scoring module.

    Called at the top of every rerun, before any scoring: the expert widgets
    sit at the bottom of the page but store their values in session state
    under the same keys, so the current configuration is always applied first.
    """
    defaults = default_grid_config()

    for key, weight in defaults["weights"].items():
        scoring.WEIGHTS[key] = st.session_state.get(f"weight_{key}", weight)

    scoring.CASH_FLOW_PROXY_FACTOR = st.session_state.get(
        "cash_flow_proxy_factor", defaults["cash_flow_proxy_factor"]
    )
    scoring.DEFAULT_LOAN_YEARS = st.session_state.get(
        "default_loan_years", defaults["default_loan_years"]
    )
    scoring.DEFAULT_ANNUAL_RATE = st.session_state.get(
        "annual_rate_pct", defaults["default_annual_rate"] * 100
    ) / 100

    # annual_payment() takes the duration and the rate as default arguments,
    # bound once at definition time: rebinding the module globals above is not
    # enough, the stored defaults have to follow.
    scoring.annual_payment.__defaults__ = (
        scoring.DEFAULT_LOAN_YEARS,
        scoring.DEFAULT_ANNUAL_RATE,
    )


# --- Pipeline ----------------------------------------------------------------

def run_analysis(data):
    """Score the data and write the sheet. Extraction is never replayed.

    Returns (analysis, report). The report is None when the engine refused to
    score the file: there is nothing to word in that case.
    """
    with st.spinner("Calcul du score et rédaction de la fiche..."):
        analysis = compute_score(data)
        report = None
        if analysis["score"] is not None:
            report = write_report(data, analysis)
    return analysis, report


def store_analysis(data):
    """Run the scoring pipeline and keep the result in session state."""
    st.session_state.error = None
    try:
        analysis, report = run_analysis(data)
    except ReportError as error:
        st.session_state.error = (
            "La rédaction de la fiche a échoué : le modèle local n'a pas "
            f"renvoyé une réponse exploitable. ({error})"
        )
    except Exception as error:  # local model unreachable, timeout, etc.
        st.session_state.error = (
            "L'analyse n'a pas abouti. Si le modèle local n'est pas démarré, "
            f"lancez Ollama puis réessayez. ({type(error).__name__})"
        )
    else:
        st.session_state.analysis = analysis
        st.session_state.report = report


# --- Rendering helpers -------------------------------------------------------

def html(markup):
    """Emit a raw HTML fragment.

    Fragments are built on a single line: Streamlit runs the string through a
    Markdown parser first, where an indented line would become a code block.
    """
    st.markdown(markup, unsafe_allow_html=True)


def section(title):
    """Section heading, the accent colour."""
    html(f"<p class='section-title'>{escape(title)}</p>")


def bullet_list(items, empty_message):
    """HTML for a list of sentences, or a quiet line when there is nothing."""
    if not items:
        return f"<p class='empty'>{escape(empty_message)}</p>"
    return (
        "<ul class='list'>"
        + "".join(f"<li>{escape(str(item))}</li>" for item in items)
        + "</ul>"
    )


def render_bullets(items, empty_message):
    """Render a list of sentences, or a caption when there is nothing."""
    html(bullet_list(items, empty_message))


def score_band(score):
    """Colour band of a score, read from the thresholds in scoring.py.

    Returns "none" for an unscored file, which the CSS renders in neutral
    tones: a credit outside the grid must still lay out cleanly.
    """
    if score is None:
        return "none"
    for floor, level, _ in scoring.DECISION_BANDS:
        if score >= floor:
            return RISK_BANDS.get(level, "watch")
    return "bad"


def render_headline(sheet):
    """Score card: progress ring, recommendation, risk level, completeness.

    Three ranks, in this order: the figure, the recommendation on its own
    line, then the supporting rows. An unscored file takes the neutral band
    and renders a dash in place of the figure, with an empty ring.
    """
    score = sheet["score"]
    band = score_band(score)
    # The band class is set on the card, so the ring, the figure, the
    # recommendation and the wash all read the same four tones. Only the ring
    # percentage is inline: it is a value, not a colour.
    html(
        f"<div class='card score-card band--{band}'>"
        f"<div class='ring' style='--ring-pct:{score if score is not None else 0}'>"
        f"<div class='ring-inner'><span class='ring-value'>"
        f"{score if score is not None else '—'}</span>"
        "<span class='ring-max'>/ 100</span></div></div>"
        "<div class='score-meta'>"
        "<p class='score-caption'>Score de solidité, "
        "100 = dossier le plus sûr</p>"
        f"<p class='score-reco'>{escape(str(sheet['recommandation']))}</p>"
        "<dl class='meta'>"
        "<div><dt>Niveau de risque</dt>"
        f"<dd class='band'>{escape(str(sheet['niveau_risque']))}</dd></div>"
        "<div><dt>Complétude des données</dt>"
        f"<dd>{sheet['completude']} % "
        f"(fiabilité {escape(str(sheet['fiabilite']))})</dd></div>"
        "</dl>"
        f"<div class='meter'><span style='width:{sheet['completude']}%'></span>"
        "</div>"
        "</div></div>"
    )


def highlight_card(title, items, empty_message, band):
    """One highlight card, with a coloured rule down its left edge."""
    return (
        f"<div class='card card--band band--{band}'>"
        f"<p class='card-title'>{escape(title)}</p>"
        + bullet_list(items, empty_message)
        + "</div>"
    )


def render_highlights(sheet):
    """Strengths and vigilance points, side by side, same height."""
    html(
        "<div class='grid-2'>"
        + highlight_card(
            "Points forts",
            sheet["points_forts"],
            "Aucun point fort identifié.",
            "good",
        )
        + highlight_card(
            "Points de vigilance",
            sheet["points_vigilance"],
            "Aucun point de vigilance.",
            "watch",
        )
        + "</div>"
    )


def render_criteria_table(items):
    """Criteria table, one coloured dot per appraisal."""
    # thead and tbody are explicit: the zebra rule counts rows inside the
    # body, and must not count the header row among them.
    header = (
        "<thead><tr><th>Critère</th><th>Valeur</th>"
        "<th class='num'>Note</th><th class='num'>Poids</th></tr></thead>"
    )
    rows = []
    for item in items:
        note = item["note"]
        band = NOTE_BANDS.get(note, "none")
        rows.append(
            "<tr>"
            f"<td>{escape(str(item['critere']))}</td>"
            f"<td>{escape(str(item['valeur']))}</td>"
            "<td class='num'><span class='appraisal-inner'>"
            f"<span class='dot dot--{band}'></span>"
            f"<span class='note'>{'—' if note is None else note}</span>"
            "</span></td>"
            f"<td class='num'>{escape(str(item['poids']))}</td>"
            "</tr>"
        )
    html(
        "<div class='table-wrap'><table class='criteria'>"
        + header
        + "<tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def render_boxed_list(title, items, empty_message, style):
    """A titled list inside a box. Style is "note-box" or "alert-box"."""
    html(
        f"<div class='{style}'>"
        f"<p class='box-title'>{escape(title)}</p>"
        + bullet_list(items, empty_message)
        + "</div>"
    )


def edit_field(key, label, kind, value):
    """Draw one editable field and return the corrected value.

    Missing values are flagged twice: in the label, and by an amber wash on
    the control. The wash needs a CSS hook, which is what the keyed wrapper
    container provides -- the key sits on the container, never on the widget.
    The widgets themselves stay keyless on purpose: they then follow the value
    passed in, so a new extraction resets the form instead of showing the
    previous request.
    """
    if value is None:
        label = f"{label} · à compléter"
        wrapper = st.container(key=f"missing-{key}")
    else:
        wrapper = nullcontext()

    with wrapper:
        return _field_widget(label, kind, value)


def _field_widget(label, kind, value):
    """The bare widget for one field, chosen by kind."""
    if kind == "choice":
        options = LOAN_TYPES if value in LOAN_TYPES else [value] + LOAN_TYPES
        return st.selectbox(
            label,
            options,
            index=options.index(value),
            format_func=lambda v: v if v else "non renseigné",
        )

    if kind == "text":
        text = st.text_input(label, value=value or "")
        return text.strip() or None

    # Floats throughout: st.number_input refuses an int value next to a
    # float step, and extract() returns whole numbers as ints.
    number = None if value is None else float(value)

    if kind == "years":
        return st.number_input(
            label, value=number, min_value=0.0, step=0.5, format="%.1f"
        )

    return st.number_input(
        label,
        value=number,
        min_value=None if kind == "signed_money" else 0.0,
        step=500.0,
        format="%.0f",
    )


def render_form(data):
    """Editable view of the extracted data. Returns the corrected dict."""
    missing = [label for key, label, _ in ALL_FIELDS if data.get(key) is None]

    with st.form("edit_data"):
        if missing:
            st.caption(
                f"{len(missing)} valeur(s) non trouvée(s) dans la demande. "
                "Un champ laissé vide reste une donnée manquante ; saisir 0 "
                "est une information différente."
            )
        else:
            st.caption("Toutes les valeurs ont été trouvées dans la demande.")

        corrected = {}
        left, right = st.columns(2)

        with left:
            st.markdown("**Demande**")
            for key, label, kind in REQUEST_FIELDS:
                corrected[key] = edit_field(key, label, kind, data.get(key))

        with right:
            st.markdown("**Entreprise**")
            for key, label, kind in COMPANY_FIELDS:
                corrected[key] = edit_field(key, label, kind, data.get(key))

        submitted = st.form_submit_button("Recalculer", type="primary")

    return corrected, submitted


def render_details(sheet):
    """Criteria table, assumptions and alerts, folded away by default."""
    with st.expander("Détail des critères, hypothèses et alertes"):
        if sheet["detail_criteres"]:
            render_criteria_table(sheet["detail_criteres"])
            st.caption("Note de 0 (favorable) à 3 (défavorable), — = non évalué.")
        else:
            st.caption("Aucun critère évalué.")

        render_boxed_list(
            "Hypothèses de calcul",
            sheet["hypotheses"],
            "Aucune hypothèse : données complètes.",
            "note-box",
        )
        render_boxed_list(
            "Alertes", sheet["alertes"], "Aucune alerte.", "alert-box"
        )


def render_expert_mode():
    """Grid configuration. Changes apply on the next Recalculer."""
    defaults = default_grid_config()

    with st.expander("Mode expert"):
        st.warning(
            "Ces valeurs sont la configuration de la grille de notation. "
            "Elles ne doivent pas être modifiées en usage courant : un score "
            "calculé avec une grille modifiée n'est plus comparable aux "
            "autres dossiers. Les changements prennent effet au prochain "
            "clic sur Recalculer."
        )

        st.markdown("**Poids des critères**")
        columns = st.columns(4)
        for index, (key, weight) in enumerate(defaults["weights"].items()):
            with columns[index % 4]:
                st.number_input(
                    scoring.CRITERIA[key][0],
                    min_value=0,
                    max_value=100,
                    value=weight,
                    step=1,
                    key=f"weight_{key}",
                )
        st.caption(f"Total des poids : {sum(scoring.WEIGHTS.values())}")

        st.markdown("**Constantes de calcul**")
        left, middle, right = st.columns(3)
        with left:
            st.number_input(
                "Facteur de proxy CAF",
                min_value=1.0,
                max_value=3.0,
                value=defaults["cash_flow_proxy_factor"],
                step=0.1,
                key="cash_flow_proxy_factor",
                help="CAF estimée = résultat net × ce facteur, faute d'EBITDA "
                "ou de CAF communiquée.",
            )
        with middle:
            st.number_input(
                "Durée de prêt par défaut (années)",
                min_value=1,
                max_value=25,
                value=defaults["default_loan_years"],
                step=1,
                key="default_loan_years",
                help="Utilisée quand la demande ne précise pas de durée.",
            )
        with right:
            st.number_input(
                "Taux annuel par défaut (%)",
                min_value=0.0,
                max_value=25.0,
                value=defaults["default_annual_rate"] * 100,
                step=0.25,
                key="annual_rate_pct",
                help="Taux retenu pour simuler l'annuité du nouveau prêt.",
            )


# --- Page --------------------------------------------------------------------

st.set_page_config(page_title="Analyse de demandes de crédit", layout="wide")

# The one and only CSS injection.
html(CSS)

apply_grid_config()

for key in ("data", "analysis", "report", "error"):
    st.session_state.setdefault(key, None)

st.title("Analyse de demandes de crédit professionnel")
st.caption(
    "Extraction et rédaction par un modèle local, notation déterministe. "
    "L'outil oriente un dossier, il ne décide pas à la place du comité."
)

request_text = st.text_area(
    "Demande de financement",
    value=SAMPLE_REQUEST,
    height=200,
    help="Collez ici la demande telle qu'elle a été reçue.",
)

if st.button("Analyser", type="primary"):
    st.session_state.error = None
    try:
        with st.spinner("Extraction des données par le modèle local..."):
            st.session_state.data = extract(request_text)
    except ExtractionError as error:
        st.session_state.error = (
            "L'extraction a échoué : le modèle local n'a pas renvoyé de "
            f"données exploitables pour cette demande. ({error})"
        )
        st.session_state.data = None
    except Exception as error:  # local model unreachable, timeout, etc.
        st.session_state.error = (
            "L'extraction n'a pas abouti. Si le modèle local n'est pas démarré, "
            f"lancez Ollama puis réessayez. ({type(error).__name__})"
        )
        st.session_state.data = None
    else:
        st.session_state.analysis = None
        st.session_state.report = None
        store_analysis(st.session_state.data)

# Filled in once the form below has been handled: an error fixed by the
# latest Recalculer must not stay on screen for one more interaction.
error_slot = st.container()

if st.session_state.data is None:
    with error_slot:
        if st.session_state.error:
            st.error(st.session_state.error)
        # Same panel as the spinner that replaces it, so the first screen and
        # the waiting screen are the same object in two states.
        html(
            "<div class='empty-state'><p class='empty-state-text'>"
            "Collez une demande puis cliquez sur Analyser.</p></div>"
        )
    st.stop()

st.divider()

# The conclusion is read first and the working data last: score, sheet, then
# the extracted values, the criteria and the grid, each folded away. The whole
# conclusion is filled in further down, because it has to reflect the values
# the form below just submitted.
conclusion = st.container()

# Working data, not the answer to the question asked: below the sheet and
# folded by default.
with st.expander("Données extraites"):
    corrected, submitted = render_form(st.session_state.data)

if submitted:
    st.session_state.data = corrected
    store_analysis(corrected)

if st.session_state.error:
    with error_slot:
        st.error(st.session_state.error)

analysis = st.session_state.analysis
report = st.session_state.report

if analysis is None:
    st.stop()

# The engine refuses to score what its grid does not cover: show why, and
# leave the form below so the file can be corrected and replayed.
if analysis["score"] is None:
    with conclusion:
        for alert in analysis["alertes"]:
            st.warning(alert)
        if not analysis["alertes"]:
            # Nothing out of scope, simply nothing to compute: the request
            # carries no figure any criterion can be graded on.
            st.warning(
                "Aucun critère n'a pu être calculé : la demande ne contient "
                "aucune donnée chiffrée exploitable. Complétez les données "
                "extraites ci-dessous puis relancez le calcul."
            )
        section("Recommandation")
        html(
            "<p class='prose'>"
            f"<strong>{escape(str(analysis['recommandation']))}</strong></p>"
        )
        st.divider()
    render_details(analysis)
    render_expert_mode()
    st.stop()

with conclusion:
    render_headline(report)

    section("Synthèse")
    html(f"<p class='prose'>{escape(str(report['synthese']))}</p>")

    render_highlights(report)

    section("Recommandation")
    html(
        "<p class='prose'>"
        f"<strong>{escape(str(report['recommandation']))}</strong></p>"
        f"<p class='prose'>{escape(str(report['justification']))}</p>"
    )

    section("Pièces à demander")
    render_bullets(report["pieces_a_demander"], "Aucune pièce complémentaire.")

    st.divider()

render_details(report)
render_expert_mode()

st.download_button(
    "Télécharger la fiche (JSON)",
    data=json.dumps(
        {
            "donnees_extraites": st.session_state.data,
            "analyse": analysis,
            "fiche": report,
        },
        indent=2,
        ensure_ascii=False,
    ),
    file_name="fiche_credit.json",
    mime="application/json",
)
