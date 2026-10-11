# Base de données

- `schema.sql` : **baseline 0001, figée**. Elle est appliquée par la migration Alembic `0001`.
- Toute évolution passe par une nouvelle migration dans `apps/api/migrations/versions/`
  (exemple : `0002` ajoute `devices.client_device_id`).
- Le backend est le seul client de la base. La RLS est activée sur toutes les tables sans politique :
  un accès direct avec la clé publique de Supabase ne lit rien.
- Les fonctions SQL `activate_payment()` et `has_access()` portent les règles d'abonnement
  (idempotence, empilement des périodes, grâce de 3 jours). Ne les contournez pas depuis le code Python.

Appliquer les migrations :

```bash
cd apps/api && alembic upgrade head
```
