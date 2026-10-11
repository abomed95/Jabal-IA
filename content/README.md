# Contenu pédagogique

`content/source/*.md` contient les chapitres transcrits (Markdown + LaTeX). Ces fichiers **ne sont jamais
versionnés** (droits d'auteur, dépôt public) : voir `.gitignore`.

## Ce que contiennent les PDF fournis

Les 9 PDF « Chap… » sont des **corrigés seuls** (réponses, tableaux de variation, captures de calculatrice,
graphiques) : les énoncés sont dans le manuel papier. Conséquence : ils alimentent les leçons
`exercice_corrige` / `activite` / `tp` et la recherche du tuteur, mais pas des exercices complets
(énoncé + solution) ni le jeu d'essai de 150 questions. Pour cela il faut les énoncés (manuel ou fichier
de l'enseignant).

## Chaîne d'ingestion

1. Lire les pages en image (le calque texte des PDF est inexploitable pour les formules) et transcrire
   chaque section sous la forme `## [type] Titre | p.N`.
2. `python -m app.cli ingest --dir ../../content/source` (depuis `apps/api`) : validation (formules fermées,
   types connus, en-tête complet) puis chargement **en brouillon**, tout ou rien.
3. Un enseignant relit et valide (`status = validated`, `validated_at`). Un chapitre dont une leçon est
   validée ne peut plus être rechargé automatiquement.
4. Vérifier systématiquement les calculs transcrits : une erreur de signe a déjà été trouvée à la relecture
   du pilote (maximum/minimum, chapitre 1).
