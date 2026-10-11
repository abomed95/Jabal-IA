# Contenu pédagogique

`content/source/*.md` contient les chapitres transcrits (Markdown + LaTeX). Ces fichiers **ne sont jamais
versionnés** (droits d'auteur, dépôt public) : voir `.gitignore`.

## Source des énoncés

Les énoncés viennent du **manuel officiel « Mathématiques 1re S » (CRIPEN, Djibouti, 2014)**, dont les corrigés
fournis suivent la numérotation. Dossier Drive de l'inspection : « Manuels Général / Manuels Première ».
Les exercices sont chargés par paires `### Énoncé` / `### Corrigé` (sections `[qcm]`, `[exercice]`,
`[probleme]`) ; chaque corrigé est recalculé avant chargement. Les écarts trouvés vont dans
`content/review/` (jamais versionné) pour relecture par un enseignant.

## Ce que contiennent les PDF fournis

Les 9 PDF « Chap… » sont des **corrigés seuls** (réponses, tableaux de variation, captures de calculatrice,
graphiques) : les énoncés sont dans le manuel. On les associe donc au manuel officiel pour obtenir des exercices complets
(énoncé + corrigé), base du jeu d'essai de 150 questions.

## Chaîne d'ingestion

1. Lire les pages en image (le calque texte des PDF est inexploitable pour les formules) et transcrire
   chaque section sous la forme `## [type] Titre | p.N`.
2. `python -m app.cli ingest --dir ../../content/source` (depuis `apps/api`) : validation (formules fermées,
   types connus, en-tête complet) puis chargement **en brouillon**, tout ou rien.
3. Un enseignant relit et valide (`status = validated`, `validated_at`). Un chapitre dont une leçon est
   validée ne peut plus être rechargé automatiquement.
4. Vérifier systématiquement les calculs transcrits : une erreur de signe a déjà été trouvée à la relecture
   du pilote (maximum/minimum, chapitre 1).
