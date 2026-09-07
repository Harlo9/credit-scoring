# Évaluation de l'extraction

30 demandes annotées à la main, 3 passage(s) chacune.

| Métrique | Valeur |
| --- | --- |
| Justesse par champ | 97.3 % (1314/1350) |
| Hallucinations | 3 |
| Champs manqués | 15 |
| Valeurs erronées | 18 |
| Écart de score moyen | 3.4 points sur 100 |
| Recommandation changée | 0/90 |
| Extractions en échec | 0 |

## Par champ

| Champ | OK | Manqué | Erroné | Halluciné |
| --- | --- | --- | --- | --- |
| `loan_type` | 84 | 6 | 0 | 0 |
| `requested_amount` | 90 | 0 | 0 | 0 |
| `financing_purpose` | 90 | 0 | 0 | 0 |
| `down_payment` | 87 | 3 | 0 | 0 |
| `existing_debt` | 84 | 3 | 0 | 3 |
| `company_type` | 90 | 0 | 0 | 0 |
| `sector` | 84 | 3 | 3 | 0 |
| `company_age_years` | 84 | 0 | 6 | 0 |
| `revenue` | 84 | 0 | 6 | 0 |
| `net_income` | 87 | 0 | 3 | 0 |
| `previous_net_income` | 90 | 0 | 0 | 0 |
| `ebitda` | 90 | 0 | 0 | 0 |
| `caf` | 90 | 0 | 0 | 0 |
| `loan_duration_years` | 90 | 0 | 0 | 0 |
| `existing_debt_annual_payment` | 90 | 0 | 0 | 0 |

## Hallucinations relevées

- `annuite-donnee` / `existing_debt` : 18000
- `annuite-donnee` / `existing_debt` : 18000
- `annuite-donnee` / `existing_debt` : 18000
