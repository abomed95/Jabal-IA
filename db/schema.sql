-- ============================================================================
-- BASELINE 0001 — figé. Ne plus modifier ce fichier : toute évolution du schéma
-- passe par une migration Alembic (apps/api/migrations/versions/).
-- ============================================================================
-- ============================================================================
-- Jabal IA — schéma PostgreSQL 15+ (testé sur PostgreSQL 16)
-- Conventions : code et colonnes en anglais, interface en français.
-- Correspondance avec le cahier des charges : statut→status, date_debut→starts_at,
-- date_fin→ends_at, methode_paiement→payment_method, montant_djf→amount_djf.
-- Le backend FastAPI est le SEUL client de la base (rôle propriétaire / service_role).
-- RLS activée partout sans politique = accès direct refusé aux rôles anon/authenticated.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS btree_gist;   -- contrainte d'exclusion sur les périodes
CREATE EXTENSION IF NOT EXISTS unaccent;     -- recherche plein texte sans accents
-- CREATE EXTENSION IF NOT EXISTS vector;    -- V2 : pgvector, seulement si le rappel FTS < 90 %

-- ---------- Types -----------------------------------------------------------
CREATE TYPE user_role      AS ENUM ('student', 'guardian', 'admin');
CREATE TYPE class_level    AS ENUM ('premiere_s', 'terminale_s');
CREATE TYPE sub_status     AS ENUM ('pending', 'active', 'grace', 'expired', 'cancelled', 'refunded');
CREATE TYPE pay_provider   AS ENUM ('waafi', 'dmoney', 'card', 'manual', 'voucher');
CREATE TYPE pay_status     AS ENUM ('initiated', 'pending_confirmation', 'awaiting_review',
                                    'succeeded', 'failed', 'expired', 'refunded');
CREATE TYPE content_status AS ENUM ('draft', 'validated', 'published', 'retired');
CREATE TYPE lesson_kind    AS ENUM ('diagnostic', 'activite', 'cours', 'methode', 'tp', 'exercice_corrige');
CREATE TYPE exercise_kind  AS ENUM ('qcm', 'exercice', 'probleme');
CREATE TYPE msg_role       AS ENUM ('user', 'assistant', 'tool');
CREATE TYPE verif_status   AS ENUM ('not_applicable', 'verified', 'unverified', 'failed');

-- Recherche sans accents (les élèves tapent sans accents sur mobile).
-- unaccent() n'est pas IMMUTABLE : on l'encapsule pour pouvoir l'utiliser dans une colonne générée.
CREATE FUNCTION immutable_unaccent(text) RETURNS text
  LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
  AS $$ SELECT public.unaccent('public.unaccent', $1) $$;

-- ---------- Utilisateurs ----------------------------------------------------
CREATE TABLE users (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  role          user_role   NOT NULL,
  full_name     text        NOT NULL CHECK (length(full_name) BETWEEN 2 AND 120),
  phone_e164    text        NOT NULL CHECK (phone_e164 ~ '^\+[1-9][0-9]{7,14}$'),
  email         text,
  password_hash text        NOT NULL,                 -- argon2id, jamais de mot de passe en clair
  level         class_level,                           -- uniquement pour les élèves
  school        text,
  locale        text        NOT NULL DEFAULT 'fr',
  phone_verified_at timestamptz,                       -- posé à la 1re transaction mobile money réussie
  is_active     boolean     NOT NULL DEFAULT true,
  created_at    timestamptz NOT NULL DEFAULT now(),
  deleted_at    timestamptz,
  CONSTRAINT users_level_only_students CHECK ((role = 'student') = (level IS NOT NULL))
);
-- Un même numéro peut être celui d'un parent ET de ses enfants : unicité par (téléphone, rôle, nom).
CREATE UNIQUE INDEX users_phone_role_name_uq ON users (phone_e164, role, lower(full_name)) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX users_email_uq ON users (lower(email)) WHERE email IS NOT NULL AND deleted_at IS NULL;

