alter table metadata.source_release
    add column retrieval_filters jsonb not null default '{}'::jsonb,
    add constraint source_release_retrieval_filters_object
        check (jsonb_typeof(retrieval_filters) = 'object');

comment on column metadata.source_release.retrieval_filters is
    'Exact source-side filters recorded by the committed manifest. An absent field means the retained release was not filtered on that field; it does not imply that every upstream population is present.';
