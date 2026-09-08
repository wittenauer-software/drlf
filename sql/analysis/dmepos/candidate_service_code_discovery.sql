-- Discover and freeze a bounded HCPCS union for a previously selected DMEPOS
-- supplier cohort. This is a descriptive portfolio-decomposition step, not a
-- national peer analysis: the Supplier-and-Service input must be filtered to
-- exactly the requested NPIs and must not be filtered by HCPCS, geography, or
-- any other field.
--
-- Output grain is every candidate NPI x HCPCS after aggregating rental cells;
-- a boolean marks the rows whose HCPCS enters the frozen union. Beneficiary
-- counts are intentionally not summed, and the claim-cell sum is explicitly
-- labeled nonunique because one claim can overlap rental cells. Reconstructed
-- amounts use published service units multiplied by published averages; they
-- are not supplier income, improper payment, loss, damages, or recovery.
with settings as materialized (
    select
        %(focus_year)s::smallint as focus_year,
        %(candidate_npis)s::text[] as candidate_npis,
        %(top_codes_per_candidate)s::integer as top_codes_per_candidate,
        %(min_cohort_code_payment)s::numeric as minimum_cohort_code_payment,
        %(target_candidate_visible_payment_share)s::numeric
            as target_candidate_visible_payment_share,
        %(min_visible_summary_payment_coverage)s::numeric
            as minimum_visible_summary_payment_coverage,
        %(max_selected_codes)s::integer as maximum_selected_codes,
        %(source_release_ids)s::bigint[] as registered_source_release_ids
), raw_candidate_npis as (
    select btrim(requested.supplier_npi) as supplier_npi
    from settings
    cross join lateral unnest(settings.candidate_npis) as requested(supplier_npi)
), parameter_validity as (
    select
        settings.focus_year in (2022, 2023, 2024)
        and cardinality(settings.candidate_npis) between 1 and 100
        and cardinality(settings.candidate_npis) = (
            select count(distinct raw.supplier_npi) from raw_candidate_npis as raw
        )
        and not exists (
            select 1
            from raw_candidate_npis as raw
            where raw.supplier_npi is null
               or raw.supplier_npi !~ '^[0-9]{10}$'
        )
        and settings.top_codes_per_candidate between 1 and 25
        and settings.minimum_cohort_code_payment >= 0
        and settings.minimum_cohort_code_payment < 'Infinity'::numeric
        and settings.minimum_cohort_code_payment <> 'NaN'::numeric
        and settings.target_candidate_visible_payment_share > 0
        and settings.target_candidate_visible_payment_share <= 1
        and settings.target_candidate_visible_payment_share <> 'NaN'::numeric
        and settings.minimum_visible_summary_payment_coverage > 0
        and settings.minimum_visible_summary_payment_coverage <= 1
        and settings.minimum_visible_summary_payment_coverage <> 'NaN'::numeric
        and settings.maximum_selected_codes between 1 and 100
        and settings.top_codes_per_candidate <= settings.maximum_selected_codes
        as valid
    from settings
), parameter_gate as materialized (
    select case
        when valid then 1
        else (
            'DMEPOS candidate-code parameters failed validation; require one '
            || 'pinned 2022-2024 focus year, one to 100 unique ten-digit NPIs, '
            || 'top N between one and 25, finite nonnegative cohort payment, '
            || 'shares in (0,1], and a code cap between top N and 100; validity='
            || coalesce(valid::text, 'null')
        )::integer
    end as guard
    from parameter_validity
), candidate_npis as materialized (
    select distinct raw.supplier_npi
    from raw_candidate_npis as raw
    cross join parameter_gate
    where parameter_gate.guard = 1
), expected_service_filter as materialized (
    select jsonb_build_object(
        'Suplr_NPI', jsonb_agg(candidate.supplier_npi order by candidate.supplier_npi)
    ) as retrieval_filters
    from candidate_npis as candidate
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
    from settings
    cross join lateral unnest(settings.registered_source_release_ids)
        as requested(source_release_id)
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
            and release.data_year = settings.focus_year
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
    cross join settings
    left join metadata.source_release as release
        on release.source_release_id = input.source_release_id
    left join metadata.source_dataset as dataset
        on dataset.dataset_id = release.dataset_id
    left join pinned_versions as pinned
        on pinned.data_year = release.data_year
), release_coverage as materialized (
    select
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
    from audited_releases as audit
), release_contract_validity as (
    select
        (select count(*) from input_release_ids) = 2
        and (select count(distinct source_release_id) from input_release_ids) = 2
        and not exists (
            select 1 from audited_releases where eligible is not true
        )
        and coverage.supplier_release_count = 1
        and coverage.supplier_service_release_count = 1 as valid,
        coverage.supplier_release_count,
        coverage.supplier_service_release_count
    from release_coverage as coverage
), release_gate as materialized (
    select case
        when valid then 1
        else (
            'DMEPOS candidate-code release validation failed; expected exactly '
            || 'one loaded pinned CMS by-Supplier release and one loaded pinned '
            || 'CMS by-Supplier-and-Service release for the focus year; observed '
            || 'supplier=' || supplier_release_count::text
            || ',supplier-service=' || supplier_service_release_count::text
            || ',validity=' || coalesce(valid::text, 'null')
        )::integer
    end as guard
    from release_contract_validity
), bound_releases as materialized (
    select
        coverage.supplier_release_id,
        coverage.supplier_service_release_id,
        supplier.retrieval_filters as supplier_retrieval_filters,
        service.retrieval_filters as supplier_service_retrieval_filters
    from release_coverage as coverage
    join audited_releases as supplier
        on supplier.source_release_id = coverage.supplier_release_id
    join audited_releases as service
        on service.source_release_id = coverage.supplier_service_release_id
    cross join release_gate
    where release_gate.guard = 1
), filter_contract_validity as (
    select
        release.supplier_retrieval_filters = '{}'::jsonb
        and release.supplier_service_retrieval_filters = expected.retrieval_filters as valid,
        release.supplier_retrieval_filters,
        release.supplier_service_retrieval_filters,
        expected.retrieval_filters as expected_service_retrieval_filters
    from bound_releases as release
    cross join expected_service_filter as expected
), filter_gate as materialized (
    select case
        when valid then 1
        else (
            'DMEPOS candidate-code retrieval-filter validation failed; the '
            || 'summary release must be unfiltered and the service release must '
            || 'use only the exact candidate-NPI set; supplier='
            || supplier_retrieval_filters::text || ',supplier-service='
            || supplier_service_retrieval_filters::text || ',expected='
            || expected_service_retrieval_filters::text || ',validity='
            || coalesce(valid::text, 'null')
        )::integer
    end as guard
    from filter_contract_validity
), validated_releases as materialized (
    select release.*
    from bound_releases as release
    cross join filter_gate
    where filter_gate.guard = 1
), candidate_summary_rows as materialized (
    select
        supplier.source_release_id,
        supplier.data_year,
        supplier.supplier_npi,
        supplier.entity_code,
        supplier.total_hcpcs_codes,
        supplier.total_claims,
        supplier.total_services,
        supplier.total_submitted_charge,
        supplier.total_medicare_allowed_amount,
        supplier.total_medicare_payment_amount,
        supplier.total_medicare_standardized_payment_amount
    from validated_releases as release
    join claims.dmepos_supplier as supplier
        on supplier.source_release_id = release.supplier_release_id
    join candidate_npis as candidate
        on candidate.supplier_npi = supplier.supplier_npi
    cross join settings
    where supplier.data_year = settings.focus_year
), candidate_service_cells as materialized (
    select
        service.source_release_id,
        service.data_year,
        service.supplier_npi,
        service.entity_code,
        service.hcpcs_code,
        service.hcpcs_description,
        service.supplier_rental_indicator,
        service.total_claims,
        service.total_services,
        service.total_services * service.average_submitted_charge
            as reconstructed_submitted_charge,
        service.total_services * service.average_medicare_allowed_amount
            as reconstructed_medicare_allowed_amount,
        service.total_services * service.average_medicare_payment_amount
            as reconstructed_medicare_payment_amount,
        service.total_services * service.average_medicare_standardized_amount
            as reconstructed_medicare_standardized_payment_amount
    from validated_releases as release
    join claims.dmepos_supplier_service as service
        on service.source_release_id = release.supplier_service_release_id
    cross join settings
    where service.data_year = settings.focus_year
), duplicate_summary_rows as (
    select summary.supplier_npi
    from candidate_summary_rows as summary
    group by summary.supplier_npi
    having count(*) > 1
), duplicate_service_cells as (
    select
        service.supplier_npi,
        service.hcpcs_code,
        service.supplier_rental_indicator
    from candidate_service_cells as service
    group by
        service.supplier_npi,
        service.hcpcs_code,
        service.supplier_rental_indicator
    having count(*) > 1
), inconsistent_service_codes as (
    select service.supplier_npi, service.hcpcs_code
    from candidate_service_cells as service
    group by service.supplier_npi, service.hcpcs_code
    having count(distinct service.entity_code) > 1
        or count(distinct service.hcpcs_description) > 1
), source_integrity_validity as (
    select
        not exists (select 1 from duplicate_summary_rows)
        and not exists (select 1 from duplicate_service_cells)
        and not exists (select 1 from inconsistent_service_codes)
        and (select count(*) from candidate_summary_rows)
            = (select count(*) from candidate_npis)
        and (
            select count(distinct service.supplier_npi)
            from candidate_service_cells as service
        ) = (select count(*) from candidate_npis)
        and not exists (
            select 1
            from candidate_service_cells as service
            left join candidate_npis as candidate
                on candidate.supplier_npi = service.supplier_npi
            where candidate.supplier_npi is null
        )
        and not exists (
            select 1
            from candidate_service_cells as service
            join candidate_summary_rows as summary
                on summary.supplier_npi = service.supplier_npi
            where service.entity_code <> summary.entity_code
        )
        and not exists (
            select 1
            from validated_releases as release
            where not exists (
                select 1
                from metadata.ingestion_run as run
                where run.source_release_id = release.supplier_release_id
                  and run.status = 'succeeded'
                  and run.loaded_rows = (
                      select count(*)
                      from claims.dmepos_supplier as supplier
                      where supplier.source_release_id = release.supplier_release_id
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
                      )
               )
        ) as valid
), source_integrity_gate as materialized (
    select case
        when valid then 1
        else (
            'DMEPOS candidate-code source integrity failed; require complete '
            || 'loaded releases, one summary row and at least one service row per '
            || 'candidate, exact candidate service scope, unique canonical cells, '
            || 'and consistent entity/code labels; validity='
            || coalesce(valid::text, 'null')
        )::integer
    end as guard
    from source_integrity_validity
), candidate_codes as materialized (
    select
        service.supplier_npi,
        min(service.entity_code) as entity_code,
        service.hcpcs_code,
        min(service.hcpcs_description) as hcpcs_description,
        count(*)::bigint as rental_cell_count,
        string_agg(
            service.supplier_rental_indicator,
            ',' order by service.supplier_rental_indicator
        ) as rental_indicators,
        sum(service.total_claims)::bigint as nonunique_cell_claim_count_sum,
        sum(service.total_services) as total_services,
        sum(service.reconstructed_submitted_charge)
            as reconstructed_submitted_charge,
        sum(service.reconstructed_medicare_allowed_amount)
            as reconstructed_medicare_allowed_amount,
        sum(service.reconstructed_medicare_payment_amount)
            as reconstructed_medicare_payment_amount,
        sum(service.reconstructed_medicare_standardized_payment_amount)
            as reconstructed_medicare_standardized_payment_amount
    from candidate_service_cells as service
    cross join source_integrity_gate
    where source_integrity_gate.guard = 1
    group by service.supplier_npi, service.hcpcs_code
), candidate_visible_portfolios as materialized (
    select
        code.supplier_npi,
        sum(code.nonunique_cell_claim_count_sum)::bigint
            as visible_detail_nonunique_cell_claim_count_sum,
        sum(code.total_services) as visible_detail_total_services,
        sum(code.reconstructed_submitted_charge)
            as visible_detail_reconstructed_submitted_charge,
        sum(code.reconstructed_medicare_allowed_amount)
            as visible_detail_reconstructed_medicare_allowed_amount,
        sum(code.reconstructed_medicare_payment_amount)
            as visible_detail_reconstructed_medicare_payment_amount,
        sum(code.reconstructed_medicare_standardized_payment_amount)
            as visible_detail_reconstructed_standardized_payment_amount
    from candidate_codes as code
    group by code.supplier_npi
), reconciled_candidates as materialized (
    select
        summary.*,
        portfolio.visible_detail_nonunique_cell_claim_count_sum,
        portfolio.visible_detail_total_services,
        portfolio.visible_detail_reconstructed_submitted_charge,
        portfolio.visible_detail_reconstructed_medicare_allowed_amount,
        portfolio.visible_detail_reconstructed_medicare_payment_amount,
        portfolio.visible_detail_reconstructed_standardized_payment_amount,
        portfolio.visible_detail_reconstructed_submitted_charge
            / nullif(summary.total_submitted_charge, 0)
            as visible_summary_submitted_charge_coverage,
        portfolio.visible_detail_reconstructed_medicare_allowed_amount
            / nullif(summary.total_medicare_allowed_amount, 0)
            as visible_summary_allowed_amount_coverage,
        portfolio.visible_detail_reconstructed_medicare_payment_amount
            / nullif(summary.total_medicare_payment_amount, 0)
            as visible_summary_payment_coverage,
        portfolio.visible_detail_reconstructed_standardized_payment_amount
            / nullif(summary.total_medicare_standardized_payment_amount, 0)
            as visible_summary_standardized_payment_coverage
    from candidate_summary_rows as summary
    join candidate_visible_portfolios as portfolio
        on portfolio.supplier_npi = summary.supplier_npi
), coverage_validity as (
    select
        count(*) = (select count(*) from candidate_npis)
        and bool_and(total_medicare_payment_amount > 0)
        and bool_and(visible_detail_reconstructed_medicare_payment_amount > 0)
        and bool_and(
            visible_summary_payment_coverage
                >= settings.minimum_visible_summary_payment_coverage
        ) as valid,
        min(visible_summary_payment_coverage) as observed_minimum_coverage
    from reconciled_candidates
    cross join settings
), coverage_gate as materialized (
    select case
        when valid then 1
        else (
            'DMEPOS candidate-code visible detail coverage failed; every '
            || 'candidate must have positive summary/detail Medicare payment and '
            || 'coverage at or above the declared floor; observed minimum='
            || coalesce(observed_minimum_coverage::text, 'null')
            || ',validity=' || coalesce(valid::text, 'null')
        )::integer
    end as guard
    from coverage_validity
), ranked_candidate_codes as materialized (
    select
        code.*,
        row_number() over (
            partition by code.supplier_npi
            order by
                code.reconstructed_medicare_payment_amount desc,
                code.hcpcs_code
        )::bigint as candidate_payment_rank,
        coalesce(
            sum(code.reconstructed_medicare_payment_amount) over (
                partition by code.supplier_npi
                order by
                    code.reconstructed_medicare_payment_amount desc,
                    code.hcpcs_code
                rows between unbounded preceding and 1 preceding
            ),
            0
        ) as candidate_prior_cumulative_visible_payment,
        sum(code.reconstructed_medicare_payment_amount) over (
            partition by code.supplier_npi
            order by
                code.reconstructed_medicare_payment_amount desc,
                code.hcpcs_code
            rows between unbounded preceding and current row
        ) as candidate_after_cumulative_visible_payment
    from candidate_codes as code
    cross join coverage_gate
    where coverage_gate.guard = 1
), cohort_code_totals as materialized (
    select
        code.hcpcs_code,
        sum(code.reconstructed_medicare_payment_amount)
            as cohort_reconstructed_medicare_payment_amount,
        sum(code.reconstructed_medicare_standardized_payment_amount)
            as cohort_reconstructed_medicare_standardized_payment_amount
    from candidate_codes as code
    group by code.hcpcs_code
), evaluated_candidate_codes as materialized (
    select
        ranked.*,
        cohort.cohort_reconstructed_medicare_payment_amount,
        cohort.cohort_reconstructed_medicare_standardized_payment_amount,
        ranked.candidate_prior_cumulative_visible_payment
            / portfolio.visible_detail_reconstructed_medicare_payment_amount
            as candidate_prior_cumulative_visible_payment_share,
        ranked.candidate_after_cumulative_visible_payment
            / portfolio.visible_detail_reconstructed_medicare_payment_amount
            as candidate_after_cumulative_visible_payment_share,
        ranked.candidate_payment_rank <= settings.top_codes_per_candidate
            as selected_by_candidate_top_n,
        cohort.cohort_reconstructed_medicare_payment_amount
            >= settings.minimum_cohort_code_payment
            as selected_by_cohort_payment,
        ranked.candidate_prior_cumulative_visible_payment
            / portfolio.visible_detail_reconstructed_medicare_payment_amount
            < settings.target_candidate_visible_payment_share
            as selected_by_candidate_coverage
    from ranked_candidate_codes as ranked
    join candidate_visible_portfolios as portfolio
        on portfolio.supplier_npi = ranked.supplier_npi
    join cohort_code_totals as cohort
        on cohort.hcpcs_code = ranked.hcpcs_code
    cross join settings
), selected_candidate_codes as materialized (
    select evaluated.*
    from evaluated_candidate_codes as evaluated
    where evaluated.selected_by_candidate_top_n
       or evaluated.selected_by_cohort_payment
       or evaluated.selected_by_candidate_coverage
), selected_code_union as materialized (
    select distinct selected.hcpcs_code
    from selected_candidate_codes as selected
), selected_code_validity as (
    select
        count(*) between 1 and settings.maximum_selected_codes as valid,
        count(*) as selected_code_count
    from selected_code_union
    cross join settings
    group by settings.maximum_selected_codes
), selected_code_gate as materialized (
    select case
        when valid then 1
        else (
            'DMEPOS candidate-code union exceeded its declared bound or was '
            || 'empty; selected=' || selected_code_count::text || ',validity='
            || coalesce(valid::text, 'null')
        )::integer
    end as guard
    from selected_code_validity
), canonical_parameters as materialized (
    select
        '{' || string_agg(candidate.supplier_npi, ',' order by candidate.supplier_npi)
            || '}' as candidate_npis_parameter,
        (
            select '{' || string_agg(
                input.source_release_id::text,
                ',' order by input.source_release_id
            ) || '}'
            from input_release_ids as input
        ) as source_release_ids_parameter
    from candidate_npis as candidate
), validation_anchor as materialized (
    select
        parameter_gate.guard as parameter_guard,
        release_gate.guard as release_guard,
        filter_gate.guard as filter_guard,
        source_integrity_gate.guard as source_integrity_guard,
        coverage_gate.guard as coverage_guard,
        selected_code_gate.guard as selected_code_guard
    from parameter_gate
    cross join release_gate
    cross join filter_gate
    cross join source_integrity_gate
    cross join coverage_gate
    cross join selected_code_gate
), output_rows as materialized (
    select
        'dmepos-candidate-service-code-discovery'::text as code_discovery_algorithm,
        '1'::text as code_discovery_algorithm_version,
        settings.focus_year,
        canonical.candidate_npis_parameter,
        canonical.source_release_ids_parameter,
        settings.top_codes_per_candidate,
        settings.minimum_cohort_code_payment as min_cohort_code_payment,
        settings.target_candidate_visible_payment_share,
        settings.minimum_visible_summary_payment_coverage
            as min_visible_summary_payment_coverage,
        settings.maximum_selected_codes,
        release.supplier_release_id as supplier_summary_source_release_id,
        release.supplier_service_release_id as supplier_service_source_release_id,
        evaluated.supplier_npi,
        evaluated.entity_code,
        evaluated.hcpcs_code,
        evaluated.hcpcs_description,
        evaluated.rental_cell_count,
        evaluated.rental_indicators,
        evaluated.nonunique_cell_claim_count_sum,
        evaluated.total_services,
        evaluated.reconstructed_submitted_charge,
        evaluated.reconstructed_medicare_allowed_amount,
        evaluated.reconstructed_medicare_payment_amount,
        evaluated.reconstructed_medicare_standardized_payment_amount,
        summary.total_submitted_charge as summary_total_submitted_charge,
        summary.total_medicare_allowed_amount as summary_total_medicare_allowed_amount,
        summary.total_medicare_payment_amount as summary_total_medicare_payment_amount,
        summary.total_medicare_standardized_payment_amount
            as summary_total_medicare_standardized_payment_amount,
        summary.visible_detail_reconstructed_submitted_charge,
        summary.visible_detail_reconstructed_medicare_allowed_amount,
        summary.visible_detail_reconstructed_medicare_payment_amount,
        summary.visible_detail_reconstructed_standardized_payment_amount,
        summary.visible_summary_submitted_charge_coverage,
        summary.visible_summary_allowed_amount_coverage,
        summary.visible_summary_payment_coverage,
        summary.visible_summary_standardized_payment_coverage,
        evaluated.candidate_payment_rank,
        evaluated.candidate_prior_cumulative_visible_payment,
        evaluated.candidate_after_cumulative_visible_payment,
        evaluated.candidate_prior_cumulative_visible_payment_share,
        evaluated.candidate_after_cumulative_visible_payment_share,
        evaluated.cohort_reconstructed_medicare_payment_amount,
        evaluated.cohort_reconstructed_medicare_standardized_payment_amount,
        evaluated.selected_by_candidate_top_n,
        evaluated.selected_by_cohort_payment,
        evaluated.selected_by_candidate_coverage,
        (
            evaluated.selected_by_candidate_top_n
            or evaluated.selected_by_cohort_payment
            or evaluated.selected_by_candidate_coverage
        ) as selected_for_code_freeze,
        concat_ws(
            '|',
            case when evaluated.selected_by_candidate_top_n then 'candidate-top-n' end,
            case when evaluated.selected_by_cohort_payment then 'cohort-payment-floor' end,
            case when evaluated.selected_by_candidate_coverage then 'candidate-coverage-target' end
        ) as selection_reason,
        'Visible public DMEPOS aggregates only. Reconstructed amounts and '
            || 'portfolio differences are descriptive; they are not supplier '
            || 'income, improper payment, loss, damages, or recovery.'
            as monetary_caveat,
        anchor.parameter_guard,
        anchor.release_guard,
        anchor.filter_guard,
        anchor.source_integrity_guard,
        anchor.coverage_guard,
        anchor.selected_code_guard
    from validation_anchor as anchor
    left join evaluated_candidate_codes as evaluated on true
    cross join settings
    cross join validated_releases as release
    cross join canonical_parameters as canonical
    left join reconciled_candidates as summary
        on summary.supplier_npi = evaluated.supplier_npi
)
select
    output.code_discovery_algorithm,
    output.code_discovery_algorithm_version,
    output.focus_year,
    output.candidate_npis_parameter,
    output.source_release_ids_parameter,
    output.top_codes_per_candidate,
    output.min_cohort_code_payment,
    output.target_candidate_visible_payment_share,
    output.min_visible_summary_payment_coverage,
    output.maximum_selected_codes,
    output.supplier_summary_source_release_id,
    output.supplier_service_source_release_id,
    output.supplier_npi,
    output.entity_code,
    output.hcpcs_code,
    output.hcpcs_description,
    output.rental_cell_count,
    output.rental_indicators,
    output.nonunique_cell_claim_count_sum,
    output.total_services,
    output.reconstructed_submitted_charge,
    output.reconstructed_medicare_allowed_amount,
    output.reconstructed_medicare_payment_amount,
    output.reconstructed_medicare_standardized_payment_amount,
    output.summary_total_submitted_charge,
    output.summary_total_medicare_allowed_amount,
    output.summary_total_medicare_payment_amount,
    output.summary_total_medicare_standardized_payment_amount,
    output.visible_detail_reconstructed_submitted_charge,
    output.visible_detail_reconstructed_medicare_allowed_amount,
    output.visible_detail_reconstructed_medicare_payment_amount,
    output.visible_detail_reconstructed_standardized_payment_amount,
    output.visible_summary_submitted_charge_coverage,
    output.visible_summary_allowed_amount_coverage,
    output.visible_summary_payment_coverage,
    output.visible_summary_standardized_payment_coverage,
    output.candidate_payment_rank,
    output.candidate_prior_cumulative_visible_payment,
    output.candidate_after_cumulative_visible_payment,
    output.candidate_prior_cumulative_visible_payment_share,
    output.candidate_after_cumulative_visible_payment_share,
    output.cohort_reconstructed_medicare_payment_amount,
    output.cohort_reconstructed_medicare_standardized_payment_amount,
    output.selected_by_candidate_top_n,
    output.selected_by_cohort_payment,
    output.selected_by_candidate_coverage,
    output.selected_for_code_freeze,
    output.selection_reason,
    output.monetary_caveat
from output_rows as output
where output.parameter_guard = 1
  and output.release_guard = 1
  and output.filter_guard = 1
  and output.source_integrity_guard = 1
  and output.coverage_guard = 1
  and output.selected_code_guard = 1
  and output.supplier_npi is not null
order by output.hcpcs_code, output.supplier_npi
