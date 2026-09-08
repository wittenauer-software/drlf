with provider_base as (
    select
        drug.data_year,
        drug.prescriber_npi,
        drug.prescriber_last_org_name,
        drug.prescriber_first_name,
        drug.prescriber_type,
        drug.prescriber_city,
        drug.prescriber_state,
        drug.total_claims,
        drug.total_beneficiaries,
        drug.total_drug_cost
    from claims.part_d_provider_drug as drug
    where drug.source_release_id = any(%(provider_release_ids)s::bigint[])
      and drug.data_year between %(from_year)s::smallint and %(to_year)s::smallint
      and lower(drug.brand_name) = lower(%(brand_name)s)
      and lower(drug.generic_name) = lower(%(generic_name)s)
), ranked as (
    select
        provider_base.*,
        rank() over (partition by data_year order by total_claims desc) as claims_rank,
        row_number() over (
            partition by data_year
            order by total_claims desc, prescriber_npi
        ) as bounded_row_number,
        percent_rank() over (partition by data_year order by total_claims)
            as claims_percent_rank,
        count(*) over (partition by data_year) as visible_provider_rows
    from provider_base
), national as (
    select data_year, total_claims as national_claims
    from claims.part_d_geography_drug
    where source_release_id = any(%(geography_release_ids)s::bigint[])
      and data_year between %(from_year)s::smallint and %(to_year)s::smallint
      and prescriber_geography_level = 'National'
      and lower(brand_name) = lower(%(brand_name)s)
      and lower(generic_name) = lower(%(generic_name)s)
)
select
    ranked.data_year,
    ranked.prescriber_npi,
    ranked.prescriber_last_org_name,
    ranked.prescriber_first_name,
    ranked.prescriber_type,
    ranked.prescriber_city,
    ranked.prescriber_state,
    ranked.total_claims,
    ranked.total_beneficiaries,
    ranked.total_drug_cost,
    round(ranked.total_drug_cost / nullif(ranked.total_claims, 0), 2) as cost_per_claim,
    round(ranked.total_claims::numeric / nullif(ranked.total_beneficiaries, 0), 3)
        as claims_per_beneficiary,
    ranked.claims_rank,
    ranked.bounded_row_number,
    round(100 * ranked.claims_percent_rank::numeric, 5) as visible_claims_percentile,
    national.national_claims,
    round(100 * ranked.total_claims::numeric / nullif(national.national_claims, 0), 4)
        as national_claim_share_pct,
    case when national.national_claims is null then 'missing' else 'available' end
        as national_denominator_status,
    ranked.visible_provider_rows
from ranked
left join national using (data_year)
where ranked.bounded_row_number <= %(leader_limit)s::integer
order by ranked.data_year, ranked.bounded_row_number;
