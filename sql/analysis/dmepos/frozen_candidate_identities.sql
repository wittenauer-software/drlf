-- Resolve identity fields only for the exact NPI/source-release pairs taken from a
-- previously frozen DMEPOS anonymous shortlist. The maintained CLI derives every
-- parameter from that validated artifact; this query must not be used as a broad
-- supplier identity export.
with settings as (
    select
        %(candidate_npis)s::text[] as candidate_npis,
        %(candidate_source_release_ids)s::bigint[] as candidate_source_release_ids,
        %(expected_data_year)s::smallint as expected_data_year,
        %(frozen_shortlist_sha256)s::text as frozen_shortlist_sha256,
        %(maximum_candidate_rows)s::integer as maximum_candidate_rows,
        %(source_release_ids)s::bigint[] as registered_source_release_ids
), parameter_gate as materialized (
    select 1 / case
        when cardinality(candidate_npis) = cardinality(candidate_source_release_ids)
          and cardinality(candidate_npis) <= maximum_candidate_rows
          and maximum_candidate_rows = 10000
          and expected_data_year = 2024
          and frozen_shortlist_sha256 ~ '^[0-9a-f]{64}$'
          and not exists (
              select 1
              from unnest(candidate_npis) as candidate_npi
              where candidate_npi !~ '^[0-9]{10}$'
          )
        then 1 else 0
    end as guard
    from settings
), requested_candidates as materialized (
    select
        candidate.supplier_npi,
        candidate.source_release_id,
        candidate.input_order
    from settings
    cross join parameter_gate
    cross join lateral unnest(
        settings.candidate_npis,
        settings.candidate_source_release_ids
    ) with ordinality as candidate(supplier_npi, source_release_id, input_order)
    where parameter_gate.guard = 1
), duplicate_candidates as (
    select supplier_npi
    from requested_candidates
    group by supplier_npi
    having count(*) > 1
), registered_releases as materialized (
    select
        registered.source_release_id,
        release.data_year,
        release.version_id,
        release.status,
        dataset.slug,
        dataset.source as dataset_source
    from settings
    cross join lateral unnest(settings.registered_source_release_ids)
        as registered(source_release_id)
    left join metadata.source_release as release
        on release.source_release_id = registered.source_release_id
    left join metadata.source_dataset as dataset
        on dataset.dataset_id = release.dataset_id
), scope_gate as materialized (
    select 1 / case
        when not exists (select 1 from duplicate_candidates)
          and cardinality(settings.registered_source_release_ids) = (
              select count(distinct registered.source_release_id)
              from registered_releases as registered
          )
          and cardinality(settings.registered_source_release_ids) = case
              when cardinality(settings.candidate_npis) = 0 then 0 else 1
          end
          and cardinality(settings.registered_source_release_ids) = (
              select count(distinct candidate.source_release_id)
              from requested_candidates as candidate
          )
          and not exists (
              select 1
              from requested_candidates as candidate
              where not (candidate.source_release_id = any(settings.registered_source_release_ids))
          )
          and not exists (
              select 1
              from registered_releases as registered
              where registered.status is distinct from 'loaded'
                 or registered.dataset_source is distinct from
                    'Centers for Medicare & Medicaid Services'
                 or registered.slug is distinct from
                    'medicare-durable-medical-equipment-devices-supplies-by-supplier'
                 or registered.data_year is distinct from settings.expected_data_year
                 or registered.version_id is distinct from
                    '4c6cfc68-a149-4bfe-b934-db2b92d0f360'
          )
        then 1 else 0
    end as guard
    from settings
), resolved_candidates as materialized (
    select
        supplier.data_year,
        supplier.supplier_npi,
        supplier.source_release_id,
        supplier.supplier_last_org_name,
        supplier.supplier_first_name,
        supplier.supplier_city,
        supplier.supplier_state
    from requested_candidates as candidate
    join claims.dmepos_supplier as supplier
        on supplier.source_release_id = candidate.source_release_id
        and supplier.supplier_npi = candidate.supplier_npi
    cross join settings
    cross join scope_gate
    where supplier.data_year = settings.expected_data_year
      and scope_gate.guard = 1
), resolution_gate as materialized (
    select 1 / case
        when (select count(*) from resolved_candidates)
            = (select count(*) from requested_candidates)
        then 1 else 0
    end as guard
), validation_anchor as materialized (
    select
        parameter_gate.guard as parameter_guard,
        scope_gate.guard as scope_guard,
        resolution_gate.guard as resolution_guard
    from parameter_gate
    cross join scope_gate
    cross join resolution_gate
), validated_output as materialized (
    select
        resolved.data_year,
        resolved.supplier_npi,
        resolved.source_release_id,
        resolved.supplier_last_org_name,
        resolved.supplier_first_name,
        resolved.supplier_city,
        resolved.supplier_state,
        anchor.parameter_guard,
        anchor.scope_guard,
        anchor.resolution_guard
    from validation_anchor as anchor
    left join resolved_candidates as resolved on true
)
select
    output.data_year,
    output.supplier_npi,
    output.source_release_id,
    output.supplier_last_org_name,
    output.supplier_first_name,
    output.supplier_city,
    output.supplier_state
from validated_output as output
where output.parameter_guard = 1
  and output.scope_guard = 1
  and output.resolution_guard = 1
  and output.supplier_npi is not null
order by output.supplier_npi, output.source_release_id