CREATE TABLE guardians (                               -- lien parent/tuteur ↔ élève
  student_id  uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  guardian_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  relation    text,
  consent_at  timestamptz,                             -- consentement parental (mineurs)
  PRIMARY KEY (student_id, guardian_id),
  CHECK (student_id <> guardian_id)
);
CREATE INDEX guardians_guardian_idx ON guardians (guardian_id);

CREATE TABLE devices (                                 -- limite de 2 appareils actifs par compte
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  label        text,
  token_hash   text NOT NULL UNIQUE,                   -- hash du refresh token lié à l'appareil
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at  timestamptz NOT NULL DEFAULT now(),
  revoked_at   timestamptz
);
CREATE INDEX devices_user_active_idx ON devices (user_id) WHERE revoked_at IS NULL;

CREATE FUNCTION enforce_device_limit() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.revoked_at IS NULL AND
     (SELECT count(*) FROM devices WHERE user_id = NEW.user_id AND revoked_at IS NULL) >= 2 THEN
    RAISE EXCEPTION 'device_limit_reached' USING ERRCODE = 'P0001';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER devices_limit BEFORE INSERT ON devices
  FOR EACH ROW EXECUTE FUNCTION enforce_device_limit();

-- ---------- Offre, abonnements, paiements -----------------------------------
CREATE TABLE plans (
  id            smallserial PRIMARY KEY,
  code          text NOT NULL UNIQUE,                  -- 'monthly_2000'
  name          text NOT NULL,
  price_djf     integer NOT NULL CHECK (price_djf > 0),
  duration_days integer NOT NULL CHECK (duration_days > 0),
  grace_days    integer NOT NULL DEFAULT 3 CHECK (grace_days >= 0),
  is_active     boolean NOT NULL DEFAULT true
);
INSERT INTO plans (code, name, price_djf, duration_days) VALUES ('monthly_2000', 'Jabal IA — 30 jours', 2000, 30);

CREATE TABLE subscriptions (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  student_id     uuid NOT NULL REFERENCES users(id),   -- bénéficiaire
  payer_id       uuid REFERENCES users(id),            -- parent ou élève qui paie
  plan_id        smallint NOT NULL REFERENCES plans(id),
  status         sub_status NOT NULL DEFAULT 'pending',
  starts_at      timestamptz,                          -- date_debut
  ends_at        timestamptz,                          -- date_fin
  payment_method pay_provider,                         -- methode_paiement
  amount_djf     integer NOT NULL CHECK (amount_djf >= 0),  -- montant_djf (0 possible : essai / bon offert)
  created_at     timestamptz NOT NULL DEFAULT now(),
  cancelled_at   timestamptz,
  CONSTRAINT sub_dates_ok CHECK (ends_at IS NULL OR starts_at IS NULL OR ends_at > starts_at),
  CONSTRAINT sub_dates_required CHECK (status IN ('pending','cancelled') OR (starts_at IS NOT NULL AND ends_at IS NOT NULL)),
  -- Pas de chevauchement de périodes payantes pour un même élève : un renouvellement anticipé
  -- démarre à la fin de la période en cours ([) = bornes adjacentes autorisées).
  CONSTRAINT sub_no_overlap EXCLUDE USING gist (
    student_id WITH =,
    tstzrange(starts_at, ends_at, '[)') WITH &&
  ) WHERE (status IN ('active', 'grace'))
);
CREATE INDEX subscriptions_student_idx ON subscriptions (student_id, ends_at DESC);
CREATE INDEX subscriptions_expiring_idx ON subscriptions (ends_at) WHERE status IN ('active', 'grace');

