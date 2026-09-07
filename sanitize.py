"""Input screening for prompt injection attempts.

The extraction step reads text written by whoever sent the request. A
local 8B model does not separate instructions from content: an order
written inside an email is followed like one written in the system
prompt. Our own eval measures this, the model obeys every time.

Prompt wording cannot fix that. This module does not try to neutralize
the attempt either, which is a losing game. It flags the request as
unsafe to score automatically, and hands it to a human. A blocked file
costs a phone call. A file scored on injected figures costs a loan.
"""

import re

# Patterns are deliberately broad. A false positive routes an honest
# request to manual review, which is the normal path for anything odd.
# A false negative lets forged figures into a credit decision.
INJECTION_PATTERNS = [
    r"ignore[zr]?\s+(les\s+)?(instructions|consignes|règles)",
    r"oublie[zr]?\s+(les\s+)?(instructions|consignes|règles)",
    r"disregard\s+(the\s+)?(previous|above|prior)",
    r"renvoie[zr]?\s+un\s+",
    r"tu\s+(dois|vas)\s+(renvoyer|répondre|indiquer)",
    r"nouvelles?\s+instructions?",
    r"system\s*prompt",
    r"<\s*/?\s*(system|instruction|demande)\s*>",
]

COMPILED = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]

# A run of capitals is not an attack on its own, people shout. Combined
# with an imperative it is a strong signal, so it only counts as one.
SHOUTING = re.compile(r"[A-ZÀ-Ÿ\s]{40,}")


def scan(text):
    """Return the list of injection signals found in a request.

    An empty list means nothing suspicious was seen, not that the text
    is safe. This is a filter, not a proof.
    """
    signals = []
    for pattern in COMPILED:
        match = pattern.search(text)
        if match:
            signals.append(f"Instruction détectée dans le texte : « {match.group(0)} »")
    if SHOUTING.search(text) and signals:
        signals.append("Passage en majuscules accompagnant une instruction")
    return signals