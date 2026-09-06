# prompts.md

Carnet de bord. Ce que j'ai demandé à l'IA, ce qui a raté, ce que j'ai corrigé.

Outil construit avec Claude Code, et un modèle local (Ollama, llama3.1:8b)
pour l'extraction et la rédaction.

---

## Étape 0 : la décision qui structure tout

Premier réflexe possible : demander au LLM de tout faire. Il lit la demande,
il sort un score, il rédige la fiche. Un seul appel.

Je ne l'ai pas fait, pour deux raisons.

**Un score doit être reproductible.** Si je relance deux fois le même dossier
et que j'obtiens 68 puis 74, l'outil est inutilisable. Un chargé de clientèle
ne peut pas défendre un chiffre qui bouge.

**Un score doit être explicable.** Quand il tombe à 42, je veux dire quelle
ligne a pesé combien. Un LLM qui sort un chiffre le justifie après coup.

D'où l'architecture :

| Étape | Qui fait quoi | Pourquoi |
|---|---|---|
| 1. Extraction | LLM | Seul capable de lire du texte libre d'email |
| 2. Scoring | Code | Doit être identique à chaque lancement |
| 3. Rédaction | LLM | Seul capable d'écrire correctement |

Le LLM ne calcule jamais. Le code ne rédige jamais.

---

## Étape 1 : les critères de risque

Posés avant d'écrire une ligne de code, à partir de ce qui est calculable
depuis un texte de ce type.

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

Score de 0 à 100, 100 étant le dossier le plus sûr.

**Ce que j'assume :** ces poids viennent d'un raisonnement métier, pas de
données de défaut observées. Avec un historique réel, on les ajusterait par
régression. Les coefficients de secteur et de nature du bien sont des
hypothèses de prototype, marquées comme telles dans le code.

**Pas de refus automatique.** Un résultat négatif déclenche une alerte et un
passage en comité, pas un rejet. Un exercice en perte peut être un exercice
d'investissement. L'outil oriente, le comité décide.

---

## Étape 2 : l'extraction

**Prompt de départ**, trop vague : "Extrais les informations financières au
format JSON." Les clés changeaient d'un appel à l'autre. Inutilisable.

**Correction 1.** Le schéma complet dans le prompt, clé par clé, avec le type.

**Correction 2.** Le modèle comblait les trous : sans CA dans le texte, il
sortait un montant plausible. C'est le pire comportement possible, un chiffre
inventé passe ensuite dans le calcul sans que personne le voie. J'ai ajouté
une règle en majuscules : toute information absente vaut null.

**Correction 3.** "Pas d'apport disponible" sortait en null. Or c'est une
information, pas une absence d'information. Apport à zéro pénalise le dossier,
apport inconnu déclenche une demande de pièces. Deux recommandations
différentes. J'ai explicité les deux cas.

---

## Étape 3 : le choix du modèle local

Je suis parti sur une API cloud, puis j'ai basculé sur Ollama en local.

**Ce n'est pas un choix technique mais métier.** On parle de liasses fiscales
et de données clients nominatives. Dans une banque, ça ne sort pas vers un
fournisseur externe. J'ai déjà déployé du RAG on-premise avec Ollama chez un
client industriel, donc je sais que ça tient.

Deux ajustements ont été nécessaires : un nettoyage plus strict de la sortie
(un 8B ajoute parfois une phrase autour du JSON) et un exemple complet dans le
prompt. Montrer un cas résolu marche mieux que décrire la règle.

J'ai ensuite retiré toute dépendance à un SDK de fournisseur. Le client tient
en trente lignes de bibliothèque standard.

---

## Étape 4 : la rédaction de la fiche

C'est là que j'ai rencontré le blocage le plus intéressant.

**Le problème.** J'ai demandé au LLM de classer les critères en points forts et
points de vigilance. Il a mis la capacité de remboursement dans les deux
listes, et l'a qualifiée de "correcte" alors que le moteur la notait
défavorable. Il recopiait aussi les mots internes du moteur dans le texte.

**La correction intermédiaire.** J'ai envoyé une appréciation en clair
("favorable", "défavorable") à côté de chaque valeur, au lieu du chiffre brut.
Ça a amélioré, sans régler.

**La vraie correction.** J'ai déplacé le classement dans le code. Note 0 ou 1
en points forts, 2 ou 3 en vigilance, formulation écrite là où les chiffres
existent. Le LLM ne rédige plus que la synthèse, la justification et la liste
des pièces à demander.

**Ce que j'en retiens :** classer est une règle, pas une tâche de rédaction. À
chaque fois que je demandais au modèle d'appliquer une règle, il dérivait. À
chaque fois que je lui demandais d'écrire, il était bon. C'est la même
frontière qu'à l'étape 0, appliquée un cran plus loin.

---

## Traitement des données manquantes

Le point qui m'a demandé le plus de réflexion.

**Une donnée absente n'est pas une donnée défavorable.** Au début, un critère
non calculable recevait une note neutre. Effet pervers : un dossier très
incomplet remontait mécaniquement vers la moyenne. J'ai changé pour une
exclusion du calcul, avec renormalisation des poids sur les critères
disponibles.

**J'ai séparé deux notions** qui n'ont rien à voir : le score (le dossier
est-il solide) et la complétude (a-t-on de quoi juger). Sous 70 % de
complétude, la recommandation bascule sur "pièces complémentaires", quel que
soit le score.

**Les hypothèses sont affichées, pas cachées.** Faute de CAF communiquée, je
l'estime à 1,5 fois le résultat net. Faute de durée, je simule sur 5 ans à
5 %. Ces trois hypothèses remontent dans la fiche, donc l'utilisateur peut les
contester. Et chacune devient automatiquement une pièce à réclamer.

---

## Limites de l'outil

- **Périmètre.** Crédit professionnel uniquement. Une demande immobilier ou
  conso renvoie "grille non applicable" plutôt qu'un score faux. Un outil qui
  refuse de répondre hors de son domaine vaut mieux qu'un outil qui répond
  n'importe quoi.
- **Déclaratif.** Aucune pièce justificative n'est vérifiée. En production, on
  croiserait avec la liasse fiscale et les relevés.
- **Contrôles de cohérence.** J'ai ajouté des bornes de plausibilité (résultat
  net supérieur au CA, apport supérieur au montant demandé) pour attraper les
  erreurs d'extraction, mais ça ne remplace pas une vérification humaine.

---

## Blocages rencontrés

- Paramètre `temperature` refusé par un SDK dans une version trop ancienne,
  réglé par une mise à jour du paquet.
- Erreur 429, plus de crédits sur l'API cloud. Ça a accéléré le passage en
  local, qui était de toute façon la bonne réponse.
- Un modèle de 3B confondait les deux exercices comptables. Le 8B règle le
  problème.
- Streamlit lancé avec le mauvais interpréteur Python (conda au lieu du venv),
  d'où un import cassé. Réglé avec `python -m streamlit`.

---

## Si j'avais plus de temps

- Calibrer les seuils sur des données de défaut réelles.
- Comparer plusieurs modèles locaux avec un taux d'erreur chiffré par champ.
- Lire directement un bilan en PDF au lieu d'un copier-coller de texte.