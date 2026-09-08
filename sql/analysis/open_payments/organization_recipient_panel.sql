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
), coverage_gate as (
    select case
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
    where (select guard from coverage_gate) = 1
), cohort as (
    select
        payment.*,
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
        end as identifier_quality
    from validated_years as year
    join relationships.open_payments_general as payment
        on payment.source_release_id = year.source_release_id
        and payment.program_year = year.program_year
    where payment.paying_entity_id = %(organization_id)s
), recipient_summary as (
    select
        program_year,
        recipient_identifier_key,
        identifier_quality,
        max(covered_recipient_npi) as covered_recipient_npi,
        max(covered_recipient_profile_id) as covered_recipient_profile_id,
        max(teaching_hospital_ccn) as teaching_hospital_ccn,
        max(teaching_hospital_id) as teaching_hospital_id,
        max(teaching_hospital_name) as teaching_hospital_name,
        max(covered_recipient_first_name) as covered_recipient_first_name,
        max(covered_recipient_middle_name) as covered_recipient_middle_name,
        max(covered_recipient_last_name) as covered_recipient_last_name,
        max(covered_recipient_name_suffix) as covered_recipient_name_suffix,
        max(covered_recipient_type) as covered_recipient_type,
        max(recipient_address_line1) as recipient_address_line1,
        max(recipient_address_line2) as recipient_address_line2,
        max(recipient_city) as recipient_city,
        max(recipient_state) as recipient_state,
        max(recipient_zip_code) as recipient_zip_code,
        max(recipient_country) as recipient_country,
        max(recipient_province) as recipient_province,
        max(recipient_postal_code) as recipient_postal_code,
        max(paying_entity_id) as paying_entity_id,
        max(paying_entity_name) as paying_entity_name,
        count(*) as reported_records,
        round(sum(total_amount_usd), 2) as reported_transfer_amount_usd,
        min(payment_date) as first_reported_payment_date,
        max(payment_date) as last_reported_payment_date,
        sum(number_of_payments) filter (where number_of_payments is not null)
            as reported_combined_payment_count,
        count(*) filter (where number_of_payments is null)
            as records_missing_combined_payment_count,
        count(*) filter (
            where lower(nature_of_payment) = 'consulting fee'
        ) as consulting_fee_records,
        coalesce(round(sum(total_amount_usd) filter (
            where lower(nature_of_payment) = 'consulting fee'
        ), 2), 0::numeric) as consulting_fee_reported_transfer_amount_usd,
        array_agg(distinct nature_of_payment order by nature_of_payment) filter (
            where nature_of_payment is not null
        ) as reported_payment_natures,
        array_agg(distinct form_of_payment order by form_of_payment) filter (
            where form_of_payment is not null
        ) as reported_payment_forms,
        count(*) filter (
            where third_party_payment_recipient_indicator is not null
        ) as records_with_third_party_indicator,
        count(*) filter (
            where third_party_payment_recipient_indicator is not null
              and lower(third_party_payment_recipient_indicator)
                  <> 'no third party payment'
        ) as records_reported_with_third_party_recipient,
        coalesce(round(sum(total_amount_usd) filter (
            where third_party_payment_recipient_indicator is not null
              and lower(third_party_payment_recipient_indicator)
                  <> 'no third party payment'
        ), 2), 0::numeric) as third_party_reported_transfer_amount_usd,
        array_agg(
            distinct third_party_payment_recipient_indicator
            order by third_party_payment_recipient_indicator
        ) filter (
            where third_party_payment_recipient_indicator is not null
        ) as third_party_indicator_values,
        count(*) filter (
            where third_party_entity_name is not null
        ) as records_with_named_third_party_entity,
        array_agg(distinct third_party_entity_name order by third_party_entity_name) filter (
            where third_party_entity_name is not null
        ) as named_third_party_entities,
        count(*) filter (
            where lower(coalesce(related_product_indicator, '')) = 'yes'
               or cardinality(product_names) > 0
               or cardinality(product_ndcs) > 0
        ) as product_linked_records,
        array_agg(distinct source_release_id order by source_release_id)
            as source_release_ids
    from cohort
    group by program_year, recipient_identifier_key, identifier_quality
), recipient_attributes as (
    select
        cohort.program_year,
        cohort.recipient_identifier_key,
        attribute.attribute_kind,
        attribute.attribute_value
    from cohort
    cross join lateral (
        select 'recipient_primary_type'::text as attribute_kind, value as attribute_value
        from unnest(cohort.recipient_primary_types) as value
        union all
        select 'recipient_specialty', value
        from unnest(cohort.recipient_specialties) as value
        union all
        select 'recipient_license_state', value
        from unnest(cohort.recipient_license_states) as value
        union all
        select 'product_type', value
        from unnest(cohort.product_types) as value
        union all
        select 'product_category', value
        from unnest(cohort.product_categories) as value
        union all
        select 'product_name', value
        from unnest(cohort.product_names) as value
        union all
        select 'product_ndc', value
        from unnest(cohort.product_ndcs) as value
    ) as attribute
    where nullif(btrim(attribute.attribute_value), '') is not null
), recipient_attribute_arrays as (
    select
        program_year,
        recipient_identifier_key,
        array_agg(distinct attribute_value order by attribute_value) filter (
            where attribute_kind = 'recipient_primary_type'
        ) as recipient_primary_types,
        array_agg(distinct attribute_value order by attribute_value) filter (
            where attribute_kind = 'recipient_specialty'
        ) as recipient_specialties,
        array_agg(distinct attribute_value order by attribute_value) filter (
            where attribute_kind = 'recipient_license_state'
        ) as recipient_license_states,
        array_agg(distinct attribute_value order by attribute_value) filter (
            where attribute_kind = 'product_type'
        ) as product_types,
        array_agg(distinct attribute_value order by attribute_value) filter (
            where attribute_kind = 'product_category'
        ) as product_categories,
        array_agg(distinct attribute_value order by attribute_value) filter (
            where attribute_kind = 'product_name'
        ) as product_names,
        array_agg(distinct attribute_value order by attribute_value) filter (
            where attribute_kind = 'product_ndc'
        ) as product_ndcs
    from recipient_attributes
    group by program_year, recipient_identifier_key
)
select
    summary.program_year,
    summary.recipient_identifier_key,
    summary.identifier_quality,
    summary.covered_recipient_npi,
    summary.covered_recipient_profile_id,
    summary.teaching_hospital_ccn,
    summary.teaching_hospital_id,
    summary.teaching_hospital_name,
    summary.covered_recipient_first_name,
    summary.covered_recipient_middle_name,
    summary.covered_recipient_last_name,
    summary.covered_recipient_name_suffix,
    summary.covered_recipient_type,
    summary.recipient_address_line1,
    summary.recipient_address_line2,
    summary.recipient_city,
    summary.recipient_state,
    summary.recipient_zip_code,
    summary.recipient_country,
    summary.recipient_province,
    summary.recipient_postal_code,
    coalesce(attributes.recipient_primary_types, '{}'::text[]) as recipient_primary_types,
    coalesce(attributes.recipient_specialties, '{}'::text[]) as recipient_specialties,
    coalesce(attributes.recipient_license_states, '{}'::text[]) as recipient_license_states,
    summary.paying_entity_id,
    summary.paying_entity_name,
    summary.reported_records,
    summary.reported_transfer_amount_usd,
    summary.first_reported_payment_date,
    summary.last_reported_payment_date,
    summary.reported_combined_payment_count,
    summary.records_missing_combined_payment_count,
    summary.consulting_fee_records,
    summary.consulting_fee_reported_transfer_amount_usd,
    summary.reported_payment_natures,
    summary.reported_payment_forms,
    summary.records_with_third_party_indicator,
    summary.records_reported_with_third_party_recipient,
    summary.third_party_reported_transfer_amount_usd,
    summary.third_party_indicator_values,
    summary.records_with_named_third_party_entity,
    summary.named_third_party_entities,
    summary.product_linked_records,
    coalesce(attributes.product_types, '{}'::text[]) as product_types,
    coalesce(attributes.product_categories, '{}'::text[]) as product_categories,
    coalesce(attributes.product_names, '{}'::text[]) as product_names,
    coalesce(attributes.product_ndcs, '{}'::text[]) as product_ndcs,
    summary.source_release_ids
from recipient_summary as summary
left join recipient_attribute_arrays as attributes
    using (program_year, recipient_identifier_key)
order by
    summary.program_year,
    summary.reported_transfer_amount_usd desc,
    summary.recipient_identifier_key;
