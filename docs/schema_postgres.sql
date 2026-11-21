-- Flat-Watch – target PostgreSQL 16 + PostGIS schema (production).
-- The SQLite schema in flatwatch/db.py maps 1:1 onto this; migrate with Alembic.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pg_trgm;          -- fuzzy duplicate detection on titles

CREATE TYPE user_role       AS ENUM ('tenant', 'landlord', 'admin');
CREATE TYPE listing_status  AS ENUM ('draft', 'active', 'expired', 'removed', 'queued');
CREATE TYPE offer_type      AS ENUM ('apartment', 'room', 'bed');
CREATE TYPE email_frequency AS ENUM ('instant', 'daily', 'weekly', 'off');

CREATE TABLE users (
    id              bigserial PRIMARY KEY,
    email           citext NOT NULL UNIQUE,
    name            text NOT NULL,
    role            user_role NOT NULL,
    pw_hash         text NOT NULL,                       -- scrypt / argon2id
    email_verified  boolean NOT NULL DEFAULT false,
    totp_secret_enc bytea,                               -- encrypted with a KMS key
    totp_enabled    boolean NOT NULL DEFAULT false,
    failed_logins   int NOT NULL DEFAULT 0,
    locked_until    timestamptz,
    identity_verified_at timestamptz,                    -- landlord ID check (provider reference only)
    email_opt_out   boolean NOT NULL DEFAULT false,
    unsub_token     text UNIQUE,
    created_at      timestamptz NOT NULL DEFAULT now(),
    last_login_at   timestamptz,
    deleted_at      timestamptz
);

CREATE TABLE one_time_codes (                            -- email verification / password reset
    user_id    bigint REFERENCES users(id) ON DELETE CASCADE,
    purpose    text NOT NULL CHECK (purpose IN ('verify', 'reset')),
    code_hash  text NOT NULL,
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (user_id, purpose)
);

CREATE TABLE sessions (
    token_hash   text PRIMARY KEY,
    user_id      bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    user_agent   text, ip_hash text
);
CREATE INDEX ON sessions(user_id);

