with input_release_ids as (
    select release_id
    from unnest(%(source_release_ids)s::bigint[]) as release_id
), requested_years as (
    select generate_series(
        %(from_year)s::integer,
        %(to_year)s::integer
    )::smallint as program_year
), audited_releases as (
    select
        input.release_id,
        release.data_year,
        release.source_release_id is not null
            and dataset.slug = 'open-payments-general-payments'
            and release.status = 'loaded'
            and release.data_year between %(from_year)s::smallint and %(to_year)s::smallint
            as eligible
    from input_release_ids as input
    left join metadata.source_release as release
        on release.source_release_id = input.release_id
    left join metadata.source_dataset as dataset
        on dataset.dataset_id = release.dataset_id
), year_coverage as (
    select
        year.program_year,
        count(release.release_id) filter (where release.eligible) as selected_release_count,
        array_agg(release.release_id order by release.release_id) filter (
            where release.eligible
        ) as source_release_ids
    from requested_years as year
    left join audited_releases as release
        on release.data_year = year.program_year
    group by year.program_year
), coverage_summary as (
    select
        %(from_year)s::integer <= %(to_year)s::integer
        and (select count(*) from input_release_ids)
            = %(to_year)s::integer - %(from_year)s::integer + 1
        and not exists (
            select 1 from audited_releases where eligible is not true
        )
        and coalesce(bool_and(selected_release_count = 1), false) as valid,
        coalesce(
            string_agg(
                program_year::text || ':' || selected_release_count::text,
                ',' order by program_year
            ),
            '[no requested years]'
        ) as observed_year_counts
    from year_coverage
), parameter_gate as (
    select case
        when lower(%(identifier_scope)s) not in ('all', 'npi-only') then (
            'Open Payments identifier_scope must be all or npi-only; received '
            || %(identifier_scope)s
        )::integer
        when valid then 1
        else (
            'Open Payments source-release coverage failed; expected exactly one loaded '
            || 'open-payments-general-payments release per requested year; observed '
            || observed_year_counts
        )::integer
    end as guard
    from coverage_summary
), validated_years as materialized (
    select
        coverage.program_year,
        coverage.source_release_ids[1] as source_release_id
    from year_coverage as coverage
    where (select guard from parameter_gate) = 1
), requested_pairs as (
    select
        previous.program_year as previous_year,
        current.program_year as current_year,
        previous.source_release_id as previous_source_release_id,
        current.source_release_id as current_source_release_id
    from validated_years as previous
    join validated_years as current
        on current.program_year = previous.program_year + 1
), cohort as (
    select
        payment.source_release_id,
        payment.program_year,
        coalesce(
            'npi:' || payment.covered_recipient_npi,
            'profile:' || payment.covered_recipient_profile_id,
            'hospital:' || payment.teaching_hospital_id,
            'record:' || payment.record_id
        ) as recipient_identifier_key,
        case
            when payment.covered_recipient_npi is not null then 'npi'
            when payment.covered_recipient_profile_id is not null then 'profile-id'
            when payment.teaching_hospital_id is not null then 'teaching-hospital-id'
            else 'record-only'
        end as identifier_quality,
        payment.covered_recipient_npi,
        payment.covered_recipient_profile_id,
        payment.teaching_hospital_id,
        payment.teaching_hospital_name,
        payment.covered_recipient_first_name,
        payment.covered_recipient_last_name,
        payment.recipient_state,
        payment.total_amount_usd,
        payment.third_party_payment_recipient_indicator,
        payment.third_party_entity_name
    from validated_years as year
    join relationships.open_payments_general as payment
        on payment.source_release_id = year.source_release_id
        and payment.program_year = year.program_year
    where payment.paying_entity_id = %(organization_id)s
      and lower(payment.nature_of_payment) = lower(%(payment_nature)s)
      and (
          lower(%(identifier_scope)s) = 'all'
          or payment.covered_recipient_npi is not null
      )
), annual_roster as (
    select
        program_year,
        recipient_identifier_key,
        identifier_quality,
        max(covered_recipient_npi) as covered_recipient_npi,
        max(covered_recipient_profile_id) as covered_recipient_profile_id,
        max(teaching_hospital_id) as teaching_hospital_id,
        max(teaching_hospital_name) as teaching_hospital_name,
        max(covered_recipient_first_name) as covered_recipient_first_name,
        max(covered_recipient_last_name) as covered_recipient_last_name,
        max(recipient_state) as recipient_state,
        count(*) as reported_records,
        round(sum(total_amount_usd), 2) as reported_transfer_amount_usd,
        bool_or(
            third_party_payment_recipient_indicator is not null
            and lower(third_party_payment_recipient_indicator) <> 'no third party payment'
        ) as reported_third_party_recipient,
        array_agg(distinct third_party_entity_name order by third_party_entity_name) filter (
            where third_party_entity_name is not null
        ) as named_third_party_entities
    from cohort
    group by program_year, recipient_identifier_key, identifier_quality
), pair_observations as (
    select
        pair.previous_year,
        pair.current_year,
        pair.previous_source_release_id,
        pair.current_source_release_id,
        'previous'::text as observation_period,
        roster.*
    from requested_pairs as pair
    join annual_roster as roster on roster.program_year = pair.previous_year
    union all
    select
        pair.previous_year,
        pair.current_year,
        pair.previous_source_release_id,
        pair.current_source_release_id,
        'current'::text as observation_period,
        roster.*
    from requested_pairs as pair
    join annual_roster as roster on roster.program_year = pair.current_year
), paired_recipients as (
    select
        previous_year,
        current_year,
        max(previous_source_release_id) as previous_source_release_id,
        max(current_source_release_id) as current_source_release_id,
        recipient_identifier_key,
        max(identifier_quality) as identifier_quality,
        bool_or(observation_period = 'previous') as present_in_previous_year,
        bool_or(observation_period = 'current') as present_in_current_year,
        max(covered_recipient_npi) filter (
            where observation_period = 'previous'
        ) as previous_covered_recipient_npi,
        max(covered_recipient_npi) filter (
            where observation_period = 'current'
        ) as current_covered_recipient_npi,
        max(covered_recipient_profile_id) filter (
            where observation_period = 'previous'
        ) as previous_covered_recipient_profile_id,
        max(covered_recipient_profile_id) filter (
            where observation_period = 'current'
        ) as current_covered_recipient_profile_id,
        max(teaching_hospital_id) filter (
            where observation_period = 'previous'
        ) as previous_teaching_hospital_id,
        max(teaching_hospital_id) filter (
            where observation_period = 'current'
        ) as current_teaching_hospital_id,
        max(teaching_hospital_name) filter (
            where observation_period = 'previous'
        ) as previous_teaching_hospital_name,
        max(teaching_hospital_name) filter (
            where observation_period = 'current'
        ) as current_teaching_hospital_name,
        max(covered_recipient_first_name) filter (
            where observation_period = 'previous'
        ) as previous_recipient_first_name,
        max(covered_recipient_first_name) filter (
            where observation_period = 'current'
        ) as current_recipient_first_name,
        max(covered_recipient_last_name) filter (
            where observation_period = 'previous'
        ) as previous_recipient_last_name,
        max(covered_recipient_last_name) filter (
            where observation_period = 'current'
        ) as current_recipient_last_name,
        max(recipient_state) filter (
            where observation_period = 'previous'
        ) as previous_recipient_state,
        max(recipient_state) filter (
            where observation_period = 'current'
        ) as current_recipient_state,
        max(reported_records) filter (
            where observation_period = 'previous'
        ) as previous_reported_records,
        max(reported_records) filter (
            where observation_period = 'current'
        ) as current_reported_records,
        max(reported_transfer_amount_usd) filter (
            where observation_period = 'previous'
        ) as previous_reported_transfer_amount_usd,
        max(reported_transfer_amount_usd) filter (
            where observation_period = 'current'
        ) as current_reported_transfer_amount_usd,
        bool_or(reported_third_party_recipient) filter (
            where observation_period = 'previous'
        ) as previous_reported_third_party_recipient,
        bool_or(reported_third_party_recipient) filter (
            where observation_period = 'current'
        ) as current_reported_third_party_recipient,
        max(named_third_party_entities) filter (
            where observation_period = 'previous'
        ) as previous_named_third_party_entities,
        max(named_third_party_entities) filter (
            where observation_period = 'current'
        ) as current_named_third_party_entities
    from pair_observations
    group by previous_year, current_year, recipient_identifier_key
), classified as (
    select
        paired_recipients.*,
        case
            when present_in_previous_year and present_in_current_year then 'retained'
            when present_in_current_year then 'added'
            else 'exited'
        end as transition_status
    from paired_recipients
), measured as (
    select
        classified.*,
        count(*) filter (where present_in_previous_year) over pair_window
            as previous_roster_size,
        count(*) filter (where present_in_current_year) over pair_window
            as current_roster_size,
        count(*) filter (where transition_status = 'retained') over pair_window
            as retained_recipient_count,
        count(*) filter (where transition_status = 'added') over pair_window
            as added_recipient_count,
        count(*) filter (where transition_status = 'exited') over pair_window
            as exited_recipient_count,
        count(*) filter (
            where previous_reported_third_party_recipient
        ) over pair_window as previous_third_party_recipient_count,
        count(*) filter (
            where current_reported_third_party_recipient
        ) over pair_window as current_third_party_recipient_count,
        count(*) filter (
            where present_in_previous_year
              and present_in_current_year
              and previous_reported_third_party_recipient
                  is distinct from current_reported_third_party_recipient
        ) over pair_window as retained_third_party_flag_change_count
    from classified
    window pair_window as (partition by previous_year, current_year)
)
select
    previous_year,
    current_year,
    %(payment_nature)s::text as selected_payment_nature,
    lower(%(identifier_scope)s)::text as identifier_scope,
    previous_roster_size,
    current_roster_size,
    retained_recipient_count,
    added_recipient_count,
    exited_recipient_count,
    round(
        100 * retained_recipient_count::numeric / nullif(previous_roster_size, 0),
        2
    ) as previous_roster_retained_pct,
    round(
        100 * retained_recipient_count::numeric
            / nullif(retained_recipient_count + added_recipient_count + exited_recipient_count, 0),
        2
    ) as roster_jaccard_overlap_pct,
    previous_third_party_recipient_count,
    current_third_party_recipient_count,
    retained_third_party_flag_change_count,
    recipient_identifier_key,
    identifier_quality,
    transition_status,
    previous_covered_recipient_npi,
    current_covered_recipient_npi,
    previous_covered_recipient_profile_id,
    current_covered_recipient_profile_id,
    previous_teaching_hospital_id,
    current_teaching_hospital_id,
    previous_teaching_hospital_name,
    current_teaching_hospital_name,
    previous_recipient_first_name,
    current_recipient_first_name,
    previous_recipient_last_name,
    current_recipient_last_name,
    previous_recipient_state,
    current_recipient_state,
    previous_reported_records,
    current_reported_records,
    previous_reported_transfer_amount_usd,
    current_reported_transfer_amount_usd,
    previous_reported_third_party_recipient,
    current_reported_third_party_recipient,
    previous_named_third_party_entities,
    current_named_third_party_entities,
    array[previous_source_release_id] as previous_source_release_ids,
    array[current_source_release_id] as current_source_release_ids
from measured
order by
    previous_year,
    current_year,
    case transition_status when 'added' then 1 when 'exited' then 2 else 3 end,
    recipient_identifier_key;
