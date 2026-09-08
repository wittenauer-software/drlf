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
), annual as (
    select
        year.program_year,
        %(organization_id)s::text as paying_entity_id,
        max(cohort.paying_entity_name) as paying_entity_name,
        count(cohort.record_id) as reported_records,
        coalesce(round(sum(cohort.total_amount_usd), 2), 0::numeric)
            as reported_transfer_amount_usd,
        case
            when count(cohort.record_id) = 0 then 0
            else sum(cohort.number_of_payments) filter (
                where cohort.number_of_payments is not null
            )
        end as reported_combined_payment_count,
        count(cohort.record_id) filter (
            where cohort.number_of_payments is null
        ) as records_missing_combined_payment_count,
        count(distinct cohort.covered_recipient_npi) filter (
            where cohort.covered_recipient_npi is not null
        ) as distinct_recipient_npis,
        count(distinct cohort.covered_recipient_profile_id) filter (
            where cohort.covered_recipient_profile_id is not null
        ) as distinct_recipient_profile_ids,
        count(distinct cohort.teaching_hospital_id) filter (
            where cohort.teaching_hospital_id is not null
        ) as distinct_teaching_hospital_ids,
        count(distinct cohort.recipient_identifier_key)
            as distinct_recipient_identifier_keys,
        count(distinct cohort.recipient_identifier_key) filter (
            where cohort.identifier_quality = 'npi'
        ) as npi_identity_recipient_count,
        coalesce(round(sum(cohort.total_amount_usd) filter (
            where cohort.identifier_quality = 'npi'
        ), 2), 0::numeric) as npi_identity_reported_transfer_amount_usd,
        count(distinct cohort.recipient_identifier_key) filter (
            where cohort.identifier_quality = 'profile-id'
        ) as profile_only_recipient_count,
        coalesce(round(sum(cohort.total_amount_usd) filter (
            where cohort.identifier_quality = 'profile-id'
        ), 2), 0::numeric) as profile_only_reported_transfer_amount_usd,
        count(distinct cohort.recipient_identifier_key) filter (
            where cohort.identifier_quality = 'teaching-hospital-id'
        ) as teaching_hospital_identity_recipient_count,
        coalesce(round(sum(cohort.total_amount_usd) filter (
            where cohort.identifier_quality = 'teaching-hospital-id'
        ), 2), 0::numeric) as teaching_hospital_reported_transfer_amount_usd,
        count(distinct cohort.recipient_identifier_key) filter (
            where cohort.identifier_quality = 'record-only'
        ) as record_only_recipient_count,
        count(cohort.record_id) filter (
            where cohort.identifier_quality = 'record-only'
        ) as records_missing_stable_recipient_identifier,
        coalesce(round(sum(cohort.total_amount_usd) filter (
            where cohort.identifier_quality = 'record-only'
        ), 2), 0::numeric) as record_only_reported_transfer_amount_usd,
        array_agg(
            distinct cohort.covered_recipient_type
            order by cohort.covered_recipient_type
        ) filter (
            where cohort.covered_recipient_type is not null
        ) as covered_recipient_types,
        array_agg(
            distinct cohort.nature_of_payment
            order by cohort.nature_of_payment
        ) filter (
            where cohort.nature_of_payment is not null
        ) as reported_payment_natures,
        array_agg(
            distinct cohort.form_of_payment
            order by cohort.form_of_payment
        ) filter (
            where cohort.form_of_payment is not null
        ) as reported_payment_forms,
        count(cohort.record_id) filter (
            where lower(cohort.nature_of_payment) = 'consulting fee'
        ) as consulting_fee_records,
        count(distinct cohort.covered_recipient_npi) filter (
            where lower(cohort.nature_of_payment) = 'consulting fee'
              and cohort.covered_recipient_npi is not null
        ) as consulting_fee_distinct_recipient_npis,
        coalesce(round(sum(cohort.total_amount_usd) filter (
            where lower(cohort.nature_of_payment) = 'consulting fee'
        ), 2), 0::numeric) as consulting_fee_reported_transfer_amount_usd,
        count(cohort.record_id) filter (
            where cohort.third_party_payment_recipient_indicator is not null
        ) as records_with_third_party_indicator,
        count(cohort.record_id) filter (
            where cohort.third_party_payment_recipient_indicator is not null
              and lower(cohort.third_party_payment_recipient_indicator)
                  <> 'no third party payment'
        ) as records_reported_with_third_party_recipient,
        coalesce(round(sum(cohort.total_amount_usd) filter (
            where cohort.third_party_payment_recipient_indicator is not null
              and lower(cohort.third_party_payment_recipient_indicator)
                  <> 'no third party payment'
        ), 2), 0::numeric) as third_party_reported_transfer_amount_usd,
        array_agg(
            distinct cohort.third_party_payment_recipient_indicator
            order by cohort.third_party_payment_recipient_indicator
        ) filter (
            where cohort.third_party_payment_recipient_indicator is not null
        ) as third_party_indicator_values,
        count(cohort.record_id) filter (
            where cohort.third_party_entity_name is not null
        ) as records_with_named_third_party_entity,
        count(distinct cohort.third_party_entity_name) filter (
            where cohort.third_party_entity_name is not null
        ) as distinct_named_third_party_entities,
        array_agg(
            distinct cohort.third_party_entity_name
            order by cohort.third_party_entity_name
        ) filter (
            where cohort.third_party_entity_name is not null
        ) as named_third_party_entities,
        count(cohort.record_id) filter (
            where lower(coalesce(cohort.related_product_indicator, '')) = 'yes'
               or cardinality(cohort.product_names) > 0
               or cardinality(cohort.product_ndcs) > 0
        ) as product_linked_records,
        array[year.source_release_id] as source_release_ids
    from validated_years as year
    left join cohort on cohort.program_year = year.program_year
    group by year.program_year, year.source_release_id
), payment_nature_counts as (
    select
        program_year,
        jsonb_object_agg(nature_key, reported_records order by nature_key)
            as payment_nature_record_counts,
        jsonb_object_agg(nature_key, reported_amount_usd order by nature_key)
            as payment_nature_reported_amounts_usd
    from (
        select
            program_year,
            coalesce(nature_of_payment, '[missing]') as nature_key,
            count(*) as reported_records,
            round(sum(total_amount_usd), 2) as reported_amount_usd
        from cohort
        group by program_year, coalesce(nature_of_payment, '[missing]')
    ) as nature
    group by program_year
), recipient_type_counts as (
    select
        program_year,
        jsonb_object_agg(recipient_type_key, reported_records order by recipient_type_key)
            as covered_recipient_type_record_counts
    from (
        select
            program_year,
            coalesce(covered_recipient_type, '[missing]') as recipient_type_key,
            count(*) as reported_records
        from cohort
        group by program_year, coalesce(covered_recipient_type, '[missing]')
    ) as recipient_type
    group by program_year
), product_attributes as (
    select
        cohort.program_year,
        attribute.attribute_kind,
        attribute.attribute_value
    from cohort
    cross join lateral (
        select 'type'::text as attribute_kind, value as attribute_value
        from unnest(cohort.product_types) as value
        union all
        select 'category', value
        from unnest(cohort.product_categories) as value
        union all
        select 'name', value
        from unnest(cohort.product_names) as value
        union all
        select 'ndc', value
        from unnest(cohort.product_ndcs) as value
    ) as attribute
    where nullif(btrim(attribute.attribute_value), '') is not null
), annual_product_attributes as (
    select
        program_year,
        array_agg(distinct attribute_value order by attribute_value) filter (
            where attribute_kind = 'type'
        ) as product_types,
        array_agg(distinct attribute_value order by attribute_value) filter (
            where attribute_kind = 'category'
        ) as product_categories,
        array_agg(distinct attribute_value order by attribute_value) filter (
            where attribute_kind = 'name'
        ) as product_names,
        array_agg(distinct attribute_value order by attribute_value) filter (
            where attribute_kind = 'ndc'
        ) as product_ndcs
    from product_attributes
    group by program_year
)
select
    annual.*,
    coalesce(nature.payment_nature_record_counts, '{}'::jsonb)
        as payment_nature_record_counts,
    coalesce(nature.payment_nature_reported_amounts_usd, '{}'::jsonb)
        as payment_nature_reported_amounts_usd,
    coalesce(recipient_type.covered_recipient_type_record_counts, '{}'::jsonb)
        as covered_recipient_type_record_counts,
    coalesce(product.product_types, '{}'::text[]) as product_types,
    coalesce(product.product_categories, '{}'::text[]) as product_categories,
    coalesce(product.product_names, '{}'::text[]) as product_names,
    coalesce(product.product_ndcs, '{}'::text[]) as product_ndcs
from annual
left join payment_nature_counts as nature using (program_year)
left join recipient_type_counts as recipient_type using (program_year)
left join annual_product_attributes as product using (program_year)
order by annual.program_year;
