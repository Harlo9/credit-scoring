# prompts.md

Carnet de bord. Ce que j'ai demandé, ce qui a raté, ce que j'ai corrigé.

---

## Étape 0 : définition du scoring

Avant de coder, j'ai posé les critères de risque et leurs poids.

| Critère | Poids |
|---|---|
| Capacité de remboursement | 30 |
| Tendance du résultat | 15 |
| Endettement global | 15 |
| Rentabilité | 10 |
| Apport | 10 |
| Ancienneté | 10 |
| Secteur | 5 |
| Nature du bien financé | 5 |

**Décision clé :** le LLM extrait, le code calcule. Un score bancaire doit
donner le même résultat à chaque lancement, et pouvoir s'expliquer ligne par
ligne. Un LLM qui sort un chiffre ne sait pas faire ça.

Deux règles écrasent le score : refus direct si le résultat net est négatif,
et pièces complémentaires forcées si une donnée clé manque. Un scoring qui
refuse sur des données incomplètes est un scoring qui se trompe.

---

## Étape 1 : extraction

Objectif : transformer un email en texte libre en JSON exploitable.

**Prompt de départ**

"Extrais les informations financières de ce texte au format JSON."

Les clés changeaient d'un appel à l'autre. Inutilisable en code.

**Correction 1**

J'ai écrit le schéma complet dans le prompt, clé par clé, avec le type attendu.

**Correction 2**

Le modèle comblait les trous : sur un texte sans CA, il sortait un montant
plausible. C'est le pire comportement ici, un chiffre inventé passe ensuite
dans le calcul de risque sans que personne le voie.

Règle ajoutée en majuscules : toute information absente vaut null. Jamais
d'estimation.

**Correction 3**

"Pas d'apport disponible" sortait en null. Or c'est une information, pas une
absence d'information. Apport à zéro pénalise le dossier, apport inconnu
déclenche une demande de pièces. Pas la même recommandation. J'ai explicité
les deux cas.

**Choix du modèle**

Local, avec Ollama et llama3.1:8b. Ce n'est pas un choix technique mais métier :
liasses fiscales et données clients nominatives ne sortent pas d'une banque.

Le passage en local a demandé deux ajustements : un nettoyage plus strict de la
sortie (le modèle ajoute parfois une phrase autour du JSON) et un exemple
complet dans le prompt, parce qu'un 8B suit moins bien une consigne de format.

**Résultat**

Extraction exacte du premier coup sur la demande de l'énoncé, y compris les
deux pièges : distinction entre les deux exercices comptables, et apport à 0.

**Blocages**

- `temperature` refusé par le SDK Anthropic, version trop ancienne, réglé par upgrade.
- Erreur 429, plus de crédits API. Ça a accéléré le passage en local.
- qwen2.5:3b trop petit, il confondait les deux résultats nets.

