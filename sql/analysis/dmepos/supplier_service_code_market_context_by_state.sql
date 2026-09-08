-- Parameterized supplier-address-state market context for retained, exact-code
-- DMEPOS supplier-service universes.
--
-- The output grain is year/HCPCS/rental indicator/entity type/requested supplier-
-- address state. National totals repeat across requested states so that every row
-- carries its denominator. All totals and shares use visible published supplier-
-- service cells only. Suppressed beneficiary counts remain in monetary and service
-- totals but are excluded from per-beneficiary distributions. Supplier state is a
-- source address assertion, not beneficiary residence or the furnishing location.
with settings as materialized (
    select
        %(data_years)s::smallint[] as data_years,
        %(candidate_npis)s::text[] as candidate_npis,
        %(hcpcs_codes)s::text[] as hcpcs_codes,
        %(source_address_states)s::text[] as source_address_states,
        %(min_peer_count)s::bigint as minimum_peer_count
), raw_requested_years as (
    select requested.data_year
    from settings
    cross join lateral unnest(settings.data_years) as requested(data_year)
), raw_candidate_npis as (
    select requested.supplier_npi
    from settings
    cross join lateral unnest(settings.candidate_npis) as requested(supplier_npi)
), raw_target_codes as (
    select upper(btrim(requested.hcpcs_code)) as hcpcs_code
    from settings
    cross join lateral unnest(settings.hcpcs_codes) as requested(hcpcs_code)
), raw_source_address_states as (
    select upper(btrim(requested.source_address_state)) as source_address_state
    from settings
    cross join lateral unnest(
        settings.source_address_states
    ) as requested(source_address_state)
), parameter_validity as (
    select
        cardinality(settings.data_years) between 1 and 3
        and cardinality(settings.candidate_npis) between 1 and 100
        and cardinality(settings.hcpcs_codes) between 1 and 100
        and cardinality(settings.source_address_states) between 1 and 100
        and settings.minimum_peer_count between 1 and 100000
        and cardinality(settings.data_years) = (
            select count(distinct data_year) from raw_requested_years
        )
        and cardinality(settings.candidate_npis) = (
            select count(distinct supplier_npi) from raw_candidate_npis
        )
        and cardinality(settings.hcpcs_codes) = (
            select count(distinct hcpcs_code) from raw_target_codes
        )
        and cardinality(settings.source_address_states) = (
            select count(distinct source_address_state)
            from raw_source_address_states
        )
        and not exists (
            select 1
            from raw_requested_years
            where data_year is null or data_year not in (2022, 2023, 2024)
        )
        and not exists (
            select 1
            from raw_candidate_npis
            where supplier_npi is null or supplier_npi !~ '^[0-9]{10}$'
        )
        and not exists (
            select 1
            from raw_target_codes
            where hcpcs_code is null or hcpcs_code !~ '^[A-Z0-9]{5}$'
        )
        and not exists (
            select 1
            from raw_source_address_states
            where source_address_state is null
               or source_address_state !~ '^[A-Z]{2}$'
        ) as valid
    from settings
), parameter_gate as materialized (
    select case
        when valid then 1
        else (
            'DMEPOS state-market parameter validation failed; require one to three '
            || 'unique pinned years, one to 100 unique ten-digit candidate NPIs, '
            || 'one to 100 unique five-character HCPCS codes, one to 100 unique '
            || 'two-letter supplier-address states, and a peer minimum between '
            || 'one and 100000; validity='
            || coalesce(valid::text, 'null')
        )::integer
    end as guard
    from parameter_validity
), requested_years as materialized (
    select distinct raw.data_year
    from raw_requested_years as raw
    cross join parameter_gate
    where parameter_gate.guard = 1
), candidate_npis as materialized (
    select distinct raw.supplier_npi
    from raw_candidate_npis as raw
    cross join parameter_gate
    where parameter_gate.guard = 1
), target_codes as materialized (
    select distinct raw.hcpcs_code
    from raw_target_codes as raw
    cross join parameter_gate
    where parameter_gate.guard = 1
), source_address_states as materialized (
    select distinct raw.source_address_state
    from raw_source_address_states as raw
    cross join parameter_gate
    where parameter_gate.guard = 1
), expected_service_filter as materialized (
    select jsonb_build_object(
        'HCPCS_Cd', jsonb_agg(target.hcpcs_code order by target.hcpcs_code)
    ) as retrieval_filters
    from target_codes as target
), pinned_versions (
    data_year,
    supplier_version_id,
    supplier_service_version_id
) as (
    values
        (
            2022::smallint,
            '471f9c0d-12c4-4601-a547-559dcfa2d0e2'::text,
            'dad703df-2e52-4ac6-a26f-51ecbf463d77'::text
        ),
        (
            2023::smallint,
            '7bb52a09-9eba-43f9-b7ec-57f65fbbc86c'::text,
            '44235fd3-6cf9-487f-928d-12998cd84071'::text
        ),
        (
            2024::smallint,
            '4c6cfc68-a149-4bfe-b934-db2b92d0f360'::text,
            '1dc6718a-e44b-403a-a735-ce01a6233822'::text
        )
), input_release_ids as (
    select requested.source_release_id
    from unnest(%(source_release_ids)s::bigint[]) as requested(source_release_id)
), audited_releases as materialized (
    select
        input.source_release_id,
        release.data_year,
        release.version_id,
        release.status,
        release.aggregation_keys,
        release.manifest_path,
        release.manifest_sha256,
        coalesce(release.retrieval_filters, '{}'::jsonb) as retrieval_filters,
        dataset.source as dataset_source,
        dataset.slug,
        release.source_release_id is not null
            and release.status = 'loaded'
            and release.manifest_path is not null
            and release.manifest_sha256 ~ '^[0-9a-f]{64}$'
            and dataset.source = 'Centers for Medicare & Medicaid Services'
            and release.data_year = requested.data_year
            and (
                (
                    dataset.slug =
                        'medicare-durable-medical-equipment-devices-supplies-by-supplier'
                    and release.version_id = pinned.supplier_version_id
                    and release.aggregation_keys = '["Suplr_NPI"]'::jsonb
                )
                or (
                    dataset.slug =
                        'medicare-durable-medical-equipment-devices-supplies-by-supplier-and-service'
                    and release.version_id = pinned.supplier_service_version_id
                    and release.aggregation_keys =
                        '["Suplr_NPI","HCPCS_Cd","Suplr_Rentl_Ind"]'::jsonb
                )
            ) as eligible
    from input_release_ids as input
    left join metadata.source_release as release
        on release.source_release_id = input.source_release_id
    left join metadata.source_dataset as dataset
        on dataset.dataset_id = release.dataset_id
    left join requested_years as requested
        on requested.data_year = release.data_year
    left join pinned_versions as pinned
        on pinned.data_year = release.data_year
), year_release_coverage as materialized (
    select
        requested.data_year,
        count(audit.source_release_id) filter (
            where audit.eligible
              and audit.slug =
                'medicare-durable-medical-equipment-devices-supplies-by-supplier'
        ) as supplier_release_count,
        count(audit.source_release_id) filter (
            where audit.eligible
              and audit.slug =
                'medicare-durable-medical-equipment-devices-supplies-by-supplier-and-service'
        ) as supplier_service_release_count,
        max(audit.source_release_id) filter (
            where audit.eligible
              and audit.slug =
                'medicare-durable-medical-equipment-devices-supplies-by-supplier'
        ) as supplier_release_id,
        max(audit.source_release_id) filter (
            where audit.eligible
              and audit.slug =
                'medicare-durable-medical-equipment-devices-supplies-by-supplier-and-service'
        ) as supplier_service_release_id
    from requested_years as requested
    left join audited_releases as audit
        on audit.data_year = requested.data_year
    group by requested.data_year
), release_contract_validity as (
    select
        (select count(*) from input_release_ids)
            = 2 * (select count(*) from requested_years)
        and (select count(distinct source_release_id) from input_release_ids)
            = (select count(*) from input_release_ids)
        and not exists (
            select 1 from audited_releases where eligible is not true
        )
        and coalesce(
            bool_and(
                supplier_release_count = 1
                and supplier_service_release_count = 1
            ),
            false
        ) as valid,
        coalesce(
            string_agg(
                data_year::text
                || ':supplier=' || supplier_release_count::text
                || ',supplier-service=' || supplier_service_release_count::text,
                ';' order by data_year
            ),
            '[no requested years]'
        ) as observed_counts
    from year_release_coverage
), release_gate as materialized (
    select case
        when valid then 1
        else (
            'DMEPOS code-market release validation failed; expected exactly one '
            || 'loaded, pinned CMS by-Supplier release and one loaded, pinned CMS '
            || 'by-Supplier-and-Service release per requested year; observed '
            || observed_counts
        )::integer
    end as guard
    from release_contract_validity
), validated_releases as materialized (
    select
        coverage.data_year,
        coverage.supplier_release_id,
        coverage.supplier_service_release_id,
        supplier.retrieval_filters as supplier_retrieval_filters,
        service.retrieval_filters as supplier_service_retrieval_filters
    from year_release_coverage as coverage
    join audited_releases as supplier
        on supplier.source_release_id = coverage.supplier_release_id
    join audited_releases as service
        on service.source_release_id = coverage.supplier_service_release_id
    cross join release_gate
    where release_gate.guard = 1
), filter_contract_validity as (
    select
        not exists (
            select 1
            from validated_releases as release
            cross join expected_service_filter as expected
            where release.supplier_retrieval_filters <> '{}'::jsonb
               or release.supplier_service_retrieval_filters
                    <> expected.retrieval_filters
        ) as valid,
        coalesce(
            string_agg(
                release.data_year::text
                || ':supplier=' || release.supplier_retrieval_filters::text
                || ',supplier-service='
                || release.supplier_service_retrieval_filters::text,
                ';' order by release.data_year
            ),
            '[no releases]'
        ) as observed_filters
    from validated_releases as release
), filter_gate as materialized (
    select case
        when valid then 1
        else (
            'DMEPOS code-market retrieval-filter validation failed; by-Supplier '
            || 'releases must be complete and unfiltered, while every service '
            || 'release must exactly match the one-key requested HCPCS code '
            || 'universe; observed ' || observed_filters
        )::integer
    end as guard
    from filter_contract_validity
), bound_releases as materialized (
    select release.*
    from validated_releases as release
    cross join filter_gate
    where filter_gate.guard = 1
), service_universe as materialized (
    select
        release.data_year,
        release.supplier_release_id,
        release.supplier_service_release_id,
        release.supplier_service_retrieval_filters,
        service.supplier_npi,
        service.supplier_last_org_name,
        service.entity_code,
        service.supplier_state,
        service.hcpcs_code,
        service.hcpcs_description,
        service.supplier_rental_indicator,
        service.total_beneficiaries,
        service.total_claims,
        service.total_services,
        service.average_medicare_payment_amount,
        service.average_medicare_standardized_amount
    from bound_releases as release
    join claims.dmepos_supplier_service as service
        on service.source_release_id = release.supplier_service_release_id
        and service.data_year = release.data_year
    join target_codes as target
        on target.hcpcs_code = service.hcpcs_code
), source_integrity_validity as (
    select
        not exists (
            select 1
            from bound_releases as release
            where not exists (
                select 1
                from claims.dmepos_supplier as supplier
                where supplier.source_release_id = release.supplier_release_id
                  and supplier.data_year = release.data_year
            )
               or not exists (
                    select 1
                    from claims.dmepos_supplier_service as service
                    where service.source_release_id = release.supplier_service_release_id
                      and service.data_year = release.data_year
               )
        )
        and not exists (
            select 1
            from bound_releases as release
            where not exists (
                select 1
                from metadata.ingestion_run as run
                where run.source_release_id = release.supplier_release_id
                  and run.status = 'succeeded'
                  and run.loaded_rows = (
                      select count(*)
                      from claims.dmepos_supplier as supplier
                      where supplier.source_release_id = release.supplier_release_id
                        and supplier.data_year = release.data_year
                  )
            )
               or not exists (
                    select 1
                    from metadata.ingestion_run as run
                    where run.source_release_id = release.supplier_service_release_id
                      and run.status = 'succeeded'
                      and run.loaded_rows = (
                          select count(*)
                          from claims.dmepos_supplier_service as service
                          where service.source_release_id =
                                release.supplier_service_release_id
                            and service.data_year = release.data_year
                      )
               )
        )
        and not exists (
            select 1
            from bound_releases as release
            join claims.dmepos_supplier_service as service
                on service.source_release_id = release.supplier_service_release_id
                and service.data_year = release.data_year
            where not exists (
                select 1
                from target_codes as target
                where target.hcpcs_code = service.hcpcs_code
            )
        )
        and not exists (
            select 1
            from (
                select
                    data_year,
                    supplier_npi,
                    hcpcs_code,
                    supplier_rental_indicator,
                    count(*) as copies
                from service_universe
                group by
                    data_year,
                    supplier_npi,
                    hcpcs_code,
                    supplier_rental_indicator
                having count(*) > 1
            ) as duplicate_service_cells
        ) as valid
), source_integrity_gate as materialized (
    select case
        when valid then 1
        else (
            'DMEPOS code-market source-integrity validation failed; a bound release '
            || 'is empty or lacks a matching successful load count, a service row '
            || 'falls outside the exact code universe, or the canonical service '
            || 'grain is duplicated; validity='
            || coalesce(valid::text, 'null')
        )::integer
    end as guard
    from source_integrity_validity
), service_metrics as materialized (
    select
        service.*,
        candidate.supplier_npi is not null as candidate_cohort_flag,
        service.total_beneficiaries is not null
            and service.total_beneficiaries > 0 as known_beneficiary_flag,
        service.total_services * service.average_medicare_payment_amount
            as reconstructed_medicare_payment_amount,
        service.total_services * service.average_medicare_standardized_amount
            as reconstructed_medicare_standardized_payment_amount,
        service.total_services
            / nullif(service.total_beneficiaries, 0)
            as services_per_beneficiary,
        service.total_services * service.average_medicare_payment_amount
            / nullif(service.total_beneficiaries, 0)
            as medicare_payment_per_beneficiary,
        service.total_services * service.average_medicare_standardized_amount
            / nullif(service.total_beneficiaries, 0)
            as standardized_payment_per_beneficiary
    from service_universe as service
    left join candidate_npis as candidate
        on candidate.supplier_npi = service.supplier_npi
    cross join source_integrity_gate
    where source_integrity_gate.guard = 1
), national_market as materialized (
    select
        data_year,
        supplier_service_release_id,
        supplier_service_retrieval_filters,
        hcpcs_code,
        max(hcpcs_description) as hcpcs_description,
        supplier_rental_indicator,
        entity_code,
        count(*) as national_total_visible_cells,
        count(*) filter (where known_beneficiary_flag)
            as national_known_beneficiary_cells,
        count(*) filter (
            where known_beneficiary_flag
              and not candidate_cohort_flag
        ) as national_cohort_excluded_known_beneficiary_peer_cells,
        count(*) filter (where not known_beneficiary_flag)
            as national_suppressed_or_unknown_beneficiary_cells,
        count(*) filter (where candidate_cohort_flag)
            as national_candidate_cohort_visible_cells,
        sum(total_services) as national_total_visible_services,
        sum(reconstructed_medicare_payment_amount)
            as national_reconstructed_visible_medicare_payment,
        sum(reconstructed_medicare_standardized_payment_amount)
            as national_reconstructed_visible_standardized_payment,
        coalesce(sum(total_services) filter (
            where candidate_cohort_flag
        ), 0) as national_candidate_cohort_visible_services,
        coalesce(sum(reconstructed_medicare_payment_amount) filter (
            where candidate_cohort_flag
        ), 0) as national_candidate_cohort_visible_medicare_payment,
        coalesce(sum(reconstructed_medicare_standardized_payment_amount) filter (
            where candidate_cohort_flag
        ), 0) as national_candidate_cohort_visible_standardized_payment,
        max(reconstructed_medicare_payment_amount)
            / nullif(sum(reconstructed_medicare_payment_amount), 0)
            as national_top_supplier_visible_payment_share,
        sum(
            reconstructed_medicare_payment_amount
            * reconstructed_medicare_payment_amount
        ) / nullif(
            sum(reconstructed_medicare_payment_amount)
            * sum(reconstructed_medicare_payment_amount),
            0
        ) as national_visible_payment_hhi,
        (percentile_cont(0.50) within group (
            order by services_per_beneficiary
        ) filter (
            where known_beneficiary_flag
              and not candidate_cohort_flag
        ))::numeric as national_peer_services_median,
        (percentile_cont(0.75) within group (
            order by services_per_beneficiary
        ) filter (
            where known_beneficiary_flag
              and not candidate_cohort_flag
        ))::numeric as national_peer_services_q75,
        (percentile_cont(0.50) within group (
            order by medicare_payment_per_beneficiary
        ) filter (
            where known_beneficiary_flag
              and not candidate_cohort_flag
        ))::numeric as national_peer_medicare_payment_median,
        (percentile_cont(0.75) within group (
            order by medicare_payment_per_beneficiary
        ) filter (
            where known_beneficiary_flag
              and not candidate_cohort_flag
        ))::numeric as national_peer_medicare_payment_q75,
        (percentile_cont(0.50) within group (
            order by standardized_payment_per_beneficiary
        ) filter (
            where known_beneficiary_flag
              and not candidate_cohort_flag
        ))::numeric as national_peer_standardized_payment_median,
        (percentile_cont(0.75) within group (
            order by standardized_payment_per_beneficiary
        ) filter (
            where known_beneficiary_flag
              and not candidate_cohort_flag
        ))::numeric as national_peer_standardized_payment_q75
    from service_metrics
    group by
        data_year,
        supplier_service_release_id,
        supplier_service_retrieval_filters,
        hcpcs_code,
        supplier_rental_indicator,
        entity_code
), state_market as materialized (
    select
        metrics.data_year,
        metrics.supplier_service_release_id,
        metrics.hcpcs_code,
        metrics.supplier_rental_indicator,
        metrics.entity_code,
        requested.source_address_state,
        count(*) as state_total_visible_cells,
        count(*) filter (where metrics.known_beneficiary_flag)
            as state_known_beneficiary_cells,
        count(*) filter (
            where metrics.known_beneficiary_flag
              and not metrics.candidate_cohort_flag
        ) as state_cohort_excluded_known_beneficiary_peer_cells,
        count(*) filter (where not metrics.known_beneficiary_flag)
            as state_suppressed_or_unknown_beneficiary_cells,
        count(*) filter (where metrics.candidate_cohort_flag)
            as state_candidate_cohort_visible_cells,
        sum(metrics.total_services) as state_total_visible_services,
        sum(metrics.reconstructed_medicare_payment_amount)
            as state_reconstructed_visible_medicare_payment,
        sum(metrics.reconstructed_medicare_standardized_payment_amount)
            as state_reconstructed_visible_standardized_payment,
        coalesce(sum(metrics.total_services) filter (
            where metrics.candidate_cohort_flag
        ), 0) as state_candidate_cohort_visible_services,
        coalesce(sum(metrics.reconstructed_medicare_payment_amount) filter (
            where metrics.candidate_cohort_flag
        ), 0) as state_candidate_cohort_visible_medicare_payment,
        coalesce(sum(
            metrics.reconstructed_medicare_standardized_payment_amount
        ) filter (
            where metrics.candidate_cohort_flag
        ), 0) as state_candidate_cohort_visible_standardized_payment,
        max(metrics.reconstructed_medicare_payment_amount)
            / nullif(sum(metrics.reconstructed_medicare_payment_amount), 0)
            as state_top_supplier_visible_payment_share,
        sum(
            metrics.reconstructed_medicare_payment_amount
            * metrics.reconstructed_medicare_payment_amount
        ) / nullif(
            sum(metrics.reconstructed_medicare_payment_amount)
            * sum(metrics.reconstructed_medicare_payment_amount),
            0
        ) as state_visible_payment_hhi,
        (percentile_cont(0.50) within group (
            order by metrics.services_per_beneficiary
        ) filter (
            where metrics.known_beneficiary_flag
              and not metrics.candidate_cohort_flag
        ))::numeric as state_peer_services_median,
        (percentile_cont(0.75) within group (
            order by metrics.services_per_beneficiary
        ) filter (
            where metrics.known_beneficiary_flag
              and not metrics.candidate_cohort_flag
        ))::numeric as state_peer_services_q75,
        (percentile_cont(0.50) within group (
            order by metrics.medicare_payment_per_beneficiary
        ) filter (
            where metrics.known_beneficiary_flag
              and not metrics.candidate_cohort_flag
        ))::numeric as state_peer_medicare_payment_median,
        (percentile_cont(0.75) within group (
            order by metrics.medicare_payment_per_beneficiary
        ) filter (
            where metrics.known_beneficiary_flag
              and not metrics.candidate_cohort_flag
        ))::numeric as state_peer_medicare_payment_q75,
        (percentile_cont(0.50) within group (
            order by metrics.standardized_payment_per_beneficiary
        ) filter (
            where metrics.known_beneficiary_flag
              and not metrics.candidate_cohort_flag
        ))::numeric as state_peer_standardized_payment_median,
        (percentile_cont(0.75) within group (
            order by metrics.standardized_payment_per_beneficiary
        ) filter (
            where metrics.known_beneficiary_flag
              and not metrics.candidate_cohort_flag
        ))::numeric as state_peer_standardized_payment_q75
    from service_metrics as metrics
    join source_address_states as requested
        on requested.source_address_state =
            upper(btrim(metrics.supplier_state))
    group by
        metrics.data_year,
        metrics.supplier_service_release_id,
        metrics.hcpcs_code,
        metrics.supplier_rental_indicator,
        metrics.entity_code,
        requested.source_address_state
), market_context as materialized (
    select
        national.*,
        requested.source_address_state,
        coalesce(state.state_total_visible_cells, 0)
            as state_total_visible_cells,
        coalesce(state.state_known_beneficiary_cells, 0)
            as state_known_beneficiary_cells,
        coalesce(
            state.state_cohort_excluded_known_beneficiary_peer_cells,
            0
        ) as state_cohort_excluded_known_beneficiary_peer_cells,
        coalesce(state.state_suppressed_or_unknown_beneficiary_cells, 0)
            as state_suppressed_or_unknown_beneficiary_cells,
        coalesce(state.state_candidate_cohort_visible_cells, 0)
            as state_candidate_cohort_visible_cells,
        coalesce(state.state_total_visible_services, 0)
            as state_total_visible_services,
        coalesce(state.state_reconstructed_visible_medicare_payment, 0)
            as state_reconstructed_visible_medicare_payment,
        coalesce(state.state_reconstructed_visible_standardized_payment, 0)
            as state_reconstructed_visible_standardized_payment,
        coalesce(state.state_candidate_cohort_visible_services, 0)
            as state_candidate_cohort_visible_services,
        coalesce(state.state_candidate_cohort_visible_medicare_payment, 0)
            as state_candidate_cohort_visible_medicare_payment,
        coalesce(
            state.state_candidate_cohort_visible_standardized_payment,
            0
        ) as state_candidate_cohort_visible_standardized_payment,
        state.state_top_supplier_visible_payment_share,
        state.state_visible_payment_hhi,
        state.state_peer_services_median,
        state.state_peer_services_q75,
        state.state_peer_medicare_payment_median,
        state.state_peer_medicare_payment_q75,
        state.state_peer_standardized_payment_median,
        state.state_peer_standardized_payment_q75,
        case
            when national.national_cohort_excluded_known_beneficiary_peer_cells
                >= settings.minimum_peer_count
                then 'scoreable'
            else 'insufficient-cohort-excluded-peer-cells-descriptive'
        end as national_known_beneficiary_peer_status,
        case
            when coalesce(
                state.state_cohort_excluded_known_beneficiary_peer_cells,
                0
            ) >= settings.minimum_peer_count
                then 'scoreable'
            else 'insufficient-cohort-excluded-peer-cells-descriptive'
        end as state_known_beneficiary_peer_status
    from national_market as national
    cross join source_address_states as requested
    cross join settings
    left join state_market as state
        on state.data_year = national.data_year
        and state.supplier_service_release_id =
            national.supplier_service_release_id
        and state.hcpcs_code = national.hcpcs_code
        and state.supplier_rental_indicator =
            national.supplier_rental_indicator
        and state.entity_code is not distinct from national.entity_code
        and state.source_address_state = requested.source_address_state
), validation_anchor as materialized (
    select
        parameter_gate.guard as parameter_guard,
        release_gate.guard as release_guard,
        filter_gate.guard as filter_guard,
        source_integrity_gate.guard as source_integrity_guard
    from parameter_gate
    cross join release_gate
    cross join filter_gate
    cross join source_integrity_gate
), validated_output as materialized (
    select
        market.*,
        anchor.parameter_guard,
        anchor.release_guard,
        anchor.filter_guard,
        anchor.source_integrity_guard
    from validation_anchor as anchor
    left join market_context as market on true
)
select
    output.data_year,
    output.supplier_service_release_id,
    output.supplier_service_retrieval_filters,
    output.hcpcs_code,
    output.hcpcs_description,
    output.supplier_rental_indicator,
    output.entity_code,
    output.source_address_state,
    output.national_total_visible_cells,
    output.national_known_beneficiary_cells,
    output.national_cohort_excluded_known_beneficiary_peer_cells,
    output.national_suppressed_or_unknown_beneficiary_cells,
    output.national_known_beneficiary_peer_status,
    output.national_total_visible_services,
    output.national_reconstructed_visible_medicare_payment,
    output.national_reconstructed_visible_standardized_payment,
    output.national_candidate_cohort_visible_cells,
    output.national_candidate_cohort_visible_services,
    output.national_candidate_cohort_visible_medicare_payment,
    output.national_candidate_cohort_visible_standardized_payment,
    output.national_candidate_cohort_visible_services
        / nullif(output.national_total_visible_services, 0)
        as national_candidate_cohort_visible_service_share,
    output.national_candidate_cohort_visible_medicare_payment
        / nullif(output.national_reconstructed_visible_medicare_payment, 0)
        as national_candidate_cohort_visible_payment_share,
    output.national_candidate_cohort_visible_standardized_payment
        / nullif(output.national_reconstructed_visible_standardized_payment, 0)
        as national_candidate_cohort_visible_standardized_payment_share,
    output.national_top_supplier_visible_payment_share,
    output.national_visible_payment_hhi,
    output.national_peer_services_median,
    output.national_peer_services_q75,
    output.national_peer_medicare_payment_median,
    output.national_peer_medicare_payment_q75,
    output.national_peer_standardized_payment_median,
    output.national_peer_standardized_payment_q75,
    output.state_total_visible_cells,
    output.state_known_beneficiary_cells,
    output.state_cohort_excluded_known_beneficiary_peer_cells,
    output.state_suppressed_or_unknown_beneficiary_cells,
    output.state_known_beneficiary_peer_status,
    output.state_total_visible_services,
    output.state_reconstructed_visible_medicare_payment,
    output.state_reconstructed_visible_standardized_payment,
    output.state_total_visible_services
        / nullif(output.national_total_visible_services, 0)
        as state_visible_service_share_of_national,
    output.state_reconstructed_visible_medicare_payment
        / nullif(output.national_reconstructed_visible_medicare_payment, 0)
        as state_visible_payment_share_of_national,
    output.state_reconstructed_visible_standardized_payment
        / nullif(output.national_reconstructed_visible_standardized_payment, 0)
        as state_visible_standardized_payment_share_of_national,
    output.state_candidate_cohort_visible_cells,
    output.state_candidate_cohort_visible_services,
    output.state_candidate_cohort_visible_medicare_payment,
    output.state_candidate_cohort_visible_standardized_payment,
    output.state_candidate_cohort_visible_services
        / nullif(output.state_total_visible_services, 0)
        as state_candidate_cohort_visible_service_share,
    output.state_candidate_cohort_visible_medicare_payment
        / nullif(output.state_reconstructed_visible_medicare_payment, 0)
        as state_candidate_cohort_visible_payment_share,
    output.state_candidate_cohort_visible_standardized_payment
        / nullif(output.state_reconstructed_visible_standardized_payment, 0)
        as state_candidate_cohort_visible_standardized_payment_share,
    output.state_top_supplier_visible_payment_share,
    output.state_visible_payment_hhi,
    output.state_peer_services_median,
    output.state_peer_services_q75,
    output.state_peer_medicare_payment_median,
    output.state_peer_medicare_payment_q75,
    output.state_peer_standardized_payment_median,
    output.state_peer_standardized_payment_q75,
    settings.minimum_peer_count,
    array_to_string(settings.data_years, ';') as requested_data_years,
    array_to_string(settings.source_address_states, ';')
        as requested_source_address_states,
    'Each row is an exact year, HCPCS, rental indicator, entity type, and requested supplier-address-state sensitivity; national denominators repeat across requested states.'
        as output_grain_caveat,
    'All totals and shares use visible published supplier-service cells; suppressed beneficiary counts remain unknown, not zero, and suppressed cells absent from the file are not reconstructed.'
        as visible_market_caveat,
    'Supplier state is a source-published address assertion, not beneficiary residence, service location, shipment destination, or payment recipient.'
        as geography_caveat,
    'Reconstructed Medicare payment is service units multiplied by the published average Medicare payment per unit; it is not supplier income, improper payment, loss, or damages.'
        as monetary_caveat
from validated_output as output
cross join settings
where output.parameter_guard = 1
  and output.release_guard = 1
  and output.filter_guard = 1
  and output.source_integrity_guard = 1
  and output.hcpcs_code is not null
order by
    output.data_year,
    output.hcpcs_code,
    output.supplier_rental_indicator,
    output.entity_code,
    output.source_address_state;