CREATE TABLE payments (                                -- une ligne par tentative / transaction
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  subscription_id  uuid NOT NULL REFERENCES subscriptions(id),
  payer_id         uuid REFERENCES users(id),
  provider         pay_provider NOT NULL,
  status           pay_status   NOT NULL DEFAULT 'initiated',
  amount_djf       integer NOT NULL CHECK (amount_djf > 0),
  currency         char(3) NOT NULL DEFAULT 'DJF' CHECK (currency = 'DJF'),
  payer_msisdn     text,                                -- numéro qui paie (peut différer de l'élève)
  provider_ref     text,                                -- id de transaction côté opérateur
  idempotency_key  text NOT NULL UNIQUE,                -- généré par NOTRE backend à l'initiation
  failure_code     text,
  initiated_at     timestamptz NOT NULL DEFAULT now(),
  confirmed_at     timestamptz,
  activated_at     timestamptz,                         -- posé une seule fois par activate_payment()
  last_polled_at   timestamptz,
  reviewed_by      uuid REFERENCES users(id),           -- repli manuel : admin qui a validé
  reviewed_at      timestamptz,
  CONSTRAINT pay_ref_unique UNIQUE (provider, provider_ref)   -- NULL autorisés plusieurs fois
);
CREATE INDEX payments_pending_idx ON payments (initiated_at)
  WHERE status IN ('initiated', 'pending_confirmation', 'awaiting_review');
CREATE INDEX payments_subscription_idx ON payments (subscription_id);

CREATE TABLE payment_events (                          -- journal brut, append-only, déduplication
  id            bigserial PRIMARY KEY,
  provider      pay_provider NOT NULL,
  event_key     text NOT NULL,                          -- id d'événement opérateur, ou hash du corps
  payment_id    uuid REFERENCES payments(id),
  payload       jsonb NOT NULL,
  signature_ok  boolean,
  received_at   timestamptz NOT NULL DEFAULT now(),
  processed_at  timestamptz,
  error         text,
  UNIQUE (provider, event_key)
);

CREATE TABLE vouchers (                                -- codes / QR d'activation prépayés (agents, écoles)
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code_hash    text NOT NULL UNIQUE,                    -- le code en clair n'est jamais stocké
  plan_id      smallint NOT NULL REFERENCES plans(id),
  batch        text NOT NULL,
  expires_at   timestamptz NOT NULL,
  redeemed_by  uuid REFERENCES users(id),
  redeemed_at  timestamptz,
  CHECK ((redeemed_by IS NULL) = (redeemed_at IS NULL))
);

-- Fonction d'activation : idempotente, verrouillée, empilement des périodes.
CREATE FUNCTION activate_payment(p_payment_id uuid) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE
  pay   payments%ROWTYPE;
  sub   subscriptions%ROWTYPE;
  pl    plans%ROWTYPE;
  v_start timestamptz;
BEGIN
  SELECT * INTO pay FROM payments WHERE id = p_payment_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'payment_not_found'; END IF;
  IF pay.status <> 'succeeded' THEN RAISE EXCEPTION 'payment_not_succeeded'; END IF;
  IF pay.activated_at IS NOT NULL THEN RETURN pay.subscription_id; END IF;   -- rejeu : sans effet

  SELECT * INTO sub FROM subscriptions WHERE id = pay.subscription_id FOR UPDATE;
  SELECT * INTO pl  FROM plans WHERE id = sub.plan_id;
  IF pay.amount_djf <> pl.price_djf THEN RAISE EXCEPTION 'amount_mismatch'; END IF;

  -- le nouvel abonnement démarre à la fin de la période payée la plus lointaine, sinon maintenant
  SELECT greatest(now(), coalesce(max(ends_at), now())) INTO v_start
    FROM subscriptions
   WHERE student_id = sub.student_id AND id <> sub.id AND status IN ('active', 'grace');

  UPDATE subscriptions
     SET status = 'active', starts_at = v_start,
         ends_at = v_start + make_interval(days => pl.duration_days),
         payment_method = pay.provider, amount_djf = pay.amount_djf
   WHERE id = sub.id;
  UPDATE payments SET activated_at = now() WHERE id = pay.id;
  UPDATE users SET phone_verified_at = coalesce(phone_verified_at, now())
   WHERE id = pay.payer_id AND pay.provider IN ('waafi', 'dmoney');
  RETURN sub.id;
END $$;

-- Contrôle d'accès : source de vérité = les dates (le statut est mis à jour par un job, pas fiable à la seconde).
CREATE FUNCTION has_access(p_student uuid) RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT EXISTS (
    SELECT 1 FROM subscriptions s JOIN plans p ON p.id = s.plan_id
     WHERE s.student_id = p_student
       AND s.status IN ('active', 'grace')
       AND s.starts_at <= now()
       AND now() < s.ends_at + make_interval(days => p.grace_days)
  )
$$;

-- ---------- Contenu pédagogique ---------------------------------------------
CREATE TABLE chapters (
  id          serial PRIMARY KEY,
  level       class_level NOT NULL,
  position    smallint NOT NULL,
  code        text NOT NULL,                            -- 'derivation'
  title       text NOT NULL,
  domain      text NOT NULL CHECK (domain IN ('analyse', 'geometrie', 'probabilites_statistiques', 'arithmetique', 'nombres_complexes')),
  free_preview boolean NOT NULL DEFAULT false,          -- 1 chapitre gratuit pour la conversion
  status      content_status NOT NULL DEFAULT 'draft',
  UNIQUE (level, code), UNIQUE (level, position)
);

CREATE TABLE lessons (
  id           serial PRIMARY KEY,
  chapter_id   integer NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
  kind         lesson_kind NOT NULL,
  position     smallint NOT NULL DEFAULT 0,
  title        text NOT NULL,
  body_md      text NOT NULL,                           -- Markdown + LaTeX ($...$), rendu KaTeX
  source_ref   text,                                    -- 'Chap8_derivation.pdf p.12'
  version      integer NOT NULL DEFAULT 1,
  status       content_status NOT NULL DEFAULT 'draft',
  validated_by uuid REFERENCES users(id),
  validated_at timestamptz,
  CONSTRAINT lessons_validation CHECK (status NOT IN ('validated', 'published') OR validated_at IS NOT NULL)
);
CREATE INDEX lessons_chapter_idx ON lessons (chapter_id, position);

CREATE TABLE content_chunks (                           -- unité de récupération du tuteur (par section)
  id         bigserial PRIMARY KEY,
  lesson_id  integer NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
  chapter_id integer NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
  position   smallint NOT NULL,
  content    text NOT NULL,
  tsv        tsvector GENERATED ALWAYS AS (to_tsvector('french', immutable_unaccent(content))) STORED
  -- embedding vector(1024)                              -- V2, si nécessaire
);
CREATE INDEX content_chunks_tsv_idx ON content_chunks USING gin (tsv);
CREATE INDEX content_chunks_chapter_idx ON content_chunks (chapter_id);

CREATE TABLE exercises (
  id           serial PRIMARY KEY,
  chapter_id   integer NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
  lesson_id    integer REFERENCES lessons(id) ON DELETE SET NULL,
  kind         exercise_kind NOT NULL,
  statement_md text NOT NULL,
  difficulty   smallint NOT NULL DEFAULT 2 CHECK (difficulty BETWEEN 1 AND 5),
  competency   text,
  solution_md  text,                                    -- jamais envoyé au client avant tentative
  answer_spec  jsonb,                                   -- ex. {"type":"expr","value":"2*x+1","check":"sympy_equiv"}
  hints_md     jsonb,                                   -- indices pré-générés ET validés (servis hors ligne)
  source_ref   text,
  status       content_status NOT NULL DEFAULT 'draft'
);
CREATE INDEX exercises_chapter_idx ON exercises (chapter_id, difficulty);

CREATE TABLE attempts (
  id           bigserial PRIMARY KEY,
  student_id   uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  exercise_id  integer NOT NULL REFERENCES exercises(id) ON DELETE CASCADE,
  client_uuid  uuid NOT NULL,                           -- idempotence de la synchro hors ligne
  answer       jsonb NOT NULL,
  is_correct   boolean,
  hints_used   smallint NOT NULL DEFAULT 0,
  time_spent_s integer,
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (student_id, client_uuid)
);
CREATE INDEX attempts_student_idx ON attempts (student_id, created_at DESC);

CREATE TABLE progress (
  student_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  chapter_id     integer NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
  mastery        numeric(4,3) NOT NULL DEFAULT 0 CHECK (mastery BETWEEN 0 AND 1),
  lessons_done   smallint NOT NULL DEFAULT 0,
  last_activity_at timestamptz,
  PRIMARY KEY (student_id, chapter_id)
);

-- ---------- Tuteur IA --------------------------------------------------------
CREATE TABLE ai_conversations (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  student_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  chapter_id      integer REFERENCES chapters(id),
  started_at      timestamptz NOT NULL DEFAULT now(),
  last_message_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ai_conv_student_idx ON ai_conversations (student_id, last_message_at DESC);

CREATE TABLE ai_messages (
  id              bigserial PRIMARY KEY,
  conversation_id uuid NOT NULL REFERENCES ai_conversations(id) ON DELETE CASCADE,
  client_msg_id   uuid,                                 -- idempotence de la file d'attente hors ligne
  role            msg_role NOT NULL,
  content         text NOT NULL,
  verification    verif_status NOT NULL DEFAULT 'not_applicable',
  model           text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (conversation_id, client_msg_id)
);
CREATE INDEX ai_msg_conv_idx ON ai_messages (conversation_id, id);

CREATE TABLE ai_usage (                                  -- 1 ligne par appel LLM : quotas + coûts réels
  id                bigserial PRIMARY KEY,
  student_id        uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  message_id        bigint REFERENCES ai_messages(id) ON DELETE SET NULL,
  model             text NOT NULL,
  input_tokens      integer NOT NULL DEFAULT 0,
  cache_read_tokens integer NOT NULL DEFAULT 0,
  cache_write_tokens integer NOT NULL DEFAULT 0,
  output_tokens     integer NOT NULL DEFAULT 0,
  cost_usd_micro    bigint  NOT NULL DEFAULT 0,           -- millionièmes de USD (entier, pas de flottants)
  latency_ms        integer,
  created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ai_usage_student_day_idx ON ai_usage (student_id, created_at DESC);

-- ---------- Exploitation -----------------------------------------------------
CREATE TABLE notification_outbox (                       -- rappels d'échéance, reçus (envoi asynchrone)
  id          bigserial PRIMARY KEY,
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  channel     text NOT NULL CHECK (channel IN ('whatsapp', 'sms', 'push')),
  template    text NOT NULL,
  payload     jsonb NOT NULL DEFAULT '{}',
  send_after  timestamptz NOT NULL DEFAULT now(),
  sent_at     timestamptz,
  error       text
);
CREATE INDEX outbox_due_idx ON notification_outbox (send_after) WHERE sent_at IS NULL;

CREATE TABLE admin_audit_log (                           -- traçabilité des validations manuelles
  id        bigserial PRIMARY KEY,
  admin_id  uuid NOT NULL REFERENCES users(id),
  action    text NOT NULL,
  entity    text NOT NULL,
  entity_id text NOT NULL,
  details   jsonb NOT NULL DEFAULT '{}',
  at        timestamptz NOT NULL DEFAULT now()
);

-- ---------- Sécurité : RLS deny-by-default ------------------------------------
DO $$
DECLARE t text;
BEGIN
  FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
  END LOOP;
END $$;
-- Aucune politique créée : seuls le propriétaire des tables et service_role (qui contournent la RLS) y accèdent.
-- Si un jour un accès direct depuis le client est voulu (Supabase JS), ajouter des politiques explicites, ex. :
--   CREATE POLICY own_progress ON progress FOR SELECT USING (student_id = auth.uid());
