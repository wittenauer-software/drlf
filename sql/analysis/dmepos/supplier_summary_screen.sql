-- Prospectively declared DMEPOS supplier-summary materiality screen.
--
-- This query produces one anonymous supplier/year row. It deliberately omits
-- supplier names and addresses; supplier_npi is retained only as the stable
-- opaque key needed for temporal analysis and a later, separately frozen
-- identity join. Reconstructed and peer-benchmark exposures are prioritization
-- measures. They are not provider income, improper payments, loss, or damages.
-- The final two-field algorithm handshake must change whenever maintained
-- classification or routing semantics change; the shortlist validates it
-- exactly at the SQL-to-shortlist boundary before applying the corresponding
-- screen label.
with settings as (
    select
        %(data_years)s::smallint[] as data_years,
        %(min_panel_beneficiaries)s::bigint as minimum_panel_beneficiaries,
        %(min_peer_count)s::bigint as minimum_peer_count,
        %(min_annual_payment)s::numeric as minimum_annual_payment,
        %(min_benchmark_exposure)s::numeric as minimum_benchmark_exposure,
        %(tail_percentile)s::numeric as tail_percentile_threshold,
        %(full_percentile)s::numeric as full_percentile_threshold,
        %(min_robust_z)s::numeric as minimum_robust_z,
        %(min_ratio)s::numeric as minimum_ratio,
        %(latest_year)s::smallint as latest_year,
        0.000000000001::numeric as robust_scale_relative_tolerance
), parameter_gate as (
    select 1 / case
        when data_years = array[2022, 2023, 2024]::smallint[]
          and latest_year = 2024
          and minimum_panel_beneficiaries = 100
          and minimum_peer_count = 100
          and minimum_annual_payment = 100000
          and minimum_benchmark_exposure = 100000
          and tail_percentile_threshold = 0.975
          and full_percentile_threshold = 0.99
          and minimum_robust_z = 3.5
          and minimum_ratio = 2
        then 1 else 0
    end as guard
    from settings
), requested_years as materialized (
    select unnest(settings.data_years)::smallint as data_year
    from settings
    cross join parameter_gate
    where parameter_gate.guard = 1
), input_release_ids as (
    select release_id
    from unnest(%(source_release_ids)s::bigint[]) as release_id
), audited_releases as (
    select
        input.release_id,
        release.data_year,
        dataset.slug,
        release.status,
        release.source_release_id is not null
            and release.status = 'loaded'
            and dataset.source = 'Centers for Medicare & Medicaid Services'
            and dataset.slug
                = 'medicare-durable-medical-equipment-devices-supplies-by-supplier'
            and release.data_year = any(settings.data_years)
            and (release.data_year, release.version_id) in (
                (2022, '471f9c0d-12c4-4601-a547-559dcfa2d0e2'),
                (2023, '7bb52a09-9eba-43f9-b7ec-57f65fbbc86c'),
                (2024, '4c6cfc68-a149-4bfe-b934-db2b92d0f360')
            )
            as eligible
    from input_release_ids as input
    left join metadata.source_release as release
        on release.source_release_id = input.release_id
    left join metadata.source_dataset as dataset
        on dataset.dataset_id = release.dataset_id
    cross join settings
), release_coverage as (
    select
        year.data_year,
        count(audit.release_id) filter (where audit.eligible) as eligible_release_count,
        max(audit.release_id) filter (where audit.eligible) as source_release_id
    from requested_years as year
    left join audited_releases as audit
        on audit.data_year = year.data_year
    group by year.data_year
), release_gate as (
    select 1 / case
        when (select count(*) from input_release_ids) = 3
          and (select count(distinct release_id) from input_release_ids) = 3
          and not exists (select 1 from audited_releases where eligible is not true)
          and coalesce(bool_and(eligible_release_count = 1), false)
        then 1 else 0
    end as guard
    from release_coverage
), validated_releases as materialized (
    select coverage.data_year, coverage.source_release_id
    from release_coverage as coverage
    cross join release_gate
    where release_gate.guard = 1
), raw_supplier_rows as materialized (
    select
        supplier.source_release_id,
        supplier.data_year,
        supplier.supplier_npi,
        supplier.entity_code,
        supplier.supplier_specialty_description,
        supplier.supplier_specialty_source,
        supplier.total_hcpcs_codes,
        supplier.total_beneficiaries,
        supplier.total_claims,
        supplier.total_services,
        supplier.total_submitted_charge,
        supplier.total_medicare_allowed_amount,
        supplier.total_medicare_payment_amount,
        supplier.total_medicare_standardized_payment_amount,
        supplier.dme_medicare_payment_amount,
        supplier.pos_medicare_payment_amount,
        supplier.drug_medicare_payment_amount
    from validated_releases as release
    join claims.dmepos_supplier as supplier
        on supplier.source_release_id = release.source_release_id
        and supplier.data_year = release.data_year
),
-- Fail closed on duplicate year/NPI grain before calculating peer statistics.
duplicate_grain as (
    select data_year, supplier_npi, count(*) as copies
    from raw_supplier_rows
    group by data_year, supplier_npi
    having count(*) > 1
), source_integrity_gate as (
    select 1 / case
        when not exists (select 1 from duplicate_grain)
          and not exists (
              select 1
              from validated_releases as release
              where not exists (
                  select 1
                  from raw_supplier_rows as supplier
                  where supplier.source_release_id = release.source_release_id
                    and supplier.data_year = release.data_year
              )
          )
          and not exists (
              select 1
              from raw_supplier_rows
              where supplier_npi !~ '^[0-9]{10}$'
                 or total_beneficiaries < 0
                 or total_claims is null
                 or total_claims < 0
                 or total_services is null
                 or total_services < 0
                 or total_services >= 'Infinity'::numeric
                 or total_submitted_charge is null
                 or total_submitted_charge < 0
                 or total_submitted_charge >= 'Infinity'::numeric
                 or total_medicare_allowed_amount is null
                 or total_medicare_allowed_amount < 0
                 or total_medicare_allowed_amount >= 'Infinity'::numeric
                 or total_medicare_payment_amount is null
                 or total_medicare_payment_amount < 0
                 or total_medicare_payment_amount >= 'Infinity'::numeric
                 or total_medicare_standardized_payment_amount is null
                 or total_medicare_standardized_payment_amount < 0
                 or total_medicare_standardized_payment_amount >= 'Infinity'::numeric
                 or dme_medicare_payment_amount < 0
                 or dme_medicare_payment_amount >= 'Infinity'::numeric
                 or pos_medicare_payment_amount < 0
                 or pos_medicare_payment_amount >= 'Infinity'::numeric
                 or drug_medicare_payment_amount < 0
                 or drug_medicare_payment_amount >= 'Infinity'::numeric
          )
        then 1 else 0
    end as guard
), normalized_rows as materialized (
    select
        supplier.source_release_id,
        supplier.data_year,
        supplier.supplier_npi,
        upper(btrim(supplier.entity_code)) as entity_code,
        btrim(supplier.supplier_specialty_description)
            as supplier_specialty_description,
        btrim(supplier.supplier_specialty_source) as supplier_specialty_source,
        case
            when supplier.total_beneficiaries between 100 and 249 then '100-249'
            when supplier.total_beneficiaries between 250 and 499 then '250-499'
            when supplier.total_beneficiaries between 500 and 999 then '500-999'
            when supplier.total_beneficiaries between 1000 and 4999 then '1000-4999'
            else '5000+'
        end as beneficiary_volume_band,
        case
            when supplier.total_medicare_payment_amount <= 0
              or (
                    coalesce(supplier.dme_medicare_payment_amount, 0) = 0
                and coalesce(supplier.pos_medicare_payment_amount, 0) = 0
                and coalesce(supplier.drug_medicare_payment_amount, 0) = 0
              )
                then 'none'
            when (
                    coalesce(supplier.dme_medicare_payment_amount, 0)
                  + coalesce(supplier.pos_medicare_payment_amount, 0)
                  + coalesce(supplier.drug_medicare_payment_amount, 0)
                ) / supplier.total_medicare_payment_amount < 0.80
                then 'unclassified'
            when coalesce(supplier.dme_medicare_payment_amount, 0)
                / supplier.total_medicare_payment_amount >= 0.80 then 'DME'
            when coalesce(supplier.pos_medicare_payment_amount, 0)
                / supplier.total_medicare_payment_amount >= 0.80 then 'POS'
            when coalesce(supplier.drug_medicare_payment_amount, 0)
                / supplier.total_medicare_payment_amount >= 0.80 then 'Drug'
            else 'mixed'
        end as dominant_broad_category,
        case when supplier.total_medicare_payment_amount > 0
            then supplier.dme_medicare_payment_amount
                / supplier.total_medicare_payment_amount
        end as dme_payment_share,
        case when supplier.total_medicare_payment_amount > 0
            then supplier.pos_medicare_payment_amount
                / supplier.total_medicare_payment_amount
        end as pos_payment_share,
        case when supplier.total_medicare_payment_amount > 0
            then supplier.drug_medicare_payment_amount
                / supplier.total_medicare_payment_amount
        end as drug_payment_share,
        supplier.total_hcpcs_codes,
        supplier.total_beneficiaries,
        supplier.total_claims,
        supplier.total_services,
        supplier.total_submitted_charge,
        supplier.total_medicare_allowed_amount,
        supplier.total_medicare_payment_amount,
        supplier.total_medicare_standardized_payment_amount,
        supplier.dme_medicare_payment_amount,
        supplier.pos_medicare_payment_amount,
        supplier.drug_medicare_payment_amount
    from raw_supplier_rows as supplier
    cross join source_integrity_gate
    cross join settings
    where source_integrity_gate.guard = 1
      and supplier.total_beneficiaries >= settings.minimum_panel_beneficiaries
      and nullif(btrim(supplier.entity_code), '') is not null
      and nullif(btrim(supplier.supplier_specialty_description), '') is not null
      and nullif(btrim(supplier.supplier_specialty_source), '') is not null
), supplier_metrics as materialized (
    select
        normalized.*,
        normalized.total_medicare_standardized_payment_amount
            / nullif(normalized.total_beneficiaries, 0)
            as standardized_payment_per_beneficiary,
        normalized.total_medicare_payment_amount
            / nullif(normalized.total_beneficiaries, 0)
            as raw_payment_per_beneficiary,
        normalized.total_claims::numeric
            / nullif(normalized.total_beneficiaries, 0)
            as claims_per_beneficiary,
        normalized.total_services
            / nullif(normalized.total_beneficiaries, 0)
            as services_per_beneficiary
    from normalized_rows as normalized
), metric_long as materialized (
    select
        supplier.data_year,
        supplier.supplier_npi,
        supplier.entity_code,
        supplier.supplier_specialty_description,
        supplier.dominant_broad_category,
        supplier.beneficiary_volume_band,
        metric.metric_name,
        metric.metric_value
    from supplier_metrics as supplier
    cross join lateral (
        values
            ('standardized_payment_per_beneficiary', supplier.standardized_payment_per_beneficiary),
            ('raw_payment_per_beneficiary', supplier.raw_payment_per_beneficiary),
            ('claims_per_beneficiary', supplier.claims_per_beneficiary),
            ('services_per_beneficiary', supplier.services_per_beneficiary)
    ) as metric(metric_name, metric_value)
), peer_quartiles as materialized (
    select
        data_year,
        entity_code,
        supplier_specialty_description,
        dominant_broad_category,
        beneficiary_volume_band,
        metric_name,
        count(*) as peer_count,
        (percentile_cont(0.25) within group (order by metric_value))::numeric as q1,
        (percentile_cont(0.50) within group (order by metric_value))::numeric as median,
        (percentile_cont(0.75) within group (order by metric_value))::numeric as q75
    from metric_long
    group by
        data_year,
        entity_code,
        supplier_specialty_description,
        dominant_broad_category,
        beneficiary_volume_band,
        metric_name
), metric_deviations as materialized (
    select
        metric.*,
        peer.peer_count,
        peer.q1,
        peer.median,
        peer.q75,
        peer.q75 - peer.q1 as iqr,
        abs(metric.metric_value - peer.median) as absolute_deviation
    from metric_long as metric
    join peer_quartiles as peer using (
        data_year,
        entity_code,
        supplier_specialty_description,
        dominant_broad_category,
        beneficiary_volume_band,
        metric_name
    )
), peer_mads as materialized (
    select
        data_year,
        entity_code,
        supplier_specialty_description,
        dominant_broad_category,
        beneficiary_volume_band,
        metric_name,
        (percentile_cont(0.50) within group (order by absolute_deviation))::numeric as mad
    from metric_deviations
    group by
        data_year,
        entity_code,
        supplier_specialty_description,
        dominant_broad_category,
        beneficiary_volume_band,
        metric_name
), ranked_metrics as materialized (
    select
        deviation.*,
        mad.mad,
        cume_dist() over (
            partition by
                deviation.data_year,
                deviation.entity_code,
                deviation.supplier_specialty_description,
                deviation.dominant_broad_category,
                deviation.beneficiary_volume_band,
                deviation.metric_name
            order by deviation.metric_value
        ) as empirical_percentile
    from metric_deviations as deviation
    join peer_mads as mad using (
        data_year,
        entity_code,
        supplier_specialty_description,
        dominant_broad_category,
        beneficiary_volume_band,
        metric_name
    )
), scored_metrics as materialized (
    select
        ranked.*,
        case
            when ranked.median > 0 then ranked.metric_value / ranked.median
        end as median_ratio,
        abs(ranked.metric_value - ranked.median) as median_absolute_delta,
        case
            when ranked.mad > settings.robust_scale_relative_tolerance
                * greatest(1::numeric, abs(ranked.median))
                then 0.67448975::numeric * (ranked.metric_value - ranked.median)
                    / ranked.mad
            when ranked.iqr > settings.robust_scale_relative_tolerance
                * greatest(1::numeric, abs(ranked.median))
                then 1.3489795::numeric * (ranked.metric_value - ranked.median)
                    / ranked.iqr
        end as robust_z,
        case
            when ranked.mad > settings.robust_scale_relative_tolerance
                * greatest(1::numeric, abs(ranked.median)) then 'mad'
            when ranked.iqr > settings.robust_scale_relative_tolerance
                * greatest(1::numeric, abs(ranked.median)) then 'iqr-fallback'
            else 'degenerate'
        end as scale_source
    from ranked_metrics as ranked
    cross join settings
), wide_statistics as materialized (
    select
        data_year,
        supplier_npi,
        max(peer_count) as peer_count,
        max(metric_value) filter (
            where metric_name = 'standardized_payment_per_beneficiary'
        ) as standardized_payment_per_beneficiary,
        max(median) filter (
            where metric_name = 'standardized_payment_per_beneficiary'
        ) as standardized_payment_peer_median,
        max(q75) filter (
            where metric_name = 'standardized_payment_per_beneficiary'
        ) as standardized_payment_peer_q75,
        max(iqr) filter (
            where metric_name = 'standardized_payment_per_beneficiary'
        ) as standardized_payment_peer_iqr,
        max(mad) filter (
            where metric_name = 'standardized_payment_per_beneficiary'
        ) as standardized_payment_peer_mad,
        max(empirical_percentile) filter (
            where metric_name = 'standardized_payment_per_beneficiary'
        ) as standardized_payment_empirical_percentile,
        max(median_ratio) filter (
            where metric_name = 'standardized_payment_per_beneficiary'
        ) as standardized_payment_median_ratio,
        max(median_absolute_delta) filter (
            where metric_name = 'standardized_payment_per_beneficiary'
        ) as standardized_payment_median_absolute_delta,
        max(robust_z) filter (
            where metric_name = 'standardized_payment_per_beneficiary'
        ) as standardized_payment_robust_z,
        max(scale_source) filter (
            where metric_name = 'standardized_payment_per_beneficiary'
        ) as standardized_payment_scale_source,
        max(metric_value) filter (
            where metric_name = 'raw_payment_per_beneficiary'
        ) as raw_payment_per_beneficiary,
        max(median) filter (
            where metric_name = 'raw_payment_per_beneficiary'
        ) as raw_payment_peer_median,
        max(q75) filter (
            where metric_name = 'raw_payment_per_beneficiary'
        ) as raw_payment_peer_q75,
        max(iqr) filter (
            where metric_name = 'raw_payment_per_beneficiary'
        ) as raw_payment_peer_iqr,
        max(mad) filter (
            where metric_name = 'raw_payment_per_beneficiary'
        ) as raw_payment_peer_mad,
        max(empirical_percentile) filter (
            where metric_name = 'raw_payment_per_beneficiary'
        ) as raw_payment_empirical_percentile,
        max(median_ratio) filter (
            where metric_name = 'raw_payment_per_beneficiary'
        ) as raw_payment_median_ratio,
        max(median_absolute_delta) filter (
            where metric_name = 'raw_payment_per_beneficiary'
        ) as raw_payment_median_absolute_delta,
        max(robust_z) filter (
            where metric_name = 'raw_payment_per_beneficiary'
        ) as raw_payment_robust_z,
        max(scale_source) filter (
            where metric_name = 'raw_payment_per_beneficiary'
        ) as raw_payment_scale_source,
        max(metric_value) filter (
            where metric_name = 'claims_per_beneficiary'
        ) as claims_per_beneficiary,
        max(median) filter (
            where metric_name = 'claims_per_beneficiary'
        ) as claims_peer_median,
        max(q75) filter (
            where metric_name = 'claims_per_beneficiary'
        ) as claims_peer_q75,
        max(iqr) filter (
            where metric_name = 'claims_per_beneficiary'
        ) as claims_peer_iqr,
        max(mad) filter (
            where metric_name = 'claims_per_beneficiary'
        ) as claims_peer_mad,
        max(empirical_percentile) filter (
            where metric_name = 'claims_per_beneficiary'
        ) as claims_empirical_percentile,
        max(median_ratio) filter (
            where metric_name = 'claims_per_beneficiary'
        ) as claims_median_ratio,
        max(median_absolute_delta) filter (
            where metric_name = 'claims_per_beneficiary'
        ) as claims_median_absolute_delta,
        max(robust_z) filter (
            where metric_name = 'claims_per_beneficiary'
        ) as claims_robust_z,
        max(scale_source) filter (
            where metric_name = 'claims_per_beneficiary'
        ) as claims_scale_source,
        max(metric_value) filter (
            where metric_name = 'services_per_beneficiary'
        ) as services_per_beneficiary,
        max(median) filter (
            where metric_name = 'services_per_beneficiary'
        ) as services_peer_median,
        max(q75) filter (
            where metric_name = 'services_per_beneficiary'
        ) as services_peer_q75,
        max(iqr) filter (
            where metric_name = 'services_per_beneficiary'
        ) as services_peer_iqr,
        max(mad) filter (
            where metric_name = 'services_per_beneficiary'
        ) as services_peer_mad,
        max(empirical_percentile) filter (
            where metric_name = 'services_per_beneficiary'
        ) as services_empirical_percentile,
        max(median_ratio) filter (
            where metric_name = 'services_per_beneficiary'
        ) as services_median_ratio,
        max(median_absolute_delta) filter (
            where metric_name = 'services_per_beneficiary'
        ) as services_median_absolute_delta,
        max(robust_z) filter (
            where metric_name = 'services_per_beneficiary'
        ) as services_robust_z,
        max(scale_source) filter (
            where metric_name = 'services_per_beneficiary'
        ) as services_scale_source
    from scored_metrics
    group by data_year, supplier_npi
), benchmarked as materialized (
    select
        supplier.*,
        statistics.peer_count,
        statistics.standardized_payment_peer_median,
        statistics.standardized_payment_peer_q75,
        statistics.standardized_payment_peer_iqr,
        statistics.standardized_payment_peer_mad,
        statistics.standardized_payment_empirical_percentile,
        statistics.standardized_payment_median_ratio,
        statistics.standardized_payment_median_absolute_delta,
        statistics.standardized_payment_robust_z,
        statistics.standardized_payment_scale_source,
        statistics.raw_payment_peer_median,
        statistics.raw_payment_peer_q75,
        statistics.raw_payment_peer_iqr,
        statistics.raw_payment_peer_mad,
        statistics.raw_payment_empirical_percentile,
        statistics.raw_payment_median_ratio,
        statistics.raw_payment_median_absolute_delta,
        statistics.raw_payment_robust_z,
        statistics.raw_payment_scale_source,
        statistics.claims_peer_median,
        statistics.claims_peer_q75,
        statistics.claims_peer_iqr,
        statistics.claims_peer_mad,
        statistics.claims_empirical_percentile,
        statistics.claims_median_ratio,
        statistics.claims_median_absolute_delta,
        statistics.claims_robust_z,
        statistics.claims_scale_source,
        statistics.services_peer_median,
        statistics.services_peer_q75,
        statistics.services_peer_iqr,
        statistics.services_peer_mad,
        statistics.services_empirical_percentile,
        statistics.services_median_ratio,
        statistics.services_median_absolute_delta,
        statistics.services_robust_z,
        statistics.services_scale_source,
        greatest(
            0::numeric,
            supplier.total_medicare_payment_amount
                - supplier.total_beneficiaries * statistics.raw_payment_peer_q75
        ) as raw_payment_benchmark_exposure_above_q75,
        greatest(
            0::numeric,
            supplier.total_medicare_standardized_payment_amount
                - supplier.total_beneficiaries
                    * statistics.standardized_payment_peer_q75
        ) as standardized_payment_benchmark_exposure_above_q75
    from supplier_metrics as supplier
    join wide_statistics as statistics using (data_year, supplier_npi)
), annual_flags as materialized (
    select
        benchmarked.*,
        benchmarked.peer_count >= settings.minimum_peer_count
            as peer_evaluation_scoreable,
        coalesce(
            benchmarked.peer_count >= settings.minimum_peer_count
            and benchmarked.raw_payment_empirical_percentile
                >= settings.full_percentile_threshold
            and benchmarked.raw_payment_robust_z >= settings.minimum_robust_z
            and benchmarked.raw_payment_median_ratio >= settings.minimum_ratio,
            false
        ) as raw_payment_full_outlier_flag,
        coalesce(
            benchmarked.peer_count >= settings.minimum_peer_count
            and benchmarked.standardized_payment_empirical_percentile
                >= settings.full_percentile_threshold
            and benchmarked.standardized_payment_robust_z >= settings.minimum_robust_z
            and benchmarked.standardized_payment_median_ratio >= settings.minimum_ratio,
            false
        ) as standardized_payment_full_outlier_flag,
        coalesce(
            benchmarked.peer_count >= settings.minimum_peer_count
            and benchmarked.claims_empirical_percentile
                >= settings.full_percentile_threshold
            and benchmarked.claims_robust_z >= settings.minimum_robust_z
            and benchmarked.claims_median_ratio >= settings.minimum_ratio,
            false
        ) as claims_full_outlier_flag,
        coalesce(
            benchmarked.peer_count >= settings.minimum_peer_count
            and benchmarked.services_empirical_percentile
                >= settings.full_percentile_threshold
            and benchmarked.services_robust_z >= settings.minimum_robust_z
            and benchmarked.services_median_ratio >= settings.minimum_ratio,
            false
        ) as services_full_outlier_flag,
        coalesce(
            benchmarked.peer_count >= settings.minimum_peer_count
            and benchmarked.standardized_payment_empirical_percentile
                >= settings.tail_percentile_threshold,
            false
        ) as standardized_payment_tail_flag,
        coalesce(
            benchmarked.peer_count >= settings.minimum_peer_count
            and (
                benchmarked.claims_empirical_percentile >= settings.tail_percentile_threshold
                or benchmarked.services_empirical_percentile
                    >= settings.tail_percentile_threshold
            ),
            false
        ) as utilization_tail_flag
    from benchmarked
    cross join settings
), annual_routes as materialized (
    select
        annual.*,
        annual.total_medicare_payment_amount >= settings.minimum_annual_payment
            and annual.raw_payment_benchmark_exposure_above_q75
                >= settings.minimum_benchmark_exposure
            and annual.standardized_payment_benchmark_exposure_above_q75
                >= settings.minimum_benchmark_exposure
            as magnitude_gates_pass,
        annual.total_medicare_payment_amount >= settings.minimum_annual_payment
            and annual.raw_payment_benchmark_exposure_above_q75
                >= settings.minimum_benchmark_exposure
            and annual.standardized_payment_benchmark_exposure_above_q75
                >= settings.minimum_benchmark_exposure
            and annual.standardized_payment_tail_flag
            and annual.utilization_tail_flag
            as candidate_tail_year,
        annual.total_medicare_payment_amount >= settings.minimum_annual_payment
            and annual.raw_payment_benchmark_exposure_above_q75
                >= settings.minimum_benchmark_exposure
            and annual.standardized_payment_benchmark_exposure_above_q75
                >= settings.minimum_benchmark_exposure
            and annual.standardized_payment_full_outlier_flag
            and (
                annual.claims_full_outlier_flag
                or annual.services_full_outlier_flag
            ) as candidate_full_year
    from annual_flags as annual
    cross join settings
), temporal_evidence as materialized (
    select
        annual.*,
        count(*) filter (
            where annual.peer_evaluation_scoreable
        ) over trajectory as comparable_years,
        count(*) filter (
            where annual.candidate_tail_year
        ) over trajectory as candidate_tail_years,
        count(*) filter (
            where annual.candidate_full_year
        ) over trajectory as candidate_full_years,
        max(annual.data_year) over trajectory as maximum_observed_year
    from annual_routes as annual
    window trajectory as (
        partition by
            annual.supplier_npi,
            annual.entity_code,
            annual.supplier_specialty_description,
            annual.dominant_broad_category
    )
), classified as (
    select
        temporal.*,
        temporal.maximum_observed_year = settings.latest_year as latest_year_present,
        temporal.comparable_years = cardinality(settings.data_years)
            and temporal.candidate_tail_years >= 2
            and temporal.candidate_full_years >= 1
            and temporal.maximum_observed_year = settings.latest_year
            as high_priority_temporal_flag
    from temporal_evidence as temporal
    cross join settings
)
select
    'dmepos-supplier-summary-materiality'::text as screen_algorithm,
    '2'::text as screen_algorithm_version,
    classified.data_year,
    classified.supplier_npi,
    classified.entity_code,
    classified.supplier_specialty_description,
    classified.supplier_specialty_source,
    classified.dominant_broad_category,
    classified.beneficiary_volume_band,
    classified.dme_payment_share,
    classified.pos_payment_share,
    classified.drug_payment_share,
    classified.source_release_id,
    classified.total_hcpcs_codes,
    classified.total_beneficiaries,
    classified.total_claims,
    classified.total_services,
    classified.total_submitted_charge,
    classified.total_medicare_allowed_amount,
    classified.total_medicare_payment_amount,
    classified.total_medicare_standardized_payment_amount,
    classified.raw_payment_benchmark_exposure_above_q75,
    classified.standardized_payment_benchmark_exposure_above_q75,
    classified.peer_count,
    case
        when classified.peer_evaluation_scoreable then 'scoreable'
        else 'insufficient-peer-descriptive'
    end as peer_evaluation_status,
    classified.standardized_payment_per_beneficiary,
    classified.standardized_payment_peer_median,
    classified.standardized_payment_peer_q75,
    classified.standardized_payment_peer_iqr,
    classified.standardized_payment_peer_mad,
    100 * classified.standardized_payment_empirical_percentile::numeric
        as standardized_payment_empirical_percentile,
    classified.standardized_payment_median_ratio,
    classified.standardized_payment_median_absolute_delta,
    classified.standardized_payment_robust_z,
    classified.standardized_payment_scale_source,
    classified.raw_payment_per_beneficiary,
    classified.raw_payment_peer_median,
    classified.raw_payment_peer_q75,
    classified.raw_payment_peer_iqr,
    classified.raw_payment_peer_mad,
    100 * classified.raw_payment_empirical_percentile::numeric
        as raw_payment_empirical_percentile,
    classified.raw_payment_median_ratio,
    classified.raw_payment_median_absolute_delta,
    classified.raw_payment_robust_z,
    classified.raw_payment_scale_source,
    classified.claims_per_beneficiary,
    classified.claims_peer_median,
    classified.claims_peer_q75,
    classified.claims_peer_iqr,
    classified.claims_peer_mad,
    100 * classified.claims_empirical_percentile::numeric
        as claims_empirical_percentile,
    classified.claims_median_ratio,
    classified.claims_median_absolute_delta,
    classified.claims_robust_z,
    classified.claims_scale_source,
    classified.services_per_beneficiary,
    classified.services_peer_median,
    classified.services_peer_q75,
    classified.services_peer_iqr,
    classified.services_peer_mad,
    100 * classified.services_empirical_percentile::numeric
        as services_empirical_percentile,
    classified.services_median_ratio,
    classified.services_median_absolute_delta,
    classified.services_robust_z,
    classified.services_scale_source,
    classified.raw_payment_full_outlier_flag,
    classified.standardized_payment_full_outlier_flag,
    classified.claims_full_outlier_flag,
    classified.services_full_outlier_flag,
    classified.standardized_payment_tail_flag,
    classified.utilization_tail_flag,
    classified.magnitude_gates_pass,
    classified.candidate_tail_year,
    classified.candidate_full_year,
    classified.comparable_years,
    classified.candidate_tail_years,
    classified.candidate_full_years,
    classified.latest_year_present,
    classified.high_priority_temporal_flag,
    case
        when classified.high_priority_temporal_flag
            then 'high-dollar-persistent'
        when classified.data_year = settings.latest_year
          and classified.candidate_full_year
            then 'high-dollar-emerging'
        when classified.total_medicare_payment_amount
            >= settings.minimum_annual_payment
            then 'high-spend-context'
        else 'not-qualified'
    end as trajectory_route,
    array_to_string(settings.data_years, ';') as requested_data_years,
    settings.latest_year as latest_year_parameter,
    settings.minimum_panel_beneficiaries,
    settings.minimum_peer_count,
    settings.minimum_annual_payment,
    settings.minimum_benchmark_exposure,
    100 * settings.tail_percentile_threshold
        as tail_percentile_threshold,
    100 * settings.full_percentile_threshold
        as full_percentile_threshold,
    settings.minimum_robust_z,
    settings.minimum_ratio,
    'DME, POS, or Drug requires at least 80 percent of total Medicare payment. Rows with less than 80 percent combined component coverage are unclassified; sufficiently covered rows without one dominant component are mixed; no visible component payment is none. Source-marked suppression can limit component coverage.'
        as category_caveat,
    'Annual total Medicare payment is reconstructed source exposure, not supplier income, improper payment, loss, or damages.'
        as monetary_caveat,
    'Q75 benchmark exposure is a prioritization proxy above a conditional peer benchmark, not a causal counterfactual or recoverable amount.'
        as benchmark_caveat,
    'Payment metrics alone cannot qualify; standardized payment and claims-or-services utilization must independently pass the declared tail/full gates in addition to all three magnitude gates. High-spend-context is descriptive and is not selected for identity review.'
        as route_caveat
from classified
cross join settings
order by
    classified.high_priority_temporal_flag desc,
    (
        classified.data_year = settings.latest_year
        and classified.candidate_full_year
    ) desc,
    classified.data_year desc,
    classified.standardized_payment_benchmark_exposure_above_q75 desc,
    classified.raw_payment_benchmark_exposure_above_q75 desc,
    classified.supplier_npi;
