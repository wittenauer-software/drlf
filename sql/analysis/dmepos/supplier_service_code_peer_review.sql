-- Product-level review for a bounded cohort of DMEPOS supplier candidates.
--
-- The output grain is one requested year/supplier-NPI/HCPCS/rental cell. Peer
-- statistics use only known-beneficiary cells at the exact year/HCPCS/rental/
-- entity grain and exclude every requested candidate NPI. A separately labeled
-- supplier-address-state sensitivity adds state to that exact peer grain. Published service
-- units retain their HCPCS-specific meaning and are never interpreted as
-- visits. Reconstructed payments and Q75 benchmark exposures are descriptive
-- research measures, not supplier income, improper payment, loss, or damages.
-- The exact-cell algorithm handshake changes whenever the fixed qualification
-- thresholds or routing semantics change.
with settings as materialized (
    select
        %(data_years)s::smallint[] as data_years,
        %(candidate_npis)s::text[] as candidate_npis,
        %(hcpcs_codes)s::text[] as hcpcs_codes,
        %(min_peer_count)s::bigint as minimum_peer_count,
        100000::numeric as minimum_observed_raw_payment,
        100000::numeric as minimum_q75_benchmark_exposure,
        0.975::numeric as tail_percentile_threshold,
        0.99::numeric as full_percentile_threshold,
        3.5::numeric as minimum_robust_z,
        2::numeric as minimum_median_ratio,
        (
            select max(requested.data_year)
            from unnest(%(data_years)s::smallint[]) as requested(data_year)
        ) as temporal_focus_year,
        0.000000000001::numeric as robust_scale_relative_tolerance
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
), parameter_validity as (
    select
        cardinality(settings.data_years) between 1 and 3
        and cardinality(settings.candidate_npis) between 1 and 100
        and cardinality(settings.hcpcs_codes) between 1 and 100
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
        ) as valid
    from settings
), parameter_gate as materialized (
    select case
        when valid then 1
        else (
            'DMEPOS code-peer parameter validation failed; require one to three '
            || 'unique pinned years, one to 100 unique ten-digit candidate NPIs, '
            || 'one to 100 unique five-character HCPCS codes, and a peer minimum '
            || 'between one and 100000; validity='
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
            'DMEPOS code-peer release validation failed; expected exactly one '
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
            'DMEPOS code-peer retrieval-filter validation failed; by-Supplier '
            || 'releases must be complete and unfiltered, while each service '
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
        service.supplier_first_name,
        service.entity_code,
        service.supplier_city,
        service.supplier_state,
        service.supplier_state_fips,
        service.supplier_zip5,
        service.supplier_specialty_code,
        service.supplier_specialty_description,
        service.supplier_specialty_source,
        service.rbcs_level,
        service.rbcs_id,
        service.rbcs_description,
        service.hcpcs_code,
        service.hcpcs_description,
        service.supplier_rental_indicator,
        service.total_beneficiaries,
        service.total_claims,
        service.total_services,
        service.average_submitted_charge,
        service.average_medicare_allowed_amount,
        service.average_medicare_payment_amount,
        service.average_medicare_standardized_amount
    from bound_releases as release
    join claims.dmepos_supplier_service as service
        on service.source_release_id = release.supplier_service_release_id
        and service.data_year = release.data_year
    join target_codes as target
        on target.hcpcs_code = service.hcpcs_code
), candidate_summary_rows as materialized (
    select
        release.data_year,
        release.supplier_release_id,
        supplier.supplier_npi,
        supplier.total_hcpcs_codes as summary_total_hcpcs_codes,
        supplier.total_beneficiaries as summary_total_beneficiaries,
        supplier.total_claims as summary_total_claims,
        supplier.total_services as summary_total_services,
        supplier.total_submitted_charge as summary_total_submitted_charge,
        supplier.total_medicare_allowed_amount
            as summary_total_medicare_allowed_amount,
        supplier.total_medicare_payment_amount
            as summary_total_medicare_payment_amount,
        supplier.total_medicare_standardized_payment_amount
            as summary_total_medicare_standardized_payment_amount
    from bound_releases as release
    join claims.dmepos_supplier as supplier
        on supplier.source_release_id = release.supplier_release_id
        and supplier.data_year = release.data_year
    join candidate_npis as candidate
        on candidate.supplier_npi = supplier.supplier_npi
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
        )
        and not exists (
            select 1
            from bound_releases as release
            where not exists (
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
            where release.supplier_service_retrieval_filters <> '{}'::jsonb
              and exists (
                  select 1
                  from claims.dmepos_supplier_service as service
                  where service.source_release_id = release.supplier_service_release_id
                    and service.data_year = release.data_year
                    and not exists (
                        select 1
                        from target_codes as target
                        where target.hcpcs_code = service.hcpcs_code
                    )
              )
        )
        and not exists (
            select 1
            from (
                select
                    service.data_year,
                    service.supplier_npi,
                    service.hcpcs_code,
                    service.supplier_rental_indicator,
                    count(*) as copies
                from service_universe as service
                group by
                    service.data_year,
                    service.supplier_npi,
                    service.hcpcs_code,
                    service.supplier_rental_indicator
                having count(*) > 1
            ) as duplicate_service_cells
        )
        and not exists (
            select 1
            from service_universe as service
            join candidate_npis as candidate
                on candidate.supplier_npi = service.supplier_npi
            left join candidate_summary_rows as summary
                on summary.data_year = service.data_year
                and summary.supplier_npi = service.supplier_npi
            where summary.supplier_npi is null
        ) as valid
), source_integrity_gate as materialized (
    select case
        when valid then 1
        else (
            'DMEPOS code-peer source-integrity validation failed; the complete '
            || 'supplier or service release is empty or lacks a matching successful '
            || 'load count, a service row falls outside its exact code universe, '
            || 'the canonical service grain is duplicated, or a candidate service '
            || 'cell has no corresponding supplier summary; validity='
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
        service.total_services * service.average_submitted_charge
            as reconstructed_submitted_charge,
        service.total_services * service.average_medicare_allowed_amount
            as reconstructed_medicare_allowed_amount,
        service.total_services * service.average_medicare_payment_amount
            as reconstructed_medicare_payment_amount,
        service.total_services * service.average_medicare_standardized_amount
            as reconstructed_medicare_standardized_payment_amount,
        service.total_services
            / nullif(service.total_beneficiaries, 0)
            as services_per_beneficiary,
        service.total_claims::numeric
            / nullif(service.total_beneficiaries, 0)
            as claims_per_beneficiary,
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
), peer_metric_values as materialized (
    select
        service.data_year,
        service.hcpcs_code,
        service.supplier_rental_indicator,
        service.entity_code,
        scope.peer_scope,
        scope.peer_geography,
        service.supplier_npi,
        metric.metric_name,
        metric.metric_value
    from service_metrics as service
    cross join lateral (
        values
            ('national'::text, ''::text),
            (
                'supplier-address-state'::text,
                upper(coalesce(service.supplier_state, ''))
            )
    ) as scope(peer_scope, peer_geography)
    cross join lateral (
        values
            ('claims_per_beneficiary', service.claims_per_beneficiary),
            ('services_per_beneficiary', service.services_per_beneficiary),
            (
                'medicare_payment_per_beneficiary',
                service.medicare_payment_per_beneficiary
            ),
            (
                'standardized_payment_per_beneficiary',
                service.standardized_payment_per_beneficiary
            )
    ) as metric(metric_name, metric_value)
    where not service.candidate_cohort_flag
      and service.known_beneficiary_flag
      and (
          scope.peer_scope = 'national'
          or scope.peer_geography <> ''
      )
), peer_quartiles as materialized (
    select
        data_year,
        hcpcs_code,
        supplier_rental_indicator,
        entity_code,
        peer_scope,
        peer_geography,
        metric_name,
        count(*) as peer_count,
        (percentile_cont(0.25) within group (order by metric_value))::numeric
            as peer_q1,
        (percentile_cont(0.50) within group (order by metric_value))::numeric
            as peer_median,
        (percentile_cont(0.75) within group (order by metric_value))::numeric
            as peer_q75
    from peer_metric_values
    group by
        data_year,
        hcpcs_code,
        supplier_rental_indicator,
        entity_code,
        peer_scope,
        peer_geography,
        metric_name
), peer_absolute_deviations as materialized (
    select
        peer.*,
        quartile.peer_count,
        quartile.peer_q1,
        quartile.peer_median,
        quartile.peer_q75,
        abs(peer.metric_value - quartile.peer_median) as absolute_deviation
    from peer_metric_values as peer
    join peer_quartiles as quartile using (
        data_year,
        hcpcs_code,
        supplier_rental_indicator,
        entity_code,
        peer_scope,
        peer_geography,
        metric_name
    )
), peer_statistics as materialized (
    select
        data_year,
        hcpcs_code,
        supplier_rental_indicator,
        entity_code,
        peer_scope,
        peer_geography,
        metric_name,
        max(peer_count) as peer_count,
        max(peer_median) as peer_median,
        max(peer_q75) as peer_q75,
        max(peer_q75) - max(peer_q1) as peer_iqr,
        (percentile_cont(0.50) within group (order by absolute_deviation))::numeric
            as peer_mad
    from peer_absolute_deviations
    group by
        data_year,
        hcpcs_code,
        supplier_rental_indicator,
        entity_code,
        peer_scope,
        peer_geography,
        metric_name
), candidate_metric_values as materialized (
    select
        service.data_year,
        service.supplier_service_release_id,
        service.supplier_npi,
        service.hcpcs_code,
        service.supplier_rental_indicator,
        service.entity_code,
        scope.peer_scope,
        scope.peer_geography,
        metric.metric_name,
        metric.metric_value
    from service_metrics as service
    cross join lateral (
        values
            ('national'::text, ''::text),
            (
                'supplier-address-state'::text,
                upper(coalesce(service.supplier_state, ''))
            )
    ) as scope(peer_scope, peer_geography)
    cross join lateral (
        values
            ('claims_per_beneficiary', service.claims_per_beneficiary),
            ('services_per_beneficiary', service.services_per_beneficiary),
            (
                'medicare_payment_per_beneficiary',
                service.medicare_payment_per_beneficiary
            ),
            (
                'standardized_payment_per_beneficiary',
                service.standardized_payment_per_beneficiary
            )
    ) as metric(metric_name, metric_value)
    where service.candidate_cohort_flag
      and (
          scope.peer_scope = 'national'
          or scope.peer_geography <> ''
      )
), candidate_metric_ranks as materialized (
    select
        candidate.data_year,
        candidate.supplier_service_release_id,
        candidate.supplier_npi,
        candidate.hcpcs_code,
        candidate.supplier_rental_indicator,
        candidate.entity_code,
        candidate.peer_scope,
        candidate.peer_geography,
        candidate.metric_name,
        candidate.metric_value,
        statistic.peer_count,
        statistic.peer_median,
        statistic.peer_q75,
        statistic.peer_iqr,
        statistic.peer_mad,
        case
            when candidate.metric_value is not null
              and statistic.peer_count > 0
                then 1 + count(peer.metric_value) filter (
                    where peer.metric_value > candidate.metric_value
                )
        end as cohort_excluded_high_rank,
        case
            when candidate.metric_value is not null
              and statistic.peer_count > 0
                then count(peer.metric_value) filter (
                    where peer.metric_value <= candidate.metric_value
                )::numeric / statistic.peer_count
        end as cohort_excluded_empirical_percentile
    from candidate_metric_values as candidate
    left join peer_statistics as statistic using (
        data_year,
        hcpcs_code,
        supplier_rental_indicator,
        entity_code,
        peer_scope,
        peer_geography,
        metric_name
    )
    left join peer_metric_values as peer
        on peer.data_year = candidate.data_year
        and peer.hcpcs_code = candidate.hcpcs_code
        and peer.supplier_rental_indicator = candidate.supplier_rental_indicator
        and peer.entity_code = candidate.entity_code
        and peer.peer_scope = candidate.peer_scope
        and peer.peer_geography = candidate.peer_geography
        and peer.metric_name = candidate.metric_name
    group by
        candidate.data_year,
        candidate.supplier_service_release_id,
        candidate.supplier_npi,
        candidate.hcpcs_code,
        candidate.supplier_rental_indicator,
        candidate.entity_code,
        candidate.peer_scope,
        candidate.peer_geography,
        candidate.metric_name,
        candidate.metric_value,
        statistic.peer_count,
        statistic.peer_median,
        statistic.peer_q75,
        statistic.peer_iqr,
        statistic.peer_mad
), candidate_metric_scores as materialized (
    select
        ranked.*,
        case
            when ranked.peer_median > 0
                then ranked.metric_value / ranked.peer_median
        end as cohort_excluded_median_ratio,
        abs(ranked.metric_value - ranked.peer_median)
            as cohort_excluded_median_absolute_delta,
        case
            when ranked.peer_mad > settings.robust_scale_relative_tolerance
                    * greatest(1::numeric, abs(ranked.peer_median))
                then 0.67448975::numeric
                    * (ranked.metric_value - ranked.peer_median)
                    / ranked.peer_mad
            when ranked.peer_iqr > settings.robust_scale_relative_tolerance
                    * greatest(1::numeric, abs(ranked.peer_median))
                then 1.3489795::numeric
                    * (ranked.metric_value - ranked.peer_median)
                    / ranked.peer_iqr
        end as cohort_excluded_robust_z,
        case
            when ranked.metric_value is null or ranked.peer_count is null
                then null
            when ranked.peer_mad > settings.robust_scale_relative_tolerance
                    * greatest(1::numeric, abs(ranked.peer_median))
                then 'mad'
            when ranked.peer_iqr > settings.robust_scale_relative_tolerance
                    * greatest(1::numeric, abs(ranked.peer_median))
                then 'iqr-fallback'
            else 'degenerate'
        end as cohort_excluded_scale_source
    from candidate_metric_ranks as ranked
    cross join settings
), candidate_peer_statistics as materialized (
    select
        data_year,
        supplier_service_release_id,
        supplier_npi,
        hcpcs_code,
        supplier_rental_indicator,
        entity_code,
        max(peer_count) filter (
            where peer_scope = 'national'
        ) as cohort_excluded_peer_count,
        max(peer_median) filter (
            where peer_scope = 'national'
              and metric_name = 'claims_per_beneficiary'
        ) as claims_peer_median,
        max(peer_q75) filter (
            where peer_scope = 'national'
              and metric_name = 'claims_per_beneficiary'
        ) as claims_peer_q75,
        max(peer_iqr) filter (
            where peer_scope = 'national'
              and metric_name = 'claims_per_beneficiary'
        ) as claims_peer_iqr,
        max(peer_mad) filter (
            where peer_scope = 'national'
              and metric_name = 'claims_per_beneficiary'
        ) as claims_peer_mad,
        max(cohort_excluded_high_rank) filter (
            where peer_scope = 'national'
              and metric_name = 'claims_per_beneficiary'
        ) as claims_cohort_excluded_high_rank,
        max(cohort_excluded_empirical_percentile) filter (
            where peer_scope = 'national'
              and metric_name = 'claims_per_beneficiary'
        ) as claims_cohort_excluded_empirical_percentile,
        max(cohort_excluded_median_ratio) filter (
            where peer_scope = 'national'
              and metric_name = 'claims_per_beneficiary'
        ) as claims_cohort_excluded_median_ratio,
        max(cohort_excluded_median_absolute_delta) filter (
            where peer_scope = 'national'
              and metric_name = 'claims_per_beneficiary'
        ) as claims_cohort_excluded_median_absolute_delta,
        max(cohort_excluded_robust_z) filter (
            where peer_scope = 'national'
              and metric_name = 'claims_per_beneficiary'
        ) as claims_cohort_excluded_robust_z,
        max(cohort_excluded_scale_source) filter (
            where peer_scope = 'national'
              and metric_name = 'claims_per_beneficiary'
        ) as claims_cohort_excluded_scale_source,
        max(peer_median) filter (
            where peer_scope = 'national'
              and metric_name = 'services_per_beneficiary'
        ) as services_peer_median,
        max(peer_q75) filter (
            where peer_scope = 'national'
              and metric_name = 'services_per_beneficiary'
        ) as services_peer_q75,
        max(peer_iqr) filter (
            where peer_scope = 'national'
              and metric_name = 'services_per_beneficiary'
        ) as services_peer_iqr,
        max(peer_mad) filter (
            where peer_scope = 'national'
              and metric_name = 'services_per_beneficiary'
        ) as services_peer_mad,
        max(cohort_excluded_high_rank) filter (
            where peer_scope = 'national'
              and metric_name = 'services_per_beneficiary'
        ) as services_cohort_excluded_high_rank,
        max(cohort_excluded_empirical_percentile) filter (
            where peer_scope = 'national'
              and metric_name = 'services_per_beneficiary'
        ) as services_cohort_excluded_empirical_percentile,
        max(cohort_excluded_median_ratio) filter (
            where peer_scope = 'national'
              and metric_name = 'services_per_beneficiary'
        ) as services_cohort_excluded_median_ratio,
        max(cohort_excluded_median_absolute_delta) filter (
            where peer_scope = 'national'
              and metric_name = 'services_per_beneficiary'
        ) as services_cohort_excluded_median_absolute_delta,
        max(cohort_excluded_robust_z) filter (
            where peer_scope = 'national'
              and metric_name = 'services_per_beneficiary'
        ) as services_cohort_excluded_robust_z,
        max(cohort_excluded_scale_source) filter (
            where peer_scope = 'national'
              and metric_name = 'services_per_beneficiary'
        ) as services_cohort_excluded_scale_source,
        max(peer_median) filter (
            where peer_scope = 'national'
              and metric_name = 'medicare_payment_per_beneficiary'
        ) as medicare_payment_peer_median,
        max(peer_q75) filter (
            where peer_scope = 'national'
              and metric_name = 'medicare_payment_per_beneficiary'
        ) as medicare_payment_peer_q75,
        max(peer_iqr) filter (
            where peer_scope = 'national'
              and metric_name = 'medicare_payment_per_beneficiary'
        ) as medicare_payment_peer_iqr,
        max(peer_mad) filter (
            where peer_scope = 'national'
              and metric_name = 'medicare_payment_per_beneficiary'
        ) as medicare_payment_peer_mad,
        max(cohort_excluded_high_rank) filter (
            where peer_scope = 'national'
              and metric_name = 'medicare_payment_per_beneficiary'
        ) as medicare_payment_cohort_excluded_high_rank,
        max(cohort_excluded_empirical_percentile) filter (
            where peer_scope = 'national'
              and metric_name = 'medicare_payment_per_beneficiary'
        ) as medicare_payment_cohort_excluded_empirical_percentile,
        max(cohort_excluded_median_ratio) filter (
            where peer_scope = 'national'
              and metric_name = 'medicare_payment_per_beneficiary'
        ) as medicare_payment_cohort_excluded_median_ratio,
        max(cohort_excluded_median_absolute_delta) filter (
            where peer_scope = 'national'
              and metric_name = 'medicare_payment_per_beneficiary'
        ) as medicare_payment_cohort_excluded_median_absolute_delta,
        max(cohort_excluded_robust_z) filter (
            where peer_scope = 'national'
              and metric_name = 'medicare_payment_per_beneficiary'
        ) as medicare_payment_cohort_excluded_robust_z,
        max(cohort_excluded_scale_source) filter (
            where peer_scope = 'national'
              and metric_name = 'medicare_payment_per_beneficiary'
        ) as medicare_payment_cohort_excluded_scale_source,
        max(peer_median) filter (
            where peer_scope = 'national'
              and metric_name = 'standardized_payment_per_beneficiary'
        ) as standardized_payment_peer_median,
        max(peer_q75) filter (
            where peer_scope = 'national'
              and metric_name = 'standardized_payment_per_beneficiary'
        ) as standardized_payment_peer_q75,
        max(peer_iqr) filter (
            where peer_scope = 'national'
              and metric_name = 'standardized_payment_per_beneficiary'
        ) as standardized_payment_peer_iqr,
        max(peer_mad) filter (
            where peer_scope = 'national'
              and metric_name = 'standardized_payment_per_beneficiary'
        ) as standardized_payment_peer_mad,
        max(cohort_excluded_high_rank) filter (
            where peer_scope = 'national'
              and metric_name = 'standardized_payment_per_beneficiary'
        ) as standardized_payment_cohort_excluded_high_rank,
        max(cohort_excluded_empirical_percentile) filter (
            where peer_scope = 'national'
              and metric_name = 'standardized_payment_per_beneficiary'
        ) as standardized_payment_cohort_excluded_empirical_percentile,
        max(cohort_excluded_median_ratio) filter (
            where peer_scope = 'national'
              and metric_name = 'standardized_payment_per_beneficiary'
        ) as standardized_payment_cohort_excluded_median_ratio,
        max(cohort_excluded_median_absolute_delta) filter (
            where peer_scope = 'national'
              and metric_name = 'standardized_payment_per_beneficiary'
        ) as standardized_payment_cohort_excluded_median_absolute_delta,
        max(cohort_excluded_robust_z) filter (
            where peer_scope = 'national'
              and metric_name = 'standardized_payment_per_beneficiary'
        ) as standardized_payment_cohort_excluded_robust_z,
        max(cohort_excluded_scale_source) filter (
            where peer_scope = 'national'
              and metric_name = 'standardized_payment_per_beneficiary'
        ) as standardized_payment_cohort_excluded_scale_source,
        max(peer_geography) filter (
            where peer_scope = 'supplier-address-state'
        ) as state_peer_geography,
        max(peer_count) filter (
            where peer_scope = 'supplier-address-state'
        ) as state_cohort_excluded_peer_count,
        max(peer_median) filter (
            where peer_scope = 'supplier-address-state'
              and metric_name = 'services_per_beneficiary'
        ) as state_services_peer_median,
        max(peer_q75) filter (
            where peer_scope = 'supplier-address-state'
              and metric_name = 'services_per_beneficiary'
        ) as state_services_peer_q75,
        max(cohort_excluded_high_rank) filter (
            where peer_scope = 'supplier-address-state'
              and metric_name = 'services_per_beneficiary'
        ) as state_services_cohort_excluded_high_rank,
        max(cohort_excluded_empirical_percentile) filter (
            where peer_scope = 'supplier-address-state'
              and metric_name = 'services_per_beneficiary'
        ) as state_services_cohort_excluded_empirical_percentile,
        max(peer_q75) filter (
            where peer_scope = 'supplier-address-state'
              and metric_name = 'medicare_payment_per_beneficiary'
        ) as state_medicare_payment_peer_q75,
        max(peer_q75) filter (
            where peer_scope = 'supplier-address-state'
              and metric_name = 'standardized_payment_per_beneficiary'
        ) as state_standardized_payment_peer_q75
    from candidate_metric_scores
    group by
        data_year,
        supplier_service_release_id,
        supplier_npi,
        hcpcs_code,
        supplier_rental_indicator,
        entity_code
), code_market_context as materialized (
    select
        data_year,
        hcpcs_code,
        supplier_rental_indicator,
        count(*) as visible_cell_count,
        count(*) filter (where known_beneficiary_flag)
            as known_beneficiary_cell_count,
        count(*) filter (where not known_beneficiary_flag)
            as suppressed_or_unknown_beneficiary_cell_count,
        sum(total_services) as visible_services,
        sum(reconstructed_medicare_payment_amount)
            as visible_medicare_payment_amount,
        sum(reconstructed_medicare_standardized_payment_amount)
            as visible_medicare_standardized_payment_amount,
        sum(total_services) filter (
            where upper(supplier_state) = 'FL'
        ) as source_address_florida_visible_services,
        sum(reconstructed_medicare_payment_amount) filter (
            where upper(supplier_state) = 'FL'
        ) as source_address_florida_visible_medicare_payment,
        sum(reconstructed_medicare_standardized_payment_amount) filter (
            where upper(supplier_state) = 'FL'
        ) as source_address_florida_visible_standardized_payment,
        sum(total_services) filter (
            where candidate_cohort_flag
        ) as candidate_cohort_visible_services,
        sum(reconstructed_medicare_payment_amount) filter (
            where candidate_cohort_flag
        ) as candidate_cohort_visible_medicare_payment,
        sum(reconstructed_medicare_standardized_payment_amount) filter (
            where candidate_cohort_flag
        ) as candidate_cohort_visible_standardized_payment
    from service_metrics
    group by data_year, hcpcs_code, supplier_rental_indicator
), candidate_cell_payment_ranks as materialized (
    select
        service.*,
        row_number() over (
            partition by service.data_year, service.supplier_npi
            order by
                service.reconstructed_medicare_payment_amount desc,
                service.hcpcs_code,
                service.supplier_rental_indicator
        ) as selected_cell_payment_rank
    from service_metrics as service
    where service.candidate_cohort_flag
), candidate_selected_portfolios as materialized (
    select
        data_year,
        supplier_npi,
        count(*) as selected_visible_cell_count,
        count(distinct hcpcs_code) as selected_visible_hcpcs_count,
        sum(total_services) as selected_visible_services,
        sum(reconstructed_submitted_charge)
            as selected_visible_submitted_charge,
        sum(reconstructed_medicare_allowed_amount)
            as selected_visible_medicare_allowed_amount,
        sum(reconstructed_medicare_payment_amount)
            as selected_visible_medicare_payment_amount,
        sum(reconstructed_medicare_standardized_payment_amount)
            as selected_visible_medicare_standardized_payment_amount,
        max(reconstructed_medicare_payment_amount)
            / nullif(sum(reconstructed_medicare_payment_amount), 0)
            as selected_top_cell_payment_share,
        sum(reconstructed_medicare_payment_amount) filter (
            where selected_cell_payment_rank <= 3
        ) / nullif(sum(reconstructed_medicare_payment_amount), 0)
            as selected_top_three_cell_payment_share,
        sum(
            reconstructed_medicare_payment_amount
            * reconstructed_medicare_payment_amount
        ) / nullif(
            sum(reconstructed_medicare_payment_amount)
            * sum(reconstructed_medicare_payment_amount),
            0
        ) as selected_visible_payment_hhi
    from candidate_cell_payment_ranks
    group by data_year, supplier_npi
), candidate_summary_context as materialized (
    select
        portfolio.*,
        summary.supplier_release_id,
        summary.summary_total_hcpcs_codes,
        summary.summary_total_beneficiaries,
        summary.summary_total_claims,
        summary.summary_total_services,
        summary.summary_total_submitted_charge,
        summary.summary_total_medicare_allowed_amount,
        summary.summary_total_medicare_payment_amount,
        summary.summary_total_medicare_standardized_payment_amount,
        portfolio.selected_visible_services
            / nullif(summary.summary_total_services, 0)
            as selected_visible_service_unit_coverage,
        portfolio.selected_visible_medicare_payment_amount
            / nullif(summary.summary_total_medicare_payment_amount, 0)
            as selected_visible_medicare_payment_coverage,
        portfolio.selected_visible_medicare_standardized_payment_amount
            / nullif(summary.summary_total_medicare_standardized_payment_amount, 0)
            as selected_visible_standardized_payment_coverage,
        summary.summary_total_medicare_payment_amount
            - portfolio.selected_visible_medicare_payment_amount
            as unrepresented_medicare_payment_amount,
        summary.summary_total_medicare_standardized_payment_amount
            - portfolio.selected_visible_medicare_standardized_payment_amount
            as unrepresented_standardized_payment_amount
    from candidate_selected_portfolios as portfolio
    join candidate_summary_rows as summary using (data_year, supplier_npi)
), candidate_scored as materialized (
    select
        candidate.*,
        coalesce(peer.cohort_excluded_peer_count, 0)
            as cohort_excluded_peer_count,
        case
            when not candidate.known_beneficiary_flag
                then 'unknown-beneficiary-descriptive'
            when coalesce(peer.cohort_excluded_peer_count, 0)
                < settings.minimum_peer_count
                then 'insufficient-peer-descriptive'
            else 'scoreable'
        end as peer_evaluation_status,
        peer.claims_peer_median,
        peer.claims_peer_q75,
        peer.claims_peer_iqr,
        peer.claims_peer_mad,
        peer.claims_cohort_excluded_high_rank,
        peer.claims_cohort_excluded_empirical_percentile,
        peer.claims_cohort_excluded_median_ratio,
        peer.claims_cohort_excluded_median_absolute_delta,
        peer.claims_cohort_excluded_robust_z,
        peer.claims_cohort_excluded_scale_source,
        peer.services_peer_median,
        peer.services_peer_q75,
        peer.services_peer_iqr,
        peer.services_peer_mad,
        peer.services_cohort_excluded_high_rank,
        peer.services_cohort_excluded_empirical_percentile,
        peer.services_cohort_excluded_median_ratio,
        peer.services_cohort_excluded_median_absolute_delta,
        peer.services_cohort_excluded_robust_z,
        peer.services_cohort_excluded_scale_source,
        peer.medicare_payment_peer_median,
        peer.medicare_payment_peer_q75,
        peer.medicare_payment_peer_iqr,
        peer.medicare_payment_peer_mad,
        peer.medicare_payment_cohort_excluded_high_rank,
        peer.medicare_payment_cohort_excluded_empirical_percentile,
        peer.medicare_payment_cohort_excluded_median_ratio,
        peer.medicare_payment_cohort_excluded_median_absolute_delta,
        peer.medicare_payment_cohort_excluded_robust_z,
        peer.medicare_payment_cohort_excluded_scale_source,
        peer.standardized_payment_peer_median,
        peer.standardized_payment_peer_q75,
        peer.standardized_payment_peer_iqr,
        peer.standardized_payment_peer_mad,
        peer.standardized_payment_cohort_excluded_high_rank,
        peer.standardized_payment_cohort_excluded_empirical_percentile,
        peer.standardized_payment_cohort_excluded_median_ratio,
        peer.standardized_payment_cohort_excluded_median_absolute_delta,
        peer.standardized_payment_cohort_excluded_robust_z,
        peer.standardized_payment_cohort_excluded_scale_source,
        peer.state_peer_geography,
        coalesce(peer.state_cohort_excluded_peer_count, 0)
            as state_cohort_excluded_peer_count,
        case
            when upper(coalesce(candidate.supplier_state, '')) = ''
                then 'state-unavailable-descriptive'
            when not candidate.known_beneficiary_flag
                then 'unknown-beneficiary-descriptive'
            when coalesce(
                peer.state_cohort_excluded_peer_count,
                0
            ) < settings.minimum_peer_count
                then 'insufficient-state-peer-descriptive'
            else 'scoreable'
        end as state_peer_evaluation_status,
        peer.state_services_peer_median,
        peer.state_services_peer_q75,
        peer.state_services_cohort_excluded_high_rank,
        peer.state_services_cohort_excluded_empirical_percentile,
        peer.state_medicare_payment_peer_q75,
        peer.state_standardized_payment_peer_q75,
        case
            when candidate.known_beneficiary_flag
              and coalesce(peer.cohort_excluded_peer_count, 0)
                    >= settings.minimum_peer_count
                then greatest(
                    0::numeric,
                    candidate.reconstructed_medicare_payment_amount
                        - candidate.total_beneficiaries
                            * peer.medicare_payment_peer_q75
                )
        end as raw_payment_benchmark_exposure_above_q75,
        case
            when candidate.known_beneficiary_flag
              and coalesce(peer.cohort_excluded_peer_count, 0)
                    >= settings.minimum_peer_count
                then greatest(
                    0::numeric,
                    candidate.reconstructed_medicare_standardized_payment_amount
                        - candidate.total_beneficiaries
                            * peer.standardized_payment_peer_q75
                )
        end as standardized_payment_benchmark_exposure_above_q75,
        case
            when candidate.known_beneficiary_flag
              and coalesce(
                    peer.state_cohort_excluded_peer_count,
                    0
              ) >= settings.minimum_peer_count
                then greatest(
                    0::numeric,
                    candidate.reconstructed_medicare_payment_amount
                        - candidate.total_beneficiaries
                            * peer.state_medicare_payment_peer_q75
                )
        end as state_raw_payment_benchmark_exposure_above_q75,
        case
            when candidate.known_beneficiary_flag
              and coalesce(
                    peer.state_cohort_excluded_peer_count,
                    0
              ) >= settings.minimum_peer_count
                then greatest(
                    0::numeric,
                    candidate.reconstructed_medicare_standardized_payment_amount
                        - candidate.total_beneficiaries
                            * peer.state_standardized_payment_peer_q75
                )
        end as state_standardized_payment_benchmark_exposure_above_q75,
        candidate.reconstructed_medicare_payment_amount
            / nullif(market.visible_medicare_payment_amount, 0)
            as candidate_cell_visible_national_payment_share,
        candidate.reconstructed_medicare_standardized_payment_amount
            / nullif(market.visible_medicare_standardized_payment_amount, 0)
            as candidate_cell_visible_national_standardized_payment_share,
        candidate.total_services
            / nullif(market.visible_services, 0)
            as candidate_cell_visible_national_service_share,
        coalesce(market.source_address_florida_visible_medicare_payment, 0)
            / nullif(market.visible_medicare_payment_amount, 0)
            as source_address_florida_visible_payment_share,
        coalesce(market.source_address_florida_visible_services, 0)
            / nullif(market.visible_services, 0)
            as source_address_florida_visible_service_share,
        coalesce(
            market.source_address_florida_visible_standardized_payment,
            0
        ) / nullif(market.visible_medicare_standardized_payment_amount, 0)
            as source_address_florida_visible_standardized_payment_share,
        coalesce(market.candidate_cohort_visible_medicare_payment, 0)
            / nullif(market.visible_medicare_payment_amount, 0)
            as candidate_cohort_visible_payment_share,
        coalesce(market.candidate_cohort_visible_services, 0)
            / nullif(market.visible_services, 0)
            as candidate_cohort_visible_service_share,
        coalesce(market.candidate_cohort_visible_standardized_payment, 0)
            / nullif(market.visible_medicare_standardized_payment_amount, 0)
            as candidate_cohort_visible_standardized_payment_share,
        market.visible_cell_count,
        market.known_beneficiary_cell_count,
        market.suppressed_or_unknown_beneficiary_cell_count,
        summary.summary_total_hcpcs_codes,
        summary.summary_total_beneficiaries,
        summary.summary_total_claims,
        summary.summary_total_services,
        summary.summary_total_submitted_charge,
        summary.summary_total_medicare_allowed_amount,
        summary.summary_total_medicare_payment_amount,
        summary.summary_total_medicare_standardized_payment_amount,
        summary.selected_visible_cell_count,
        summary.selected_visible_hcpcs_count,
        summary.selected_visible_services,
        summary.selected_visible_submitted_charge,
        summary.selected_visible_medicare_allowed_amount,
        summary.selected_visible_medicare_payment_amount,
        summary.selected_visible_medicare_standardized_payment_amount,
        summary.selected_top_cell_payment_share,
        summary.selected_top_three_cell_payment_share,
        summary.selected_visible_payment_hhi,
        summary.selected_visible_service_unit_coverage,
        summary.selected_visible_medicare_payment_coverage,
        summary.selected_visible_standardized_payment_coverage,
        summary.unrepresented_medicare_payment_amount,
        summary.unrepresented_standardized_payment_amount
    from candidate_cell_payment_ranks as candidate
    left join candidate_peer_statistics as peer using (
        data_year,
        supplier_service_release_id,
        supplier_npi,
        hcpcs_code,
        supplier_rental_indicator,
        entity_code
    )
    join code_market_context as market using (
        data_year,
        hcpcs_code,
        supplier_rental_indicator
    )
    join candidate_summary_context as summary using (data_year, supplier_npi)
    cross join settings
), candidate_metric_flags as materialized (
    select
        scored.*,
        case
            when scored.peer_evaluation_status <> 'scoreable'
                then scored.peer_evaluation_status
            when scored.claims_cohort_excluded_scale_source = 'degenerate'
              and scored.services_cohort_excluded_scale_source = 'degenerate'
                then 'degenerate-utilization-scale-descriptive'
            else 'scoreable'
        end as exact_cell_qualification_status,
        coalesce(
            scored.peer_evaluation_status = 'scoreable'
            and scored.claims_cohort_excluded_empirical_percentile
                >= settings.tail_percentile_threshold,
            false
        ) as claims_tail_flag,
        coalesce(
            scored.peer_evaluation_status = 'scoreable'
            and scored.services_cohort_excluded_empirical_percentile
                >= settings.tail_percentile_threshold,
            false
        ) as services_tail_flag,
        coalesce(
            scored.peer_evaluation_status = 'scoreable'
            and scored.medicare_payment_cohort_excluded_empirical_percentile
                >= settings.tail_percentile_threshold,
            false
        ) as raw_payment_tail_flag,
        coalesce(
            scored.peer_evaluation_status = 'scoreable'
            and scored.standardized_payment_cohort_excluded_empirical_percentile
                >= settings.tail_percentile_threshold,
            false
        ) as standardized_payment_tail_flag,
        coalesce(
            scored.peer_evaluation_status = 'scoreable'
            and scored.claims_cohort_excluded_empirical_percentile
                >= settings.tail_percentile_threshold
            and scored.claims_cohort_excluded_robust_z
                >= settings.minimum_robust_z
            and scored.claims_cohort_excluded_median_ratio
                >= settings.minimum_median_ratio,
            false
        ) as claims_robust_tail_flag,
        coalesce(
            scored.peer_evaluation_status = 'scoreable'
            and scored.services_cohort_excluded_empirical_percentile
                >= settings.tail_percentile_threshold
            and scored.services_cohort_excluded_robust_z
                >= settings.minimum_robust_z
            and scored.services_cohort_excluded_median_ratio
                >= settings.minimum_median_ratio,
            false
        ) as services_robust_tail_flag,
        coalesce(
            scored.peer_evaluation_status = 'scoreable'
            and scored.claims_cohort_excluded_empirical_percentile
                >= settings.full_percentile_threshold
            and scored.claims_cohort_excluded_robust_z
                >= settings.minimum_robust_z
            and scored.claims_cohort_excluded_median_ratio
                >= settings.minimum_median_ratio,
            false
        ) as claims_full_outlier_flag,
        coalesce(
            scored.peer_evaluation_status = 'scoreable'
            and scored.services_cohort_excluded_empirical_percentile
                >= settings.full_percentile_threshold
            and scored.services_cohort_excluded_robust_z
                >= settings.minimum_robust_z
            and scored.services_cohort_excluded_median_ratio
                >= settings.minimum_median_ratio,
            false
        ) as services_full_outlier_flag,
        coalesce(
            scored.peer_evaluation_status = 'scoreable'
            and scored.medicare_payment_cohort_excluded_empirical_percentile
                >= settings.full_percentile_threshold
            and scored.medicare_payment_cohort_excluded_robust_z
                >= settings.minimum_robust_z
            and scored.medicare_payment_cohort_excluded_median_ratio
                >= settings.minimum_median_ratio,
            false
        ) as raw_payment_full_outlier_flag,
        coalesce(
            scored.peer_evaluation_status = 'scoreable'
            and scored.standardized_payment_cohort_excluded_empirical_percentile
                >= settings.full_percentile_threshold
            and scored.standardized_payment_cohort_excluded_robust_z
                >= settings.minimum_robust_z
            and scored.standardized_payment_cohort_excluded_median_ratio
                >= settings.minimum_median_ratio,
            false
        ) as standardized_payment_full_outlier_flag,
        coalesce(
            scored.reconstructed_medicare_payment_amount
                >= settings.minimum_observed_raw_payment,
            false
        ) as observed_raw_payment_magnitude_flag,
        coalesce(
            scored.raw_payment_benchmark_exposure_above_q75
                >= settings.minimum_q75_benchmark_exposure,
            false
        ) as raw_payment_q75_exposure_magnitude_flag,
        coalesce(
            scored.standardized_payment_benchmark_exposure_above_q75
                >= settings.minimum_q75_benchmark_exposure,
            false
        ) as standardized_q75_exposure_magnitude_flag
    from candidate_scored as scored
    cross join settings
), candidate_utilization_flags as materialized (
    select
        flagged.*,
        flagged.claims_robust_tail_flag
            or flagged.services_robust_tail_flag
            as utilization_robust_tail_flag,
        flagged.claims_full_outlier_flag
            or flagged.services_full_outlier_flag
            as utilization_full_outlier_flag
    from candidate_metric_flags as flagged
), candidate_annual_qualification as materialized (
    select
        flagged.*,
        flagged.observed_raw_payment_magnitude_flag
            and flagged.raw_payment_q75_exposure_magnitude_flag
            and flagged.standardized_q75_exposure_magnitude_flag
            as exact_cell_magnitude_gates_pass,
        flagged.observed_raw_payment_magnitude_flag
            and flagged.raw_payment_q75_exposure_magnitude_flag
            and flagged.standardized_q75_exposure_magnitude_flag
            and flagged.utilization_robust_tail_flag
            as annual_exact_cell_tail_qualified,
        flagged.observed_raw_payment_magnitude_flag
            and flagged.raw_payment_q75_exposure_magnitude_flag
            and flagged.standardized_q75_exposure_magnitude_flag
            and flagged.utilization_full_outlier_flag
            as annual_exact_cell_full_qualified
    from candidate_utilization_flags as flagged
), candidate_temporal_evidence as materialized (
    select
        annual.*,
        count(*) over exact_cell_trajectory
            as exact_cell_observed_year_count,
        count(*) filter (
            where annual.annual_exact_cell_tail_qualified
        ) over exact_cell_trajectory as exact_cell_tail_year_count,
        count(*) filter (
            where annual.annual_exact_cell_full_qualified
        ) over exact_cell_trajectory as exact_cell_full_year_count,
        bool_or(
            annual.data_year = settings.temporal_focus_year
            and annual.annual_exact_cell_tail_qualified
        ) over exact_cell_trajectory as exact_cell_focus_year_tail_flag,
        bool_or(
            annual.data_year < settings.temporal_focus_year
            and annual.annual_exact_cell_tail_qualified
        ) over exact_cell_trajectory as exact_cell_prior_year_tail_flag,
        bool_or(
            annual.data_year = settings.temporal_focus_year
            and annual.annual_exact_cell_full_qualified
        ) over exact_cell_trajectory as exact_cell_focus_year_full_flag,
        bool_or(
            annual.data_year < settings.temporal_focus_year
            and annual.annual_exact_cell_full_qualified
        ) over exact_cell_trajectory as exact_cell_prior_year_full_flag
    from candidate_annual_qualification as annual
    cross join settings
    window exact_cell_trajectory as (
        partition by
            annual.supplier_npi,
            annual.hcpcs_code,
            annual.supplier_rental_indicator,
            annual.entity_code
    )
), candidate_temporal_qualification as materialized (
    select
        temporal.*,
        temporal.exact_cell_focus_year_tail_flag
            and temporal.exact_cell_prior_year_tail_flag
            as temporal_exact_cell_tail_qualified,
        temporal.exact_cell_focus_year_full_flag
            and temporal.exact_cell_prior_year_full_flag
            as temporal_exact_cell_full_qualified
    from candidate_temporal_evidence as temporal
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
        candidate.*,
        anchor.parameter_guard,
        anchor.release_guard,
        anchor.filter_guard,
        anchor.source_integrity_guard
    from validation_anchor as anchor
    left join candidate_temporal_qualification as candidate on true
)
select
    output.data_year,
    output.supplier_release_id,
    output.supplier_service_release_id,
    output.supplier_service_retrieval_filters,
    output.supplier_npi,
    output.supplier_last_org_name,
    output.supplier_first_name,
    output.entity_code,
    output.supplier_city,
    output.supplier_state,
    output.supplier_state_fips,
    output.supplier_zip5,
    output.supplier_specialty_code,
    output.supplier_specialty_description,
    output.supplier_specialty_source,
    output.rbcs_level,
    output.rbcs_id,
    output.rbcs_description,
    output.hcpcs_code,
    output.hcpcs_description,
    output.supplier_rental_indicator,
    output.total_beneficiaries,
    output.known_beneficiary_flag,
    output.total_claims,
    output.total_services,
    output.average_submitted_charge,
    output.average_medicare_allowed_amount,
    output.average_medicare_payment_amount,
    output.average_medicare_standardized_amount,
    output.reconstructed_submitted_charge,
    output.reconstructed_medicare_allowed_amount,
    output.reconstructed_medicare_payment_amount,
    output.reconstructed_medicare_standardized_payment_amount,
    output.services_per_beneficiary,
    output.claims_per_beneficiary,
    output.medicare_payment_per_beneficiary,
    output.standardized_payment_per_beneficiary,
    output.cohort_excluded_peer_count,
    output.peer_evaluation_status,
    output.exact_cell_qualification_status,
    output.claims_peer_median,
    output.claims_peer_q75,
    output.claims_peer_iqr,
    output.claims_peer_mad,
    output.claims_cohort_excluded_high_rank,
    100 * output.claims_cohort_excluded_empirical_percentile
        as claims_cohort_excluded_empirical_percentile,
    output.claims_cohort_excluded_median_ratio,
    output.claims_cohort_excluded_median_absolute_delta,
    output.claims_cohort_excluded_robust_z,
    output.claims_cohort_excluded_scale_source,
    output.services_peer_median,
    output.services_peer_q75,
    output.services_peer_iqr,
    output.services_peer_mad,
    output.services_cohort_excluded_high_rank,
    100 * output.services_cohort_excluded_empirical_percentile
        as services_cohort_excluded_empirical_percentile,
    output.services_cohort_excluded_median_ratio,
    output.services_cohort_excluded_median_absolute_delta,
    output.services_cohort_excluded_robust_z,
    output.services_cohort_excluded_scale_source,
    output.medicare_payment_peer_median,
    output.medicare_payment_peer_q75,
    output.medicare_payment_peer_iqr,
    output.medicare_payment_peer_mad,
    output.medicare_payment_cohort_excluded_high_rank,
    100 * output.medicare_payment_cohort_excluded_empirical_percentile
        as medicare_payment_cohort_excluded_empirical_percentile,
    output.medicare_payment_cohort_excluded_median_ratio,
    output.medicare_payment_cohort_excluded_median_absolute_delta,
    output.medicare_payment_cohort_excluded_robust_z,
    output.medicare_payment_cohort_excluded_scale_source,
    output.standardized_payment_peer_median,
    output.standardized_payment_peer_q75,
    output.standardized_payment_peer_iqr,
    output.standardized_payment_peer_mad,
    output.standardized_payment_cohort_excluded_high_rank,
    100 * output.standardized_payment_cohort_excluded_empirical_percentile
        as standardized_payment_cohort_excluded_empirical_percentile,
    output.standardized_payment_cohort_excluded_median_ratio,
    output.standardized_payment_cohort_excluded_median_absolute_delta,
    output.standardized_payment_cohort_excluded_robust_z,
    output.standardized_payment_cohort_excluded_scale_source,
    output.raw_payment_benchmark_exposure_above_q75,
    output.standardized_payment_benchmark_exposure_above_q75,
    output.claims_tail_flag,
    output.services_tail_flag,
    output.raw_payment_tail_flag,
    output.standardized_payment_tail_flag,
    output.claims_robust_tail_flag,
    output.services_robust_tail_flag,
    output.utilization_robust_tail_flag,
    output.claims_full_outlier_flag,
    output.services_full_outlier_flag,
    output.raw_payment_full_outlier_flag,
    output.standardized_payment_full_outlier_flag,
    output.utilization_full_outlier_flag,
    output.observed_raw_payment_magnitude_flag,
    output.raw_payment_q75_exposure_magnitude_flag,
    output.standardized_q75_exposure_magnitude_flag,
    output.exact_cell_magnitude_gates_pass,
    output.annual_exact_cell_tail_qualified,
    output.annual_exact_cell_full_qualified,
    output.exact_cell_observed_year_count,
    output.exact_cell_tail_year_count,
    output.exact_cell_full_year_count,
    output.exact_cell_focus_year_tail_flag,
    output.exact_cell_prior_year_tail_flag,
    output.exact_cell_focus_year_full_flag,
    output.exact_cell_prior_year_full_flag,
    output.temporal_exact_cell_tail_qualified,
    output.temporal_exact_cell_full_qualified,
    output.state_peer_geography,
    output.state_cohort_excluded_peer_count,
    output.state_peer_evaluation_status,
    output.state_services_peer_median,
    output.state_services_peer_q75,
    output.state_services_cohort_excluded_high_rank,
    100 * output.state_services_cohort_excluded_empirical_percentile
        as state_services_cohort_excluded_empirical_percentile,
    output.state_medicare_payment_peer_q75,
    output.state_standardized_payment_peer_q75,
    output.state_raw_payment_benchmark_exposure_above_q75,
    output.state_standardized_payment_benchmark_exposure_above_q75,
    output.visible_cell_count,
    output.known_beneficiary_cell_count,
    output.suppressed_or_unknown_beneficiary_cell_count,
    output.candidate_cell_visible_national_payment_share,
    output.candidate_cell_visible_national_standardized_payment_share,
    output.candidate_cell_visible_national_service_share,
    output.source_address_florida_visible_payment_share,
    output.source_address_florida_visible_service_share,
    output.source_address_florida_visible_standardized_payment_share,
    output.candidate_cohort_visible_payment_share,
    output.candidate_cohort_visible_service_share,
    output.candidate_cohort_visible_standardized_payment_share,
    output.summary_total_hcpcs_codes,
    output.summary_total_beneficiaries,
    output.summary_total_claims,
    output.summary_total_services,
    output.summary_total_submitted_charge,
    output.summary_total_medicare_allowed_amount,
    output.summary_total_medicare_payment_amount,
    output.summary_total_medicare_standardized_payment_amount,
    output.selected_visible_cell_count,
    output.selected_visible_hcpcs_count,
    output.selected_visible_services,
    output.selected_visible_submitted_charge,
    output.selected_visible_medicare_allowed_amount,
    output.selected_visible_medicare_payment_amount,
    output.selected_visible_medicare_standardized_payment_amount,
    output.selected_visible_service_unit_coverage,
    output.selected_visible_medicare_payment_coverage,
    output.selected_visible_standardized_payment_coverage,
    output.unrepresented_medicare_payment_amount,
    output.unrepresented_standardized_payment_amount,
    output.selected_cell_payment_rank,
    output.selected_top_cell_payment_share,
    output.selected_top_three_cell_payment_share,
    output.selected_visible_payment_hhi,
    settings.minimum_peer_count,
    array_to_string(settings.data_years, ';') as requested_data_years,
    'dmepos-supplier-service-exact-cell'::text as exact_cell_algorithm,
    '1'::text as exact_cell_algorithm_version,
    settings.minimum_observed_raw_payment,
    settings.minimum_q75_benchmark_exposure,
    100 * settings.tail_percentile_threshold as tail_percentile_threshold,
    100 * settings.full_percentile_threshold as full_percentile_threshold,
    settings.minimum_robust_z,
    settings.minimum_median_ratio,
    settings.temporal_focus_year,
    settings.robust_scale_relative_tolerance,
    'Service counts are HCPCS-defined product or service units, not visits; beneficiary and claim counts must not be summed across cells.'
        as unit_caveat,
    'National, Florida-address, and cohort shares use only visible published supplier-service cells; suppressed cells are absent and supplier address is not beneficiary or furnishing geography.'
        as visible_market_caveat,
    'Selected-code coverage and concentration describe only the requested retained HCPCS universe and are not a complete supplier portfolio unless every summary code is visibly represented.'
        as reconciliation_caveat,
    'National and supplier-address-state Q75 benchmark exposures are distances above separately labeled cohort-excluded exact-code peer benchmarks, not improper-payment, loss, damages, or recovery estimates; supplier address is not beneficiary or furnishing geography.'
        as benchmark_caveat,
    'Exact-cell qualification uses fixed $100,000 observed raw-payment and Q75-exposure gates plus an independent claims-or-services lane meeting the percentile, robust-z, and median-ratio gates; payment percentile flags are descriptive. A temporal result requires the identical NPI, HCPCS, rental, and entity cell in the requested focus year and at least one earlier requested year.'
        as exact_cell_qualification_caveat
from validated_output as output
cross join settings
where output.parameter_guard = 1
  and output.release_guard = 1
  and output.filter_guard = 1
  and output.source_integrity_guard = 1
  and output.supplier_npi is not null
order by
    output.data_year,
    output.supplier_npi,
    output.reconstructed_medicare_payment_amount desc,
    output.hcpcs_code,
    output.supplier_rental_indicator;