CREATE TABLE tenant_profiles (
    user_id    bigint PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    criteria   jsonb NOT NULL,                           -- validated by the Criteria model; no nationality
    published  boolean NOT NULL DEFAULT true,
    campus_geom geography(Point, 4326),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE saved_searches (
    id                bigserial PRIMARY KEY,
    user_id           bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name              text NOT NULL,
    criteria          jsonb NOT NULL,
    active            boolean NOT NULL DEFAULT true,
    frequency         email_frequency NOT NULL DEFAULT 'instant',
    instant_threshold int NOT NULL DEFAULT 85,
    min_score         int NOT NULL DEFAULT 65,
    created_at        timestamptz NOT NULL DEFAULT now(),
    last_run_at       timestamptz, last_digest_at timestamptz
);
CREATE INDEX ON saved_searches(active, frequency);

CREATE TABLE listings (
    id               text PRIMARY KEY,
    owner_id         bigint REFERENCES users(id) ON DELETE SET NULL,
    source           text NOT NULL,                      -- 'flat-watch', 'partner:<name>', 'pasted:<site>'
    source_ref       text,                               -- id at the source
    status           listing_status NOT NULL DEFAULT 'active',
    offer_type       offer_type NOT NULL,
    title            text NOT NULL,
    description      text NOT NULL,
    price_huf        int NOT NULL CHECK (price_huf > 0),
    currency         char(3) NOT NULL DEFAULT 'HUF',
    utilities_huf    int, utilities_included boolean, deposit_huf int,
    size_m2          int, rooms numeric(3,1), beds_in_room int,
    furnished        boolean, pets_ok boolean, smoking_ok boolean,
    available_from   date,
    amenities        text[] NOT NULL DEFAULT '{}',
    pref_genders     text[] NOT NULL DEFAULT '{}',       -- only allowed when offer_type <> 'apartment'
    pref_languages   text[] NOT NULL DEFAULT '{}',
    address_private  text,                               -- full address, never shown publicly
    district         text,
    geom             geography(Point, 4326),
    fingerprint      text,
    duplicate_of     text REFERENCES listings(id),
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    last_verified_at timestamptz NOT NULL DEFAULT now(),
    reminder_sent_at timestamptz,
    CONSTRAINT gender_only_for_shared CHECK (offer_type <> 'apartment' OR cardinality(pref_genders) = 0)
);
CREATE INDEX ON listings USING gist (geom);
CREATE INDEX ON listings (status, offer_type, price_huf);
CREATE INDEX ON listings (owner_id);
CREATE INDEX ON listings USING gin (title gin_trgm_ops);
CREATE UNIQUE INDEX ON listings (source, source_ref) WHERE source_ref IS NOT NULL;

CREATE TABLE price_history (
    id bigserial PRIMARY KEY,
    listing_id text NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    price_huf int NOT NULL, changed_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE photos (
    id         bigserial PRIMARY KEY,
    listing_id text NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    object_key text NOT NULL,                            -- S3 / R2 key; served via CDN
    thumb_key  text NOT NULL,
    category   text NOT NULL DEFAULT 'Other',
    width int, height int, bytes int,
    sort int NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON photos(listing_id, sort);

CREATE TABLE favorites (
    user_id bigint REFERENCES users(id) ON DELETE CASCADE,
    listing_id text REFERENCES listings(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, listing_id)
);

CREATE TABLE feedback (
    id bigserial PRIMARY KEY,
    user_id bigint REFERENCES users(id) ON DELETE CASCADE,
    listing_id text REFERENCES listings(id) ON DELETE CASCADE,
    verdict text NOT NULL CHECK (verdict IN ('like', 'dislike')),
    reason text, created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE conversations (
    id bigserial PRIMARY KEY,
    listing_id text NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    tenant_id bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    landlord_id bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_message_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (listing_id, tenant_id)
);
CREATE TABLE messages (
    id bigserial PRIMARY KEY,
    conversation_id bigint NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    sender_id bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    body text NOT NULL CHECK (length(body) <= 2000),
    flags text[] NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(), read_at timestamptz
);
CREATE INDEX ON messages(conversation_id, created_at);

CREATE TABLE blocks (blocker_id bigint REFERENCES users(id) ON DELETE CASCADE,
                     blocked_id bigint REFERENCES users(id) ON DELETE CASCADE,
                     created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (blocker_id, blocked_id));
CREATE TABLE reports (id bigserial PRIMARY KEY, reporter_id bigint REFERENCES users(id) ON DELETE SET NULL,
                      target_user_id bigint REFERENCES users(id) ON DELETE SET NULL,
                      conversation_id bigint, listing_id text, reason text NOT NULL,
                      status text NOT NULL DEFAULT 'open', created_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE notified (
    search_id bigint REFERENCES saved_searches(id) ON DELETE CASCADE,
    listing_id text REFERENCES listings(id) ON DELETE CASCADE,
    score int NOT NULL, channel text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (search_id, listing_id)                  -- duplicate-notification guard
);

CREATE TABLE email_queue (
    id bigserial PRIMARY KEY, to_email citext NOT NULL, subject text NOT NULL,
    html text NOT NULL, text text NOT NULL,
    status text NOT NULL DEFAULT 'pending', attempts int NOT NULL DEFAULT 0,
    dedupe_key text UNIQUE, created_at timestamptz NOT NULL DEFAULT now(), sent_at timestamptz, error text
);
CREATE INDEX ON email_queue(status) WHERE status = 'pending';

CREATE TABLE audit_log (id bigserial PRIMARY KEY, user_id bigint, action text NOT NULL, detail text,
                        created_at timestamptz NOT NULL DEFAULT now());

-- Example PostGIS query: active rooms within 2 km of the main building
-- SELECT id, title FROM listings
--  WHERE status = 'active' AND offer_type = 'room'
--    AND ST_DWithin(geom, ST_MakePoint(21.6215, 47.5536)::geography, 2000);
