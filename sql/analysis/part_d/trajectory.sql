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
        drug.total_drug_cost,
        drug.ge65_total_claims,
        drug.ge65_total_beneficiaries,
        drug.ge65_total_drug_cost,
        drug.total_drug_cost / nullif(drug.total_claims, 0) as cost_per_claim,
        drug.total_claims::numeric / nullif(drug.total_beneficiaries, 0)
            as claims_per_beneficiary
    from claims.part_d_provider_drug as drug
    where drug.source_release_id = any(%(provider_release_ids)s::bigint[])
      and drug.data_year between %(from_year)s::smallint and %(to_year)s::smallint
      and lower(drug.brand_name) = lower(%(brand_name)s)
      and lower(drug.generic_name) = lower(%(generic_name)s)
), ranked as (
    select
        provider_base.*,
        rank() over (partition by data_year order by total_claims desc) as claims_rank,
        percent_rank() over (partition by data_year order by total_claims)
            as claims_percent_rank,
        count(*) over (partition by data_year) as visible_provider_rows,
        sum(total_claims) over (partition by data_year) as visible_provider_claims
    from provider_base
), distribution as (
    select
        data_year,
        percentile_cont(0.999) within group (order by total_claims) as claims_p999,
        percentile_cont(0.50) within group (order by cost_per_claim)
            as median_cost_per_claim,
        percentile_cont(0.01) within group (order by cost_per_claim) as p01_cost_per_claim,
        percentile_cont(0.99) within group (order by cost_per_claim) as p99_cost_per_claim
    from provider_base
    group by data_year
), national as (
    select
        geography.data_year,
        geography.total_prescribers,
        geography.total_claims,
        geography.total_beneficiaries,
        geography.total_drug_cost
    from claims.part_d_geography_drug as geography
    where geography.source_release_id = any(%(geography_release_ids)s::bigint[])
      and geography.data_year between %(from_year)s::smallint and %(to_year)s::smallint
      and geography.prescriber_geography_level = 'National'
      and lower(geography.brand_name) = lower(%(brand_name)s)
      and lower(geography.generic_name) = lower(%(generic_name)s)
), candidate as (
    select
        ranked.*,
        distribution.claims_p999,
        distribution.median_cost_per_claim,
        distribution.p01_cost_per_claim,
        distribution.p99_cost_per_claim,
        national.total_prescribers as national_prescribers,
        national.total_claims as national_claims,
        national.total_beneficiaries as national_beneficiaries,
        national.total_drug_cost as national_drug_cost,
        national.total_drug_cost / nullif(national.total_claims, 0)
            as national_cost_per_claim
    from ranked
    join distribution using (data_year)
    left join national using (data_year)
    where ranked.prescriber_npi = %(candidate_npi)s
), temporal as (
    select
        candidate.*,
        lag(data_year) over (order by data_year) as previous_visible_year,
        lag(total_claims) over (order by data_year) as previous_visible_claims
    from candidate
)
select
    data_year,
    prescriber_npi,
    prescriber_last_org_name,
    prescriber_first_name,
    prescriber_type,
    prescriber_city,
    prescriber_state,
    total_claims,
    total_beneficiaries,
    total_drug_cost,
    round(cost_per_claim, 2) as cost_per_claim,
    round(claims_per_beneficiary, 3) as claims_per_beneficiary,
    ge65_total_claims,
    ge65_total_beneficiaries,
    ge65_total_drug_cost,
    round(100 * ge65_total_claims::numeric / nullif(total_claims, 0), 3)
        as ge65_claim_share_pct,
    claims_rank,
    visible_provider_rows,
    round(100 * claims_percent_rank::numeric, 5) as visible_claims_percentile,
    round(claims_p999::numeric, 2) as claims_p999,
    round(total_claims / nullif(claims_p999, 0)::numeric, 3) as ratio_to_claims_p999,
    national_prescribers,
    national_claims,
    national_beneficiaries,
    national_drug_cost,
    round(national_cost_per_claim, 2) as national_cost_per_claim,
    round(100 * total_claims::numeric / nullif(national_claims, 0), 4)
        as national_claim_share_pct,
    round(100 * total_drug_cost / nullif(national_drug_cost, 0), 4)
        as national_cost_share_pct,
    round(100 * total_claims::numeric / nullif(visible_provider_claims, 0), 4)
        as visible_claim_share_pct,
    case when national_claims is null then 'missing' else 'available' end
        as national_denominator_status,
    previous_visible_year,
    case
        when previous_visible_year = data_year - 1 then round(
            100 * (total_claims - previous_visible_claims)::numeric
                / nullif(previous_visible_claims, 0),
            2
        )
    end as candidate_claim_yoy_pct,
    round(median_cost_per_claim::numeric, 2) as peer_median_cost_per_claim,
    round(p01_cost_per_claim::numeric, 2) as peer_p01_cost_per_claim,
    round(p99_cost_per_claim::numeric, 2) as peer_p99_cost_per_claim
from temporal
order by data_year;
