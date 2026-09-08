with available_pairs as (
    select year_value as from_year, (year_value + 1)::smallint as to_year
    from unnest(%(cohort_years)s::smallint[]) as year_value
    where (year_value + 1)::smallint = any(%(cohort_years)s::smallint[])
), annual as (
    select
        drug.data_year,
        drug.prescriber_npi,
        drug.prescriber_last_org_name,
        drug.prescriber_first_name,
        drug.prescriber_type,
        drug.prescriber_state,
        drug.total_claims,
        drug.total_drug_cost
    from claims.part_d_provider_drug as drug
    where drug.source_release_id = any(%(provider_release_ids)s::bigint[])
      and drug.data_year between %(from_year)s::smallint and %(to_year)s::smallint
      and lower(drug.brand_name) = lower(%(brand_name)s)
      and lower(drug.generic_name) = lower(%(generic_name)s)
), paired as (
    select
        pair.from_year,
        pair.to_year,
        annual.prescriber_npi,
        coalesce(
            max(annual.prescriber_last_org_name) filter (
                where annual.data_year = pair.to_year
            ),
            max(annual.prescriber_last_org_name) filter (
                where annual.data_year = pair.from_year
            )
        ) as prescriber_last_org_name,
        coalesce(
            max(annual.prescriber_first_name) filter (
                where annual.data_year = pair.to_year
            ),
            max(annual.prescriber_first_name) filter (
                where annual.data_year = pair.from_year
            )
        ) as prescriber_first_name,
        coalesce(
            max(annual.prescriber_type) filter (where annual.data_year = pair.to_year),
            max(annual.prescriber_type) filter (where annual.data_year = pair.from_year)
        ) as prescriber_type,
        coalesce(
            max(annual.prescriber_state) filter (where annual.data_year = pair.to_year),
            max(annual.prescriber_state) filter (where annual.data_year = pair.from_year)
        ) as prescriber_state,
        max(annual.total_claims) filter (
            where annual.data_year = pair.from_year
        ) as from_claims,
        max(annual.total_claims) filter (
            where annual.data_year = pair.to_year
        ) as to_claims,
        max(annual.total_drug_cost) filter (
            where annual.data_year = pair.from_year
        ) as from_drug_cost,
        max(annual.total_drug_cost) filter (
            where annual.data_year = pair.to_year
        ) as to_drug_cost
    from available_pairs as pair
    join annual on annual.data_year in (pair.from_year, pair.to_year)
    group by pair.from_year, pair.to_year, annual.prescriber_npi
), comparison as (
    select
        paired.*,
        case
            when paired.from_claims is not null and paired.to_claims is not null
                then paired.to_claims - paired.from_claims
        end as claim_change,
        case
            when paired.from_claims is not null and paired.to_claims is not null
                then paired.to_claims - paired.from_claims
            when paired.from_claims is null then paired.to_claims - 10
            else -paired.from_claims
        end as claim_change_lower_bound,
        case
            when paired.from_claims is not null and paired.to_claims is not null
                then paired.to_claims - paired.from_claims
            when paired.from_claims is null then paired.to_claims
            else 10 - paired.from_claims
        end as claim_change_upper_bound,
        abs(coalesce(paired.to_claims, 0) - coalesce(paired.from_claims, 0))
            as visible_change_ranking_magnitude,
        case
            when paired.from_claims is not null and paired.to_claims is not null
                then 'both-visible-exact'
            when paired.from_claims is null then 'became-visible-prior-0-to-10-or-absent'
            else 'became-nonvisible-current-0-to-10-or-absent'
        end as visibility_status,
        case
            when paired.from_drug_cost is not null and paired.to_drug_cost is not null
                then paired.to_drug_cost - paired.from_drug_cost
        end as drug_cost_change
    from paired
), ranked as (
    select
        comparison.*,
        row_number() over (
            partition by from_year, to_year
            order by visible_change_ranking_magnitude desc, prescriber_npi
        ) as change_rank
    from comparison
)
select
    from_year,
    to_year,
    prescriber_npi,
    prescriber_last_org_name,
    prescriber_first_name,
    prescriber_type,
    prescriber_state,
    from_claims,
    to_claims,
    claim_change,
    claim_change_lower_bound,
    claim_change_upper_bound,
    visible_change_ranking_magnitude,
    visibility_status,
    round(100 * claim_change::numeric / nullif(from_claims, 0), 2) as claim_change_pct,
    from_drug_cost,
    to_drug_cost,
    drug_cost_change,
    change_rank
from ranked
where change_rank <= %(leader_limit)s::integer
order by from_year, change_rank, prescriber_npi;
