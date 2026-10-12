# Jabal IA

Tuteur IA de mathématiques pour les lycéens de Djibouti (Première S d'abord, Terminale S ensuite),
conçu mobile d'abord, utilisable avec un réseau instable et payable par mobile money.

> Projet porté par AMD Digital. Licence Apache 2.0.

## État

Semaine 1 de la feuille de route : socle, authentification, comptes parent/élève.

| Module | État |
| --- | --- |
| Base PostgreSQL (schéma, règles d'abonnement en SQL) | fait, migrations Alembic |
| Authentification (inscription, connexion, jetons, 2 appareils maximum) | fait, testé |
| Comptes parent et élève, consentement parental | fait, testé |
| Paiement (Waafi, D-Money, repli manuel) | semaine 2 |
| Contenu, tuteur IA, PWA hors ligne | semaine 3 |

## Démarrer en local

Prérequis : Python 3.12+, Docker.

```bash
docker compose up -d db
cd apps/api
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env            # puis renseigner JWT_SECRET
export DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/jabal
alembic upgrade head
uvicorn app.main:app --reload   # http://localhost:8000/docs
```

Créer un administrateur : `python -m app.cli create-admin --phone "77 00 00 00" --name "Nom"`.

Tests (ils recréent un schéma vide dans la base `jabal_test` et rejouent toutes les migrations) :

```bash
psql -h localhost -U postgres -c "CREATE DATABASE jabal_test ENCODING 'UTF8'"
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/jabal_test pytest
```

> La base de test **doit** être en UTF8. Sinon psycopg renvoie des octets au lieu de texte et tout échoue.

## Structure

```
apps/api/        API FastAPI (modules : auth, puis billing, content, tutor, progress, admin)
db/              schéma baseline et notes sur les migrations
docs/            décisions d'architecture
content/         contenu pédagogique validé (les PDF sources ne sont JAMAIS versionnés)
```

## Règle importante : le contenu des manuels

Ce dépôt est public. Les chapitres et corrigés du manuel ne doivent pas y être publiés tant que
leurs auteurs ou l'inspection n'ont pas donné une autorisation écrite. Le `.gitignore` exclut
`*.pdf`, `content/raw/` et `content/source/`. Ne les contournez pas.

## Documentation

- [Décisions d'architecture](docs/architecture.md)
- [Base de données](db/README.md)
