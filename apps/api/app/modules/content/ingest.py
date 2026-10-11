"""Ingestion du contenu pédagogique (corrigés du manuel) : Markdown validé -> base, en brouillon.

Format d'un fichier chapitre (content/source/*.md, JAMAIS versionné : droits d'auteur) :

    ---
    level: premiere_s
    code: fonctions
    position: 1
    title: Fonctions de référence
    domain: analyse
    source: Chap1_Fonct.pdf
    ---
    ## [exercice_corrige] Exercice 2 | p.3
    Corps Markdown + LaTeX ($...$ ou $$...$$).

Chaque section devient une `lesson` en statut `draft`. Rien n'est servi aux élèves tant qu'un
enseignant n'a pas validé (status validated + validated_at), voir db/schema.sql.
"""
import re
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import Connection, text

LEVELS = {"premiere_s", "terminale_s"}
DOMAINS = {"analyse", "geometrie", "probabilites_statistiques", "arithmetique", "nombres_complexes"}
KINDS = {"diagnostic", "activite", "cours", "methode", "tp", "exercice_corrige"}
SECTION_RE = re.compile(r"^##\s*\[(?P<kind>[a-z_]+)\]\s*(?P<title>[^|]+?)\s*(?:\|\s*p\.(?P<page>[\d\-–, ]+))?\s*$")
MAX_CHUNK = 900  # caractères : assez court pour une recherche plein texte précise


class IngestError(ValueError):
    pass


@dataclass
class Section:
    kind: str
    title: str
    body: str
    page: str | None = None


@dataclass
class Chapter:
    level: str
    code: str
    position: int
    title: str
    domain: str
    source: str
    sections: list[Section] = field(default_factory=list)


def _split_front_matter(raw: str) -> tuple[dict[str, str], str]:
    if not raw.startswith("---\n"):
        raise IngestError("en-tête '---' manquant en début de fichier")
    end = raw.find("\n---\n", 4)
    if end == -1:
        raise IngestError("en-tête non fermé")
    meta: dict[str, str] = {}
    for line in raw[4:end].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, raw[end + 5 :]


def _check_math(body: str, where: str) -> None:
    """Détecte les erreurs de transcription les plus courantes (formule non fermée)."""
    stripped = body.replace("$$", "")
    if stripped.count("$") % 2:
        raise IngestError(f"{where} : symbole $ non apparié (formule LaTeX non fermée)")
    if body.count("$$") % 2:
        raise IngestError(f"{where} : $$ non apparié")
    if body.count("{") != body.count("}"):
        raise IngestError(f"{where} : accolades { '{}' } déséquilibrées")


def parse_chapter(raw: str) -> Chapter:
    meta, content = _split_front_matter(raw)
    for key in ("level", "code", "position", "title", "domain", "source"):
        if key not in meta:
            raise IngestError(f"en-tête : champ '{key}' manquant")
    if meta["level"] not in LEVELS:
        raise IngestError(f"level invalide : {meta['level']}")
    if meta["domain"] not in DOMAINS:
        raise IngestError(f"domain invalide : {meta['domain']}")
    if not meta["position"].isdigit():
        raise IngestError("position doit être un entier")
    chapter = Chapter(
        level=meta["level"], code=meta["code"], position=int(meta["position"]),
        title=meta["title"], domain=meta["domain"], source=meta["source"],
    )
    current: Section | None = None
    buf: list[str] = []

    def close() -> None:
        if current is None:
            return
        current.body = "\n".join(buf).strip()
        if not current.body:
            raise IngestError(f"section vide : {current.title}")
        _check_math(current.body, current.title)
        chapter.sections.append(current)

    for line in content.splitlines():
        if line.startswith("## "):
            close()
            m = SECTION_RE.match(line)
            if not m:
                raise IngestError(f"titre de section illisible : {line!r}")
            if m["kind"] not in KINDS:
                raise IngestError(f"type de section inconnu : {m['kind']}")
            current = Section(kind=m["kind"], title=m["title"], body="", page=(m["page"] or "").strip() or None)
            buf = []
        elif current is not None:
            buf.append(line)
        elif line.strip():
            raise IngestError("texte avant la première section")
    close()
    if not chapter.sections:
        raise IngestError("aucune section")
    return chapter


def chunk_text(body: str, limit: int = MAX_CHUNK) -> list[str]:
    """Découpe aux frontières de paragraphes ; ne coupe jamais une formule $$...$$ en deux."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    chunks: list[str] = []
    cur = ""
    for p in paragraphs:
        if cur and len(cur) + len(p) + 2 > limit:
            chunks.append(cur)
            cur = p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur:
        chunks.append(cur)
    return chunks


def load_chapter(conn: Connection, chapter: Chapter) -> dict[str, int]:
    """Charge (ou recharge) un chapitre. Les leçons déjà validées/publiées ne sont jamais écrasées."""
    row = conn.execute(
        text(
            "INSERT INTO chapters (level, position, code, title, domain) "
            "VALUES (:level, :position, :code, :title, :domain) "
            "ON CONFLICT (level, code) DO UPDATE SET title = EXCLUDED.title, domain = EXCLUDED.domain "
            "RETURNING id"
        ),
        {"level": chapter.level, "position": chapter.position, "code": chapter.code,
         "title": chapter.title, "domain": chapter.domain},
    ).one()
    chapter_id = row.id
    locked = conn.execute(
        text("SELECT count(*) FROM lessons WHERE chapter_id = :c AND status <> 'draft'"), {"c": chapter_id}
    ).scalar_one()
    if locked:
        raise IngestError(
            f"le chapitre '{chapter.code}' contient {locked} leçon(s) validée(s) : rechargement refusé "
            "(créer une nouvelle version à la main plutôt que d'écraser un contenu validé)"
        )
    conn.execute(text("DELETE FROM lessons WHERE chapter_id = :c"), {"c": chapter_id})  # cascade : chunks
    n_chunks = 0
    for pos, s in enumerate(chapter.sections, start=1):
        ref = f"{chapter.source}" + (f" p.{s.page}" if s.page else "")
        lesson_id = conn.execute(
            text(
                "INSERT INTO lessons (chapter_id, kind, position, title, body_md, source_ref) "
                "VALUES (:c, :k, :p, :t, :b, :r) RETURNING id"
            ),
            {"c": chapter_id, "k": s.kind, "p": pos, "t": s.title, "b": s.body, "r": ref},
        ).scalar_one()
        for i, ch in enumerate(chunk_text(s.body), start=1):
            conn.execute(
                text(
                    "INSERT INTO content_chunks (lesson_id, chapter_id, position, content) "
                    "VALUES (:l, :c, :p, :t)"
                ),
                {"l": lesson_id, "c": chapter_id, "p": i, "t": f"{s.title}\n\n{ch}"},
            )
            n_chunks += 1
    return {"chapter_id": chapter_id, "lessons": len(chapter.sections), "chunks": n_chunks}


def ingest_dir(conn: Connection, directory: Path) -> list[dict[str, int]]:
    results = []
    for path in sorted(directory.glob("*.md")):
        try:
            chapter = parse_chapter(path.read_text(encoding="utf-8"))
            results.append({"file": path.name, **load_chapter(conn, chapter)})  # type: ignore[dict-item]
        except IngestError as e:
            raise IngestError(f"{path.name} : {e}") from e
    return results
