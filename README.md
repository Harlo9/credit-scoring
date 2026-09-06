# Analyse de demandes de crédit professionnel

Outil interne qui transforme une demande de financement en texte libre, copiée
depuis un email, en fiche de synthèse avec score de risque et recommandation.

Tout tourne en local. Aucune donnée client ne sort de la machine.

---

## Démo

![Démonstration de l'outil](docs/demo.gif)

**En entrée**, une demande de financement en texte libre, telle qu'elle arrive
par email ou par formulaire :

> Bonjour, je suis gérant d'une SARL dans le secteur du bâtiment, créée il y a
> 3 ans. Nous réalisons un CA annuel de 420 000 € avec un résultat net de
> 18 000 € cette année, en baisse par rapport aux 35 000 € de l'année
> précédente. Je souhaite financer l'achat d'un véhicule utilitaire et de
> matériel pour 65 000 €. Nous avons déjà un emprunt en cours de 40 000 €
> contracté il y a 18 mois. Pas d'apport disponible pour le moment.

**En sortie**, une fiche de synthèse :

- un score de solidité sur 100, avec le niveau de risque et la recommandation
- les points forts et les points de vigilance du dossier, chiffrés
- les hypothèses retenues quand une donnée manque, et les pièces à réclamer
  pour les lever
- le détail de chaque critère, avec sa valeur et son appréciation

Les données extraites restent éditables : corriger une valeur mal lue ou
saisir une donnée manquante met le score à jour instantanément, sans nouvel
appel au modèle.

---

## Stack

| Brique | Choix | Pourquoi |
|---|---|---|
| Modèle | Ollama, llama3.1:8b, en local | Données bancaires nominatives, rien ne sort de la machine |
| Client LLM | `urllib`, bibliothèque standard | Aucun SDK de fournisseur, 30 lignes suffisent |
| Scoring | Python pur, sans dépendance | Reproductible et testable en une seconde |
| Interface | Streamlit | Le plus court chemin vers une démo utilisable |
| Tests | pytest | Le moteur ne fait aucun appel réseau |
| Développement | Claude Code | Itérations documentées dans `prompts.md` |

Trois dépendances au total : `streamlit`, `pytest`, et Ollama.

---

## Comment ça marche

| Étape | Qui fait quoi |
|---|---|
| 1. Extraction | Un LLM local lit le texte et sort un JSON structuré |
| 2. Scoring | Le code calcule le score, avec des règles déterministes |
| 3. Rédaction | Le LLM écrit la synthèse, à partir du score déjà calculé |

**Le LLM ne calcule jamais le score.** Un score bancaire doit donner le même
résultat à chaque lancement, et pouvoir s'expliquer ligne par ligne. Le
classement des critères en points forts et points de vigilance est lui aussi
fait par le code, pour la même raison.

Le score va de 0 à 100. Plus il est haut, plus le dossier est solide.

| Score | Recommandation |
|---|---|
| 75 à 100 | Accord de principe |
| 60 à 74 | Accord sous conditions |
| 40 à 59 | Instruction en comité |
| 0 à 39 | Analyse renforcée, avis défavorable en l'état |

Aucun refus automatique : l'outil oriente un dossier, un humain décide.

---

## La grille

Huit critères, notés de 0 (favorable) à 3 (défavorable), pondérés sur 100.

| Critère | Poids | Ce qu'il mesure |
|---|---|---|
| Capacité de remboursement | 30 | Service de la dette rapporté à la CAF |
| Tendance du résultat | 15 | Variation du résultat net d'un exercice à l'autre |
| Endettement global | 15 | Dette existante plus nouveau prêt, rapportés au CA |
| Rentabilité | 10 | Marge nette |
| Apport | 10 | Part du projet financée par l'entreprise |
| Ancienneté | 10 | Années d'existence |
| Secteur | 5 | Coefficient de risque sectoriel |
| Nature du bien financé | 5 | Valeur de revente de l'actif en cas de défaut |

Un critère qui ne peut pas être calculé n'est pas rempli par une valeur
neutre : il est **exclu** de la pondération, et les poids sont renormalisés
sur ce qui a pu être calculé. Une donnée manquante ne pousse donc le score ni
vers le haut ni vers le bas — elle ressort dans la **complétude**.

Trois nombres sortent du moteur, et ils ne disent pas la même chose :

| Sortie | Question à laquelle elle répond |
|---|---|
| Score | Le dossier est-il solide, sur ce qu'on sait de lui ? |
| Complétude | Quelle part de l'analyse a pu être menée ? |
| Alertes | Que doit regarder un humain, quel que soit le score ? |

Sous **70 % de complétude**, la fiabilité passe à « insuffisante » et la
recommandation bascule sur « demande de pièces complémentaires » : le score
reste affiché, mais il ne porte plus la décision à lui seul.

Le classement des critères dans la fiche suit trois bandes, pas deux : un
critère noté 0 est un point fort, 2 et 3 sont des points de vigilance, et un
critère noté 1 — correct sans être un argument — n'apparaît dans aucune des
deux listes. Il reste lisible dans le tableau des critères.

Les pourcentages affichés sont arrondis **vers le bas**. Un apport de 9,99 %
s'affiche « 9 % » et non « 10 % » : le chiffre lu sur la fiche donne toujours
la note imprimée à côté de lui.

---

## Installation

Prérequis : Python 3.10 ou plus, et [Ollama](https://ollama.com).

```bash
git clone https://github.com/credit-scoring.git
cd credit-scoring

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

ollama pull llama3.1:8b
```

---

## Lancer

Dans un premier terminal, le serveur de modèle :

```bash
ollama serve
```

Dans un second, l'interface :

```bash
source .venv/bin/activate
python -m streamlit run app.py
```

L'application s'ouvre sur http://localhost:8501, pré-remplie avec la demande
d'exemple ci-dessus.

---

## Utiliser sans interface

```bash
python extraction.py   # texte vers JSON
python scoring.py      # JSON vers score
python report.py       # pipeline complet, fiche en JSON
```

En Python :

```python
from extraction import extract
from scoring import compute_score
from report import write_report

data = extract("Bonjour, je suis gérant d'une SARL...")
fiche = write_report(data, compute_score(data))
```

---

## Fichiers

| Fichier | Rôle |
|---|---|
| `app.py` | Interface Streamlit |
| `extraction.py` | Texte libre vers JSON structuré |
| `scoring.py` | Grille de risque : critères, seuils, poids |
| `report.py` | Classement des points et rédaction de la fiche |
| `llm.py` | Client Ollama minimal |
| `prompts.md` | Carnet de bord : itérations, blocages, décisions |
| `presentation.html` | Support de présentation du projet |
| `tests/test_scoring.py` | Tests du moteur de scoring |
| `tests/test_report.py` | Tests du classement des points de la fiche |

---

## Configurer

Le modèle et l'adresse du serveur se règlent par variables d'environnement :

```bash
export OLLAMA_MODEL=qwen2.5:14b
export OLLAMA_BASE_URL=http://localhost:11434
```

Les seuils et les poids de la grille sont regroupés en haut de `scoring.py`,
dans `WEIGHTS` et `THRESHOLDS`. Aucune valeur n'est écrite en dur dans les
fonctions.

L'interface expose un **mode expert**, replié en bas de page, pour ajuster les
poids et les constantes de calcul le temps d'une session — sans toucher au
fichier. Les changements prennent effet au prochain « Recalculer ». Un score
obtenu avec une grille modifiée n'est plus comparable aux autres dossiers.

---

## Tests

```bash
pytest -v
```

152 tests, sans appel réseau ni modèle : tout ce qui est testé est
déterministe. Ils décrivent le comportement du moteur plutôt que de le
redéfinir, et s'appuient sur les constantes exportées par `scoring.py` —
retoucher un poids fait suivre les tests, seul un changement de comportement
les casse.

| Ce qui est couvert | |
|---|---|
| Dossiers | Un bon dossier, un mauvais, un sans aucun chiffre, un hors périmètre |
| Arithmétique | Le score est recalculable depuis le détail des critères publié |
| Données manquantes | Critère exclu et non noté, libellés, hypothèses énoncées |
| `0` contre absent | Un apport nul est noté et alerte, un apport non renseigné ne l'est pas |
| Seuils | Chaque borne des six tables, plus les bandes de décision |
| Affichage | Le pourcentage affiché donne toujours la note affichée à côté |
| Fiche | Les trois bandes du classement, l'ordre et la troncature des listes |

---

## Limites connues

- **Crédit professionnel uniquement.** Une demande immobilier ou consommation
  renvoie « grille non applicable » plutôt qu'un score qui ne voudrait rien
  dire.
- **Déclaratif.** Aucune pièce justificative n'est vérifiée. Des contrôles de
  plausibilité attrapent les erreurs d'extraction grossières, sans remplacer
  une vérification humaine.
- **Seuils non calibrés.** Les poids, les coefficients de secteur et le
  facteur d'estimation de la CAF sont des hypothèses de travail, pas des
  valeurs issues de données de défaut observées.