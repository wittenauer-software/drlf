alter table metadata.source_release
    add column observed_at timestamptz,
    add column manifest_path text,
    add column manifest_sha256 char(64);

update metadata.source_release
set observed_at = accessed_at::timestamp at time zone 'UTC'
where observed_at is null;

alter table metadata.source_release
    alter column observed_at set not null;

alter table metadata.source_release
    alter column modified_at type timestamptz
        using modified_at::timestamp at time zone 'UTC';

drop index metadata.source_release_identity;

create unique index source_release_observation_identity
    on metadata.source_release (
        dataset_id,
        coalesce(data_year, -1),
        coalesce(version_id, ''),
        observed_at
    );

create unique index source_release_manifest_path_identity
    on metadata.source_release (manifest_path)
    where manifest_path is not null;

create unique index source_release_manifest_hash_identity
    on metadata.source_release (manifest_sha256)
    where manifest_sha256 is not null;

alter table metadata.source_release
    add constraint source_release_manifest_sha256_format
        check (manifest_sha256 is null or manifest_sha256 ~ '^[0-9a-f]{64}$'),
    add constraint source_release_manifest_path_format
        check (
            manifest_path is null
            or manifest_path like 'research/data-manifests/%'
        ),
    add constraint source_release_manifest_required
        check (
            status = 'discovered'
            or (manifest_path is not null and manifest_sha256 is not null)
        ) not valid;

create table metadata.content_artifact (
    content_artifact_id bigint generated always as identity primary key,
    bytes bigint check (bytes is null or bytes >= 0),
    sha256 char(64) not null unique
        check (sha256 ~ '^[0-9a-f]{64}$'),
    media_type text not null,
    created_at timestamptz not null default now()
);

insert into metadata.content_artifact (bytes, sha256, media_type, created_at)
select bytes, sha256, format, downloaded_at
from metadata.file_artifact;

create table metadata.source_release_file (
    source_release_file_id bigint generated always as identity primary key,
    source_release_id bigint not null
        references metadata.source_release(source_release_id),
    content_artifact_id bigint not null
        references metadata.content_artifact(content_artifact_id),
    relative_path text not null,
    filename text not null,
    role text not null default 'data',
    ordinal integer not null default 1 check (ordinal > 0),
    retrieved_at timestamptz not null,
    validation jsonb not null default '{}'::jsonb,
    unique (source_release_file_id, source_release_id),
    unique (source_release_id, relative_path),
    unique (source_release_id, ordinal)
);

insert into metadata.source_release_file (
    source_release_id,
    content_artifact_id,
    relative_path,
    filename,
    ordinal,
    retrieved_at,
    validation
)
select
    old_file.source_release_id,
    content.content_artifact_id,
    old_file.relative_path,
    old_file.filename,
    row_number() over (
        partition by old_file.source_release_id
        order by old_file.file_artifact_id
    ),
    old_file.downloaded_at,
    old_file.validation
from metadata.file_artifact as old_file
join metadata.content_artifact as content using (sha256);

alter table metadata.ingestion_run
    add constraint ingestion_run_release_identity
        unique (ingestion_run_id, source_release_id);

create table metadata.ingestion_run_file (
    ingestion_run_id bigint not null,
    source_release_id bigint not null,
    source_release_file_id bigint not null,
    role text not null default 'input',
    ordinal integer not null default 1 check (ordinal > 0),
    primary key (ingestion_run_id, source_release_file_id),
    unique (ingestion_run_id, ordinal),
    foreign key (ingestion_run_id, source_release_id)
        references metadata.ingestion_run(ingestion_run_id, source_release_id)
        on delete cascade,
    foreign key (source_release_file_id, source_release_id)
        references metadata.source_release_file(source_release_file_id, source_release_id)
);

insert into metadata.ingestion_run_file (
    ingestion_run_id,
    source_release_id,
    source_release_file_id
)
select
    run.ingestion_run_id,
    run.source_release_id,
    release_file.source_release_file_id
from metadata.ingestion_run as run
join metadata.file_artifact as old_file
    on old_file.file_artifact_id = run.file_artifact_id
join metadata.content_artifact as content using (sha256)
join metadata.source_release_file as release_file
    on release_file.source_release_id = old_file.source_release_id
    and release_file.content_artifact_id = content.content_artifact_id
    and release_file.relative_path = old_file.relative_path
where run.file_artifact_id is not null;

alter table metadata.ingestion_run
    drop column file_artifact_id;

drop table metadata.file_artifact;

create table analytics.analysis_run_source_release (
    analysis_run_id bigint not null
        references analytics.analysis_run(analysis_run_id) on delete cascade,
    source_release_id bigint not null
        references metadata.source_release(source_release_id),
    role text not null default 'input',
    primary key (analysis_run_id, source_release_id)
);

create index analysis_run_source_release_release_idx
    on analytics.analysis_run_source_release (source_release_id);

insert into analytics.analysis_run_source_release (
    analysis_run_id,
    source_release_id
)
select distinct run.analysis_run_id, release_id
from analytics.analysis_run as run
cross join lateral unnest(run.source_release_ids) as release_id;

alter table analytics.analysis_run
    drop column source_release_ids;

comment on table metadata.content_artifact is
    'Content-addressed immutable files. Release-specific names and retrieval facts live in source_release_file.';
comment on table metadata.source_release_file is
    'Files observed in a source release, including snapshot path, retrieval time, role, and validation.';
comment on table metadata.ingestion_run_file is
    'One or more release files consumed by an ingestion run, constrained to the run source release.';
comment on table analytics.analysis_run_source_release is
    'Foreign-key-enforced source releases used by an analysis run.';
