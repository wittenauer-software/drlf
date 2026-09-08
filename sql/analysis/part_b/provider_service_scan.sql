-- First-pass Part B provider-service outlier scan.
--
-- The output grain remains one visible provider/HCPCS/place-of-service cell. In
-- particular, this query never sums service-level beneficiary counts across
-- HCPCS codes or places of service because those populations can overlap.
with settings as (
    select
        100::bigint as minimum_panel_beneficiaries,
        11::bigint as minimum_descriptive_beneficiaries,
        20::bigint as minimum_tested_beneficiaries,
        100::bigint as minimum_peer_count,
        0.99::numeric as tail_percentile_threshold,
        0.975::numeric as temporal_tail_percentile_threshold,
        3.5::numeric as robust_z_threshold,
        -- percentile_cont internally uses floating point; treat sub-picounit
        -- relative residue as zero so tied distributions remain degenerate.
        0.000000000001::numeric as robust_scale_relative_tolerance,
        2.0::numeric as effect_ratio_threshold,
        0.10::numeric as reach_difference_threshold
), input_release_ids as (
    select release_id
    from unnest(%(source_release_ids)s::bigint[]) as release_id
), requested_years as (
    select generate_series(
        %(from_year)s::integer,
        %(to_year)s::integer
    )::smallint as data_year
), audited_releases as (
    select
        input.release_id,
        release.data_year,
        dataset.slug,
        coalesce(release.retrieval_filters, '{}'::jsonb) as retrieval_filters,
        release.source_release_id is not null
            and release.status = 'loaded'
            and release.data_year between %(from_year)s::smallint
                and %(to_year)s::smallint
            and dataset.slug in (
                'medicare-physician-other-practitioners-by-provider',
                'medicare-physician-other-practitioners-by-provider-and-service'
            ) as eligible
    from input_release_ids as input
    left join metadata.source_release as release
        on release.source_release_id = input.release_id
    left join metadata.source_dataset as dataset
        on dataset.dataset_id = release.dataset_id
), year_coverage as (
    select
        year.data_year,
        count(release.release_id) filter (
            where release.eligible
              and release.slug = 'medicare-physician-other-practitioners-by-provider'
        ) as provider_release_count,
        count(release.release_id) filter (
            where release.eligible
              and release.slug
                  = 'medicare-physician-other-practitioners-by-provider-and-service'
        ) as service_release_count,
        max(release.release_id) filter (
            where release.eligible
              and release.slug = 'medicare-physician-other-practitioners-by-provider'
        ) as provider_release_id,
        array_agg(release.release_id order by release.release_id) filter (
            where release.eligible
              and release.slug
                  = 'medicare-physician-other-practitioners-by-provider-and-service'
        ) as service_release_ids
    from requested_years as year
    left join audited_releases as release
        on release.data_year = year.data_year
    group by year.data_year
), coverage_summary as (
    select
        %(from_year)s::integer <= %(to_year)s::integer
        and (select count(*) from input_release_ids) = (
            select coalesce(sum(provider_release_count + service_release_count), 0)
            from year_coverage
        )
        and not exists (
            select 1 from audited_releases where eligible is not true
        )
        and coalesce(
            bool_and(provider_release_count = 1 and service_release_count >= 1),
            false
        ) as valid,
        coalesce(
            string_agg(
                data_year::text
                || ':provider=' || provider_release_count::text
                || ',service=' || service_release_count::text,
                ';' order by data_year
            ),
            '[no requested years]'
        ) as observed_year_counts
    from year_coverage
), coverage_gate as (
    select case
        when valid then 1
        else (
            'Part B source-release coverage failed; expected exactly one loaded provider '
            || 'and at least one loaded provider-service release per requested year; observed '
            || observed_year_counts
        )::integer
    end as guard
    from coverage_summary
), validated_years as materialized (
    select
        coverage.data_year,
        coverage.provider_release_id,
        coverage.service_release_ids
    from year_coverage as coverage
    where (select guard from coverage_gate) = 1
), raw_target_codes as (
    select upper(nullif(btrim(code), '')) as hcpcs_code
    from unnest(%(hcpcs_codes)s::text[]) as code
), target_code_summary as (
    select
        count(*) > 0
        and coalesce(
            bool_and(coalesce(hcpcs_code ~ '^[A-Z0-9]{5}$', false)),
            false
        ) as valid,
        coalesce(string_agg(coalesce(hcpcs_code, '[blank]'), ','), '[none]')
            as observed_codes
    from raw_target_codes
), target_code_gate as (
    select case
        when valid then 1
        else (
            'Part B HCPCS code selection failed; supply at least one five-character '
            || 'alphanumeric code; observed ' || observed_codes
        )::integer
    end as guard
    from target_code_summary
), target_codes as materialized (
    select distinct hcpcs_code
    from raw_target_codes
    where (select guard from target_code_gate) = 1
), requested_service_cells as (
    select year.data_year, code.hcpcs_code
    from requested_years as year
    cross join target_codes as code
), provider_selection_coverage as (
    select
        year.data_year,
        count(release.release_id) filter (
            where release.eligible
              and release.slug = 'medicare-physician-other-practitioners-by-provider'
              -- An unfiltered release is broader than the requested population and is
              -- safe. A specialty-filtered release must match exactly. Any other API
              -- filter (for example, state or NPI) would make national peers incomplete.
              and release.retrieval_filters <@ jsonb_build_object(
                  'Rndrng_Prvdr_Type', %(provider_type)s::text
              )
        ) as covering_releases
    from requested_years as year
    left join audited_releases as release
        on release.data_year = year.data_year
    group by year.data_year
), service_selection_coverage as (
    select
        cell.data_year,
        cell.hcpcs_code,
        count(release.release_id) filter (
            where release.eligible
              and release.slug
                  = 'medicare-physician-other-practitioners-by-provider-and-service'
              -- Permit a national release that is broader than the requested cell,
              -- but reject every narrowing filter except the matching specialty/code.
              and release.retrieval_filters <@ jsonb_build_object(
                  'Rndrng_Prvdr_Type', %(provider_type)s::text,
                  'HCPCS_Cd', cell.hcpcs_code
              )
        ) as covering_releases
    from requested_service_cells as cell
    left join audited_releases as release
        on release.data_year = cell.data_year
    group by cell.data_year, cell.hcpcs_code
), unexpected_selection_releases as (
    select release.release_id
    from audited_releases as release
    where release.eligible
      and (
          (
              release.slug = 'medicare-physician-other-practitioners-by-provider'
              and not (
                  release.retrieval_filters <@ jsonb_build_object(
                      'Rndrng_Prvdr_Type', %(provider_type)s::text
                  )
              )
          )
          or (
              release.slug
                  = 'medicare-physician-other-practitioners-by-provider-and-service'
              and not exists (
                  select 1
                  from requested_service_cells as cell
                  where cell.data_year = release.data_year
                    and release.retrieval_filters <@ jsonb_build_object(
                        'Rndrng_Prvdr_Type', %(provider_type)s::text,
                        'HCPCS_Cd', cell.hcpcs_code
                    )
              )
          )
      )
), selection_coverage_summary as (
    select
        coalesce(
            (select bool_and(covering_releases = 1)
             from provider_selection_coverage),
            false
        )
        and coalesce(
            (select bool_and(covering_releases = 1)
             from service_selection_coverage),
            false
        )
        and not exists (select 1 from unexpected_selection_releases) as valid,
        coalesce(
            (
                select string_agg(
                    data_year::text || ':provider=' || covering_releases::text,
                    ';' order by data_year
                )
                from provider_selection_coverage
                where covering_releases <> 1
            ),
            '[none]'
        ) as provider_failures,
        coalesce(
            (
                select string_agg(
                    data_year::text || '/' || hcpcs_code
                        || '=' || covering_releases::text,
                    ';' order by data_year, hcpcs_code
                )
                from service_selection_coverage
                where covering_releases <> 1
            ),
            '[none]'
        ) as service_failures,
        coalesce(
            (select string_agg(release_id::text, ',' order by release_id)
             from unexpected_selection_releases),
            '[none]'
        ) as unexpected_releases
), selection_coverage_gate as (
    select case
        when valid then 1
        else (
            'Part B retrieval-filter coverage failed; expected exactly one release '
            || 'covering the requested provider type per year and exactly one release '
            || 'covering every requested year/HCPCS cell; provider failures '
            || provider_failures || '; service failures ' || service_failures
            || '; unexpected narrowed releases ' || unexpected_releases
        )::integer
    end as guard
    from selection_coverage_summary
), fully_validated_years as materialized (
    select *
    from validated_years
    where (select guard from selection_coverage_gate) = 1
), provider_rows as (
    select
        year.data_year,
        year.provider_release_id,
        year.service_release_ids,
        provider.rendering_npi,
        provider.provider_last_org_name,
        provider.provider_first_name,
        provider.entity_code,
        provider.provider_city,
        provider.provider_state,
        provider.provider_state_fips,
        provider.provider_zip5,
        provider.provider_type,
        provider.total_hcpcs_codes,
        provider.total_beneficiaries as provider_total_beneficiaries,
        provider.total_services as provider_total_services,
        provider.total_submitted_charge as provider_total_submitted_charge,
        provider.total_medicare_allowed_amount,
        provider.total_medicare_payment_amount,
        provider.total_medicare_standardized_amount,
        provider.asthma_pct,
        provider.copd_pct,
        provider.tobacco_pct,
        provider.average_risk_score,
        case
            when provider.total_beneficiaries between 100 and 199 then '100-199'
            when provider.total_beneficiaries between 200 and 499 then '200-499'
            when provider.total_beneficiaries between 500 and 999 then '500-999'
            else '1000+'
        end as panel_volume_band
    from fully_validated_years as year
    join claims.part_b_provider as provider
        on provider.source_release_id = year.provider_release_id
        and provider.data_year = year.data_year
    cross join settings
    where lower(provider.provider_type) = lower(%(provider_type)s)
      and upper(provider.entity_code) = upper(%(entity_code)s)
      and provider.total_beneficiaries >= settings.minimum_panel_beneficiaries
), raw_candidate_rows as (
    select
        provider.*,
        service.source_release_id as service_release_id,
        service.hcpcs_code,
        service.hcpcs_description,
        service.hcpcs_drug_indicator,
        service.place_of_service,
        service.total_beneficiaries as tested_beneficiaries,
        service.total_services,
        service.total_beneficiary_day_services,
        service.average_submitted_charge,
        service.average_medicare_allowed_amount,
        service.average_medicare_payment_amount,
        service.average_medicare_standardized_amount,
        service.total_beneficiaries::numeric
            / nullif(provider.provider_total_beneficiaries, 0)
            as panel_penetration,
        service.total_beneficiary_day_services::numeric
            / nullif(service.total_beneficiaries, 0)
            as days_per_tested_beneficiary,
        service.total_services::numeric
            / nullif(service.total_beneficiary_day_services, 0)
            as units_per_beneficiary_day,
        service.total_services * service.average_medicare_allowed_amount
            as reconstructed_medicare_allowed_amount,
        service.total_services * service.average_medicare_payment_amount
            as reconstructed_medicare_payment_amount,
        service.total_services * service.average_medicare_standardized_amount
            as reconstructed_medicare_standardized_amount,
        service.total_services * service.average_medicare_standardized_amount
            / nullif(provider.provider_total_beneficiaries, 0)
            as standardized_payment_per_panel_beneficiary,
        service.total_services * service.average_medicare_standardized_amount
            / nullif(provider.total_medicare_standardized_amount, 0)
            as provider_standardized_payment_share
    from provider_rows as provider
    join claims.part_b_provider_service as service
        on service.source_release_id = any(provider.service_release_ids)
        and service.data_year = provider.data_year
        and service.rendering_npi = provider.rendering_npi
    join target_codes as target
        on target.hcpcs_code = upper(service.hcpcs_code)
    cross join settings
    where service.total_beneficiaries >= settings.minimum_descriptive_beneficiaries
      and service.total_services > 0
      and service.total_beneficiary_day_services > 0
), duplicate_service_cells as (
    select
        data_year,
        rendering_npi,
        hcpcs_code,
        place_of_service,
        count(*) as copies
    from raw_candidate_rows
    group by data_year, rendering_npi, hcpcs_code, place_of_service
    having count(*) > 1
), duplicate_service_cell_gate as (
    select case
        when not exists (select 1 from duplicate_service_cells) then 1
        else (
            'Part B service releases overlap at year/NPI/HCPCS/POS grain; '
            || 'use non-overlapping exact-filter manifests; first duplicate='
            || coalesce(
                (
                    select min(
                        data_year::text || '/' || rendering_npi || '/'
                        || hcpcs_code || '/' || place_of_service
                    )
                    from duplicate_service_cells
                ),
                '[unavailable]'
            )
        )::integer
    end as guard
), candidate_rows as materialized (
    select *
    from raw_candidate_rows
    where (select guard from duplicate_service_cell_gate) = 1
), eligible_rows as materialized (
    select candidate.*
    from candidate_rows as candidate
    cross join settings
    where candidate.tested_beneficiaries >= settings.minimum_tested_beneficiaries
), peer_quartiles as (
    select
        data_year,
        provider_type,
        entity_code,
        hcpcs_code,
        place_of_service,
        panel_volume_band,
        count(*) as peer_count,
        (percentile_cont(0.25) within group (order by panel_penetration))::numeric
            as reach_q1,
        (percentile_cont(0.50) within group (order by panel_penetration))::numeric
            as reach_median,
        (percentile_cont(0.75) within group (order by panel_penetration))::numeric
            as reach_q3,
        (
            percentile_cont(0.25)
                within group (order by days_per_tested_beneficiary)
        )::numeric as repeat_days_q1,
        (
            percentile_cont(0.50)
                within group (order by days_per_tested_beneficiary)
        )::numeric as repeat_days_median,
        (
            percentile_cont(0.75)
                within group (order by days_per_tested_beneficiary)
        )::numeric as repeat_days_q3,
        (
            percentile_cont(0.25)
                within group (order by units_per_beneficiary_day)
        )::numeric as units_per_day_q1,
        (
            percentile_cont(0.50)
                within group (order by units_per_beneficiary_day)
        )::numeric as units_per_day_median,
        (
            percentile_cont(0.75)
                within group (order by units_per_beneficiary_day)
        )::numeric as units_per_day_q3
    from eligible_rows
    group by
        data_year,
        provider_type,
        entity_code,
        hcpcs_code,
        place_of_service,
        panel_volume_band
), peer_deviations as (
    select
        row.*,
        peer.peer_count,
        peer.reach_q1,
        peer.reach_median,
        peer.reach_q3,
        peer.reach_q3 - peer.reach_q1 as reach_iqr,
        peer.repeat_days_q1,
        peer.repeat_days_median,
        peer.repeat_days_q3,
        peer.repeat_days_q3 - peer.repeat_days_q1 as repeat_days_iqr,
        peer.units_per_day_q1,
        peer.units_per_day_median,
        peer.units_per_day_q3,
        peer.units_per_day_q3 - peer.units_per_day_q1 as units_per_day_iqr,
        abs(row.panel_penetration - peer.reach_median) as reach_absolute_deviation,
        abs(row.days_per_tested_beneficiary - peer.repeat_days_median)
            as repeat_days_absolute_deviation,
        abs(row.units_per_beneficiary_day - peer.units_per_day_median)
            as units_per_day_absolute_deviation
    from eligible_rows as row
    join peer_quartiles as peer using (
        data_year,
        provider_type,
        entity_code,
        hcpcs_code,
        place_of_service,
        panel_volume_band
    )
), peer_mads as (
    select
        data_year,
        provider_type,
        entity_code,
        hcpcs_code,
        place_of_service,
        panel_volume_band,
        (
            percentile_cont(0.50)
                within group (order by reach_absolute_deviation)
        )::numeric as reach_mad,
        (
            percentile_cont(0.50)
                within group (order by repeat_days_absolute_deviation)
        )::numeric as repeat_days_mad,
        (
            percentile_cont(0.50)
                within group (order by units_per_day_absolute_deviation)
        )::numeric as units_per_day_mad
    from peer_deviations
    group by
        data_year,
        provider_type,
        entity_code,
        hcpcs_code,
        place_of_service,
        panel_volume_band
), ranked as (
    select
        deviation.*,
        mad.reach_mad,
        mad.repeat_days_mad,
        mad.units_per_day_mad,
        cume_dist() over (
            partition by
                deviation.data_year,
                deviation.provider_type,
                deviation.entity_code,
                deviation.hcpcs_code,
                deviation.place_of_service,
                deviation.panel_volume_band
            order by deviation.panel_penetration
        ) as reach_empirical_percentile,
        cume_dist() over (
            partition by
                deviation.data_year,
                deviation.provider_type,
                deviation.entity_code,
                deviation.hcpcs_code,
                deviation.place_of_service,
                deviation.panel_volume_band
            order by deviation.days_per_tested_beneficiary
        ) as repeat_days_empirical_percentile,
        cume_dist() over (
            partition by
                deviation.data_year,
                deviation.provider_type,
                deviation.entity_code,
                deviation.hcpcs_code,
                deviation.place_of_service,
                deviation.panel_volume_band
            order by deviation.units_per_beneficiary_day
        ) as units_per_day_empirical_percentile,
        count(*) over (
            partition by
                deviation.data_year,
                deviation.provider_type,
                deviation.entity_code,
                deviation.hcpcs_code,
                deviation.place_of_service
        ) as broad_peer_count,
        cume_dist() over (
            partition by
                deviation.data_year,
                deviation.provider_type,
                deviation.entity_code,
                deviation.hcpcs_code,
                deviation.place_of_service
            order by deviation.panel_penetration
        ) as broad_reach_empirical_percentile,
        cume_dist() over (
            partition by
                deviation.data_year,
                deviation.provider_type,
                deviation.entity_code,
                deviation.hcpcs_code,
                deviation.place_of_service
            order by deviation.days_per_tested_beneficiary
        ) as broad_repeat_days_empirical_percentile,
        cume_dist() over (
            partition by
                deviation.data_year,
                deviation.provider_type,
                deviation.entity_code,
                deviation.hcpcs_code,
                deviation.place_of_service
            order by deviation.units_per_beneficiary_day
        ) as broad_units_per_day_empirical_percentile
    from peer_deviations as deviation
    join peer_mads as mad using (
        data_year,
        provider_type,
        entity_code,
        hcpcs_code,
        place_of_service,
        panel_volume_band
    )
), statistics as materialized (
    select
        ranked.*,
        ranked.panel_penetration / nullif(ranked.reach_median, 0)
            as reach_to_median_ratio,
        ranked.panel_penetration - ranked.reach_median
            as reach_median_difference,
        ranked.days_per_tested_beneficiary
            / nullif(ranked.repeat_days_median, 0)
            as repeat_days_to_median_ratio,
        ranked.units_per_beneficiary_day
            / nullif(ranked.units_per_day_median, 0)
            as units_per_day_to_median_ratio,
        case
            when ranked.reach_mad > settings.robust_scale_relative_tolerance
                * greatest(1::numeric, abs(ranked.reach_median)) then
                0.67448975 * (ranked.panel_penetration - ranked.reach_median)
                    / ranked.reach_mad
            when ranked.reach_iqr > settings.robust_scale_relative_tolerance
                * greatest(1::numeric, abs(ranked.reach_median)) then
                1.3489795 * (ranked.panel_penetration - ranked.reach_median)
                    / ranked.reach_iqr
        end as reach_robust_z,
        case
            when ranked.repeat_days_mad > settings.robust_scale_relative_tolerance
                * greatest(1::numeric, abs(ranked.repeat_days_median)) then
                0.67448975
                    * (ranked.days_per_tested_beneficiary - ranked.repeat_days_median)
                    / ranked.repeat_days_mad
            when ranked.repeat_days_iqr > settings.robust_scale_relative_tolerance
                * greatest(1::numeric, abs(ranked.repeat_days_median)) then
                1.3489795
                    * (ranked.days_per_tested_beneficiary - ranked.repeat_days_median)
                    / ranked.repeat_days_iqr
        end as repeat_days_robust_z,
        case
            when ranked.units_per_day_mad > settings.robust_scale_relative_tolerance
                * greatest(1::numeric, abs(ranked.units_per_day_median)) then
                0.67448975
                    * (ranked.units_per_beneficiary_day - ranked.units_per_day_median)
                    / ranked.units_per_day_mad
            when ranked.units_per_day_iqr > settings.robust_scale_relative_tolerance
                * greatest(1::numeric, abs(ranked.units_per_day_median)) then
                1.3489795
                    * (ranked.units_per_beneficiary_day - ranked.units_per_day_median)
                    / ranked.units_per_day_iqr
        end as units_per_day_robust_z,
        case
            when ranked.reach_mad > settings.robust_scale_relative_tolerance
                * greatest(1::numeric, abs(ranked.reach_median)) then 'mad'
            when ranked.reach_iqr > settings.robust_scale_relative_tolerance
                * greatest(1::numeric, abs(ranked.reach_median)) then 'iqr-fallback'
            else 'degenerate'
        end as reach_scale_source,
        case
            when ranked.repeat_days_mad > settings.robust_scale_relative_tolerance
                * greatest(1::numeric, abs(ranked.repeat_days_median)) then 'mad'
            when ranked.repeat_days_iqr > settings.robust_scale_relative_tolerance
                * greatest(1::numeric, abs(ranked.repeat_days_median)) then 'iqr-fallback'
            else 'degenerate'
        end as repeat_days_scale_source,
        case
            when ranked.units_per_day_mad > settings.robust_scale_relative_tolerance
                * greatest(1::numeric, abs(ranked.units_per_day_median)) then 'mad'
            when ranked.units_per_day_iqr > settings.robust_scale_relative_tolerance
                * greatest(1::numeric, abs(ranked.units_per_day_median)) then 'iqr-fallback'
            else 'degenerate'
        end as units_per_day_scale_source
    from ranked
    cross join settings
), flagged as (
    select
        candidate.*,
        statistics.peer_count,
        statistics.reach_q1,
        statistics.reach_median,
        statistics.reach_q3,
        statistics.reach_iqr,
        statistics.repeat_days_q1,
        statistics.repeat_days_median,
        statistics.repeat_days_q3,
        statistics.repeat_days_iqr,
        statistics.units_per_day_q1,
        statistics.units_per_day_median,
        statistics.units_per_day_q3,
        statistics.units_per_day_iqr,
        statistics.reach_mad,
        statistics.repeat_days_mad,
        statistics.units_per_day_mad,
        statistics.reach_empirical_percentile,
        statistics.repeat_days_empirical_percentile,
        statistics.units_per_day_empirical_percentile,
        statistics.broad_peer_count,
        statistics.broad_reach_empirical_percentile,
        statistics.broad_repeat_days_empirical_percentile,
        statistics.broad_units_per_day_empirical_percentile,
        statistics.reach_to_median_ratio,
        statistics.reach_median_difference,
        statistics.repeat_days_to_median_ratio,
        statistics.units_per_day_to_median_ratio,
        statistics.reach_robust_z,
        statistics.repeat_days_robust_z,
        statistics.units_per_day_robust_z,
        statistics.reach_scale_source,
        statistics.repeat_days_scale_source,
        statistics.units_per_day_scale_source,
        case
            when candidate.tested_beneficiaries
                < settings.minimum_tested_beneficiaries
                then 'insufficient-volume-descriptive'
            when statistics.peer_count >= settings.minimum_peer_count
                then 'scoreable'
            else 'insufficient-peer-descriptive'
        end as peer_evaluation_status,
        coalesce(
            candidate.tested_beneficiaries
                >= settings.minimum_tested_beneficiaries
            and
            statistics.peer_count >= settings.minimum_peer_count
            and statistics.reach_empirical_percentile
                >= settings.tail_percentile_threshold
            and statistics.reach_robust_z >= settings.robust_z_threshold
            and statistics.reach_to_median_ratio >= settings.effect_ratio_threshold
            and statistics.reach_median_difference
                >= settings.reach_difference_threshold,
            false
        ) as reach_outlier_flag,
        coalesce(
            candidate.tested_beneficiaries
                >= settings.minimum_tested_beneficiaries
            and
            statistics.peer_count >= settings.minimum_peer_count
            and statistics.repeat_days_empirical_percentile
                >= settings.tail_percentile_threshold
            and statistics.repeat_days_robust_z >= settings.robust_z_threshold
            and statistics.repeat_days_to_median_ratio
                >= settings.effect_ratio_threshold,
            false
        ) as repeat_day_intensity_flag,
        coalesce(
            candidate.tested_beneficiaries
                >= settings.minimum_tested_beneficiaries
            and
            statistics.peer_count >= settings.minimum_peer_count
            and statistics.units_per_day_empirical_percentile
                >= settings.tail_percentile_threshold
            and statistics.units_per_day_robust_z >= settings.robust_z_threshold
            and statistics.units_per_day_to_median_ratio
                >= settings.effect_ratio_threshold,
            false
        ) as unit_intensity_flag
    from candidate_rows as candidate
    full outer join statistics
        on statistics.service_release_id = candidate.service_release_id
        and statistics.data_year = candidate.data_year
        and statistics.rendering_npi = candidate.rendering_npi
        and statistics.hcpcs_code = candidate.hcpcs_code
        and statistics.place_of_service = candidate.place_of_service
    cross join settings
), temporal_evidence as (
    select
        flagged.*,
        count(*) filter (
            where flagged.peer_evaluation_status = 'scoreable'
        ) over identity_cell as comparable_years,
        count(*) filter (
            where flagged.peer_evaluation_status = 'scoreable'
              and flagged.reach_empirical_percentile
                  >= settings.temporal_tail_percentile_threshold
        ) over identity_cell as reach_temporal_tail_years,
        count(*) filter (
            where flagged.peer_evaluation_status = 'scoreable'
              and flagged.repeat_days_empirical_percentile
                  >= settings.temporal_tail_percentile_threshold
        ) over identity_cell as repeat_days_temporal_tail_years,
        count(*) filter (
            where flagged.peer_evaluation_status = 'scoreable'
              and flagged.units_per_day_empirical_percentile
                  >= settings.temporal_tail_percentile_threshold
        ) over identity_cell as unit_temporal_tail_years,
        count(*) filter (
            where flagged.reach_outlier_flag
        ) over identity_cell as reach_full_flag_years,
        count(*) filter (
            where flagged.repeat_day_intensity_flag
        ) over identity_cell as repeat_days_full_flag_years,
        count(*) filter (
            where flagged.unit_intensity_flag
        ) over identity_cell as unit_full_flag_years,
        max(flagged.data_year) over identity_cell as maximum_observed_year
    from flagged
    cross join settings
    window identity_cell as (
        partition by
            flagged.rendering_npi,
            flagged.provider_type,
            flagged.entity_code,
            flagged.hcpcs_code,
            flagged.place_of_service
    )
), temporally_classified as (
    select
        temporal_evidence.*,
        maximum_observed_year = %(to_year)s::smallint as latest_year_present,
        maximum_observed_year = %(to_year)s::smallint
            and comparable_years >= 3
            and reach_temporal_tail_years >= 2
            and reach_full_flag_years >= 1
            as reach_temporally_confirmed,
        maximum_observed_year = %(to_year)s::smallint
            and comparable_years >= 3
            and repeat_days_temporal_tail_years >= 2
            and repeat_days_full_flag_years >= 1
            as repeat_days_temporally_confirmed,
        maximum_observed_year = %(to_year)s::smallint
            and comparable_years >= 3
            and unit_temporal_tail_years >= 2
            and unit_full_flag_years >= 1
            as unit_temporally_confirmed
    from temporal_evidence
)
select
    data_year,
    rendering_npi,
    hcpcs_code,
    place_of_service,
    peer_evaluation_status,
    reach_outlier_flag,
    repeat_day_intensity_flag,
    unit_intensity_flag,
    case
        when peer_evaluation_status = 'insufficient-volume-descriptive'
            then 'insufficient-volume'
        when peer_evaluation_status <> 'scoreable'
            then 'insufficient-peer'
        when reach_outlier_flag
            and (repeat_day_intensity_flag or unit_intensity_flag)
            then 'reach-and-intensity'
        when reach_outlier_flag then 'reach'
        when repeat_day_intensity_flag or unit_intensity_flag then 'intensity'
        else 'none'
    end as triage_route,
    comparable_years,
    reach_temporal_tail_years,
    repeat_days_temporal_tail_years,
    unit_temporal_tail_years,
    reach_full_flag_years,
    repeat_days_full_flag_years,
    unit_full_flag_years,
    latest_year_present,
    reach_temporally_confirmed,
    repeat_days_temporally_confirmed,
    unit_temporally_confirmed,
    case
        when reach_temporally_confirmed
            and (repeat_days_temporally_confirmed or unit_temporally_confirmed)
            then 'reach-and-intensity'
        when reach_temporally_confirmed then 'reach'
        when repeat_days_temporally_confirmed or unit_temporally_confirmed
            then 'intensity'
        else 'not-confirmed'
    end as temporal_triage_route,
    provider_last_org_name,
    provider_first_name,
    entity_code,
    provider_type,
    provider_city,
    provider_state,
    provider_state_fips,
    provider_zip5,
    hcpcs_description,
    hcpcs_drug_indicator,
    panel_volume_band,
    provider_total_beneficiaries,
    tested_beneficiaries,
    total_services,
    total_beneficiary_day_services,
    round(panel_penetration, 6) as panel_penetration,
    round(days_per_tested_beneficiary, 6) as days_per_tested_beneficiary,
    round(units_per_beneficiary_day, 6) as units_per_beneficiary_day,
    round(reconstructed_medicare_allowed_amount, 2)
        as reconstructed_medicare_allowed_amount,
    round(reconstructed_medicare_payment_amount, 2)
        as reconstructed_medicare_payment_amount,
    round(reconstructed_medicare_standardized_amount, 2)
        as reconstructed_medicare_standardized_amount,
    round(standardized_payment_per_panel_beneficiary, 2)
        as standardized_payment_per_panel_beneficiary,
    round(provider_standardized_payment_share, 6)
        as provider_standardized_payment_share,
    average_submitted_charge,
    average_medicare_allowed_amount,
    average_medicare_payment_amount,
    average_medicare_standardized_amount,
    peer_count,
    broad_peer_count,
    round(100 * broad_reach_empirical_percentile::numeric, 3)
        as broad_reach_empirical_percentile,
    round(100 * broad_repeat_days_empirical_percentile::numeric, 3)
        as broad_repeat_days_empirical_percentile,
    round(100 * broad_units_per_day_empirical_percentile::numeric, 3)
        as broad_units_per_day_empirical_percentile,
    case
        when peer_evaluation_status = 'insufficient-volume-descriptive'
            then 'insufficient-volume'
        when peer_evaluation_status = 'insufficient-peer-descriptive'
          and broad_peer_count >= 100
          and (
              broad_reach_empirical_percentile >= 0.99
              or broad_repeat_days_empirical_percentile >= 0.99
          ) then 'broader-peer-tail-descriptive'
        when peer_evaluation_status = 'insufficient-peer-descriptive'
            then 'insufficient-peer'
        else 'not-applicable'
    end as broader_peer_sensitivity_status,
    round(reach_median, 6) as reach_peer_median,
    round(reach_iqr, 6) as reach_peer_iqr,
    round(reach_mad, 6) as reach_peer_mad,
    round(100 * reach_empirical_percentile::numeric, 3)
        as reach_empirical_percentile,
    round(reach_to_median_ratio, 3) as reach_to_median_ratio,
    round(reach_median_difference, 6) as reach_median_difference,
    round(reach_robust_z, 3) as reach_robust_z,
    reach_scale_source,
    round(repeat_days_median, 6) as repeat_days_peer_median,
    round(repeat_days_iqr, 6) as repeat_days_peer_iqr,
    round(repeat_days_mad, 6) as repeat_days_peer_mad,
    round(100 * repeat_days_empirical_percentile::numeric, 3)
        as repeat_days_empirical_percentile,
    round(repeat_days_to_median_ratio, 3) as repeat_days_to_median_ratio,
    round(repeat_days_robust_z, 3) as repeat_days_robust_z,
    repeat_days_scale_source,
    round(units_per_day_median, 6) as units_per_day_peer_median,
    round(units_per_day_iqr, 6) as units_per_day_peer_iqr,
    round(units_per_day_mad, 6) as units_per_day_peer_mad,
    round(100 * units_per_day_empirical_percentile::numeric, 3)
        as units_per_day_empirical_percentile,
    round(units_per_day_to_median_ratio, 3) as units_per_day_to_median_ratio,
    round(units_per_day_robust_z, 3) as units_per_day_robust_z,
    units_per_day_scale_source,
    total_hcpcs_codes,
    provider_total_services,
    provider_total_submitted_charge,
    total_medicare_allowed_amount as provider_total_medicare_allowed_amount,
    total_medicare_payment_amount as provider_total_medicare_payment_amount,
    total_medicare_standardized_amount as provider_total_medicare_standardized_amount,
    asthma_pct,
    copd_pct,
    tobacco_pct,
    average_risk_score,
    provider_release_id,
    service_release_id,
    'Visible provider/HCPCS/POS cell; CMS omits cells derived from 10 or fewer beneficiaries.'
        as suppression_caveat,
    'Do not sum tested beneficiaries across HCPCS codes or places of service.'
        as beneficiary_overlap_caveat,
    'Peer statistics are conditional on visible cells with at least 20 beneficiaries.'
        as peer_population_caveat,
    'Visible cells with 11 through 19 beneficiaries are retained as unscored descriptive rows.'
        as volume_caveat,
    'Provider chronic-condition percentages are context, not service indications; CMS top-codes 75 through 100 percent at 75.'
        as case_mix_caveat,
    'Reconstructed amounts use rounded published averages; they are not personal income, loss, or damages.'
        as monetary_caveat
from temporally_classified
order by
    (
        reach_temporally_confirmed
        or repeat_days_temporally_confirmed
        or unit_temporally_confirmed
    ) desc,
    (
        peer_evaluation_status = 'scoreable'
        and (reach_outlier_flag or repeat_day_intensity_flag or unit_intensity_flag)
    ) desc,
    (peer_evaluation_status = 'scoreable') desc,
    reach_outlier_flag desc,
    repeat_day_intensity_flag desc,
    unit_intensity_flag desc,
    data_year desc,
    reach_empirical_percentile desc,
    reconstructed_medicare_standardized_amount desc nulls last,
    rendering_npi,
    hcpcs_code,
    place_of_service;
