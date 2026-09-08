create schema if not exists metadata;
create schema if not exists stage;
create schema if not exists reference;
create schema if not exists claims;
create schema if not exists analytics;

create table if not exists metadata.schema_migration (
    version text primary key,
    sha256 char(64) not null,
    applied_at timestamptz not null default now()
);

create table metadata.source_dataset (
    dataset_id bigint generated always as identity primary key,
    source text not null,
    slug text not null,
    name text not null,
    landing_page text,
    notes text,
    created_at timestamptz not null default now(),
    unique (source, slug)
);

create table metadata.source_release (
    source_release_id bigint generated always as identity primary key,
    dataset_id bigint not null references metadata.source_dataset(dataset_id),
    data_year smallint,
    version_id text,
    published_at date,
    modified_at date,
    accessed_at date not null,
    population text not null,
    aggregation_keys jsonb not null default '[]'::jsonb,
    suppression text,
    exclusions jsonb not null default '[]'::jsonb,
    status text not null default 'discovered'
        check (status in ('discovered', 'acquired', 'validated', 'loaded', 'superseded')),
    created_at timestamptz not null default now()
);

create unique index source_release_identity
    on metadata.source_release (
        dataset_id,
        coalesce(data_year, -1),
        coalesce(version_id, '')
    );

create table metadata.file_artifact (
    file_artifact_id bigint generated always as identity primary key,
    source_release_id bigint not null
        references metadata.source_release(source_release_id),
    relative_path text not null,
    filename text not null,
    bytes bigint check (bytes is null or bytes >= 0),
    sha256 char(64) not null,
    format text not null,
    downloaded_at timestamptz not null,
    validation jsonb not null default '{}'::jsonb,
    unique (sha256)
);

create table metadata.ingestion_run (
    ingestion_run_id bigint generated always as identity primary key,
    source_release_id bigint not null
        references metadata.source_release(source_release_id),
    file_artifact_id bigint references metadata.file_artifact(file_artifact_id),
    case_id text check (case_id is null or case_id ~ '^CASE-[0-9]{4}$'),
    pipeline_version text not null,
    code_commit char(40) not null,
    started_at timestamptz not null default now(),
    completed_at timestamptz,
    status text not null default 'running'
        check (status in ('running', 'succeeded', 'failed', 'cancelled')),
    source_rows bigint check (source_rows is null or source_rows >= 0),
    loaded_rows bigint check (loaded_rows is null or loaded_rows >= 0),
    rejected_rows bigint check (rejected_rows is null or rejected_rows >= 0),
    error_summary text
);

create table metadata.data_quality_result (
    data_quality_result_id bigint generated always as identity primary key,
    ingestion_run_id bigint not null
        references metadata.ingestion_run(ingestion_run_id) on delete cascade,
    check_name text not null,
    status text not null check (status in ('pass', 'warn', 'fail')),
    observed jsonb not null default '{}'::jsonb,
    details text,
    created_at timestamptz not null default now(),
    unique (ingestion_run_id, check_name)
);

create table analytics.analysis_run (
    analysis_run_id bigint generated always as identity primary key,
    case_id text check (case_id is null or case_id ~ '^CASE-[0-9]{4}$'),
    name text not null,
    code_commit char(40) not null,
    query_path text not null,
    parameters jsonb not null default '{}'::jsonb,
    source_release_ids bigint[] not null default '{}',
    status text not null default 'running'
        check (status in ('running', 'succeeded', 'failed', 'cancelled')),
    started_at timestamptz not null default now(),
    completed_at timestamptz,
    notes text
);

create table analytics.analysis_artifact (
    analysis_artifact_id bigint generated always as identity primary key,
    analysis_run_id bigint not null
        references analytics.analysis_run(analysis_run_id) on delete cascade,
    relative_path text not null,
    media_type text not null,
    sha256 char(64) not null,
    description text,
    created_at timestamptz not null default now(),
    unique (analysis_run_id, relative_path)
);

comment on schema stage is
    'Transient load tables. Rebuild from immutable source files; do not treat as canonical.';
comment on schema reference is
    'Versioned provider, drug, code-system, Census, and geographic reference data.';
comment on schema claims is
    'Normalized public aggregate healthcare claims facts with source-release lineage.';
comment on schema analytics is
    'Derived peer groups, features, anomaly runs, and reproducible analysis artifacts.';
