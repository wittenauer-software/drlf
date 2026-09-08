with requested_years as (
    select generate_series(
        %(from_year)s::integer,
        %(to_year)s::integer
    )::smallint as data_year
), national as (
    select
        data_year,
        total_prescribers,
        total_claims,
        total_beneficiaries,
        total_drug_cost,
        ge65_total_claims,
        ge65_total_beneficiaries,
        ge65_total_drug_cost,
        lis_beneficiary_cost_share,
        nonlis_beneficiary_cost_share,
        source_release_id
    from claims.part_d_geography_drug
    where source_release_id = any(%(geography_release_ids)s::bigint[])
      and data_year between %(from_year)s::smallint and %(to_year)s::smallint
      and prescriber_geography_level = 'National'
      and lower(brand_name) = lower(%(brand_name)s)
      and lower(generic_name) = lower(%(generic_name)s)
)
select
    requested_years.data_year,
    national.total_prescribers,
    national.total_claims,
    national.total_beneficiaries,
    national.total_drug_cost,
    national.ge65_total_claims,
    national.ge65_total_beneficiaries,
    national.ge65_total_drug_cost,
    national.lis_beneficiary_cost_share,
    national.nonlis_beneficiary_cost_share,
    national.source_release_id,
    case
        when national.source_release_id is null or national.total_claims is null
            or national.total_claims <= 0 then 'missing'
        else 'available'
    end
        as national_denominator_status
from requested_years
left join national using (data_year)
order by requested_years.data_year;
