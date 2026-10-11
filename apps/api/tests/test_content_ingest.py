import pytest
from sqlalchemy import text

from app.core.db import get_engine
from app.modules.content.ingest import IngestError, chunk_text, load_chapter, parse_chapter


@pytest.fixture(autouse=True)
def _clean_content():
    with get_engine().begin() as conn:
        conn.execute(text("TRUNCATE chapters CASCADE"))
    yield
    with get_engine().begin() as conn:
        conn.execute(text("TRUNCATE chapters CASCADE"))


SAMPLE = """---
level: premiere_s
code: fonctions
position: 1
title: Fonctions de référence
domain: analyse
source: Chap1_Fonct.pdf
---
## [exercice_corrige] Exercice 2 | p.3
1. $f(x) = x^2 - 4$.

2. $f$ est décroissante sur $]-\\infty ; 0]$ et croissante sur $[0 ; +\\infty[$.

## [tp] TP 1
Le minimum de $a$ et $b$ vaut $\\frac{a+b-|a-b|}{2}$.
"""


def test_parse_ok():
    ch = parse_chapter(SAMPLE)
    assert ch.code == "fonctions" and len(ch.sections) == 2
    assert ch.sections[0].page == "3" and ch.sections[1].page is None


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda s: s.replace("level: premiere_s", "level: cp"), "level invalide"),
        (lambda s: s.replace("[tp]", "[bidule]"), "inconnu"),
        (lambda s: s.replace("$f(x) = x^2 - 4$", "$f(x) = x^2 - 4"), "non apparié"),
        (lambda s: s.replace("\\frac{a+b-|a-b|}{2}", "\\frac{a+b-|a-b|{2}"), "accolades"),
        (lambda s: s.replace("domain: analyse\n", ""), "'domain' manquant"),
        (lambda s: s.split("## [tp]")[0] + "## [tp] TP 1\n", "section vide"),
    ],
)
def test_parse_rejects_bad_transcription(mutate, message):
    with pytest.raises(IngestError, match=message):
        parse_chapter(mutate(SAMPLE))


def test_chunks_never_split_a_paragraph():
    body = "\n\n".join(f"Paragraphe {i} " + "x" * 300 for i in range(5))
    chunks = chunk_text(body, limit=700)
    assert all(len(c) <= 700 for c in chunks)
    assert "\n\n".join(chunks) == body


def test_load_is_draft_and_searchable(db):
    out = load_chapter(db, parse_chapter(SAMPLE))
    assert out["lessons"] == 2 and out["chunks"] >= 2
    statuses = db.execute(text("SELECT DISTINCT status FROM lessons")).scalars().all()
    assert statuses == ["draft"]
    ref = db.execute(text("SELECT source_ref FROM lessons ORDER BY position LIMIT 1")).scalar_one()
    assert ref == "Chap1_Fonct.pdf p.3"
    # recherche plein texte français, sans accents
    hits = db.execute(
        text("SELECT count(*) FROM content_chunks WHERE tsv @@ plainto_tsquery('french', immutable_unaccent(:q))"),
        {"q": "decroissante"},
    ).scalar_one()
    assert hits == 1


def test_reload_replaces_drafts_but_refuses_validated(db):
    load_chapter(db, parse_chapter(SAMPLE))
    load_chapter(db, parse_chapter(SAMPLE))  # idempotent
    assert db.execute(text("SELECT count(*) FROM lessons")).scalar_one() == 2
    db.execute(text("UPDATE lessons SET status='validated', validated_at=now() WHERE position=1"))
    with pytest.raises(IngestError, match="validé"):
        load_chapter(db, parse_chapter(SAMPLE))
    assert db.execute(text("SELECT count(*) FROM lessons")).scalar_one() == 2  # rien d'écrasé


EXERCISE_SAMPLE = SAMPLE.replace("source: Chap1_Fonct.pdf", "source: Chap1_Fonct.pdf\nmanual: Manuel test 2014") + """
## [exercice] Exercice 7 | p.39
### Énoncé
Calculer $|1 - \\pi|$.
### Corrigé
$|1 - \\pi| = \\pi - 1$.
"""


def test_exercise_pairs_are_loaded_as_drafts(db):
    out = load_chapter(db, parse_chapter(EXERCISE_SAMPLE))
    assert out["exercises"] == 1 and out["lessons"] == 2
    row = db.execute(text("SELECT statement_md, solution_md, status, source_ref FROM exercises")).one()
    assert "Calculer" in row.statement_md and "pi - 1" in row.solution_md
    assert row.status == "draft"
    assert row.source_ref == "Manuel test 2014 p.39 ; corrigé Chap1_Fonct.pdf"


@pytest.mark.parametrize(
    "block, message",
    [
        ("### Énoncé\nQuestion $x$.\n", "obligatoires"),
        ("### Corrigé\nRéponse.\n### Énoncé\nQuestion.\n", "obligatoires"),
        ("### Énoncé\n\n### Corrigé\nRéponse.\n", "vide"),
    ],
)
def test_exercise_needs_statement_then_solution(block, message):
    raw = SAMPLE + "\n## [exercice] Exercice 8 | p.40\n" + block
    with pytest.raises(IngestError, match=message):
        parse_chapter(raw)


def test_validated_exercise_blocks_reload(db):
    load_chapter(db, parse_chapter(EXERCISE_SAMPLE))
    db.execute(text("UPDATE exercises SET status='validated'"))
    with pytest.raises(IngestError, match="validé"):
        load_chapter(db, parse_chapter(EXERCISE_SAMPLE))
