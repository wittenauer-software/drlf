with portfolio as (
    select
        drug.source_release_id,
        drug.data_year,
        drug.brand_name,
        drug.generic_name,
        drug.total_claims,
        drug.total_beneficiaries,
        drug.total_drug_cost,
        case
            when drug.source_release_id = any(%(full_portfolio_release_ids)s::bigint[])
                then 'all-reported-drugs'
            else 'target-brand-only'
        end as portfolio_scope
    from claims.part_d_provider_drug as drug
    where drug.source_release_id = any(%(portfolio_release_ids)s::bigint[])
      and drug.data_year between %(from_year)s::smallint and %(to_year)s::smallint
      and drug.prescriber_npi = %(candidate_npi)s
), measures as (
    select
        portfolio.*,
        rank() over (partition by data_year order by total_drug_cost desc) as annual_cost_rank,
        sum(total_claims) over (partition by data_year) as annual_total_claims,
        sum(total_drug_cost) over (partition by data_year) as annual_total_drug_cost
    from portfolio
)
select
    data_year,
    brand_name,
    generic_name,
    total_claims,
    total_beneficiaries,
    total_drug_cost,
    round(total_drug_cost / nullif(total_claims, 0), 2) as cost_per_claim,
    round(total_claims::numeric / nullif(total_beneficiaries, 0), 3)
        as claims_per_beneficiary,
    annual_cost_rank as visible_annual_cost_rank,
    round(100 * total_claims::numeric / nullif(annual_total_claims, 0), 3)
        as share_of_visible_npi_claims_pct,
    round(100 * total_drug_cost / nullif(annual_total_drug_cost, 0), 3)
        as share_of_visible_npi_cost_pct,
    annual_total_claims as visible_annual_total_claims,
    annual_total_drug_cost as visible_annual_total_drug_cost,
    portfolio_scope,
    source_release_id
from measures
order by data_year, annual_cost_rank, brand_name, generic_name;
