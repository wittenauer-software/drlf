create table claims.part_d_geography_drug (
    source_release_id bigint not null
        references metadata.source_release(source_release_id),
    data_year smallint not null check (data_year between 2013 and 2200),
    prescriber_geography_level text not null
        check (prescriber_geography_level in ('National', 'State')),
    prescriber_geography_code text not null,
    prescriber_geography_description text not null,
    brand_name text not null,
    generic_name text not null,
    total_prescribers bigint not null check (total_prescribers >= 0),
    total_claims bigint not null check (total_claims >= 0),
    total_30day_fills numeric(18, 3),
    total_drug_cost numeric(18, 2) not null check (total_drug_cost >= 0),
    total_beneficiaries bigint,
    ge65_suppression_flag text,
    ge65_total_claims bigint,
    ge65_total_30day_fills numeric(18, 3),
    ge65_total_drug_cost numeric(18, 2),
    ge65_beneficiary_suppression_flag text,
    ge65_total_beneficiaries bigint,
    lis_beneficiary_cost_share numeric(18, 2),
    nonlis_beneficiary_cost_share numeric(18, 2),
    opioid_drug_flag text,
    long_acting_opioid_drug_flag text,
    antibiotic_drug_flag text,
    antipsychotic_drug_flag text,
    primary key (
        source_release_id,
        prescriber_geography_level,
        prescriber_geography_code,
        brand_name,
        generic_name
    ),
    check (
        (
            prescriber_geography_level = 'National'
            and prescriber_geography_code = ''
        )
        or (
            prescriber_geography_level = 'State'
            and prescriber_geography_code ~ '^[0-9A-Z]{2}$'
        )
    )
);

create index part_d_geography_drug_brand_year_idx
    on claims.part_d_geography_drug (brand_name, data_year);

create index part_d_geography_drug_geography_year_idx
    on claims.part_d_geography_drug (
        prescriber_geography_level,
        prescriber_geography_code,
        data_year
    );

comment on table claims.part_d_geography_drug is
    'Public Medicare Part D PDE aggregates at prescriber geography, brand, generic, and year grain.';
comment on column claims.part_d_geography_drug.prescriber_geography_code is
    'CMS prescriber-geography code; intentionally blank for the National row and two-character FIPS-like code for State rows.';
comment on column claims.part_d_geography_drug.prescriber_geography_description is
    'Provider geography derived from NPPES assertions; not beneficiary residence, dispensing pharmacy, or service location.';
comment on column claims.part_d_geography_drug.total_drug_cost is
    'Ingredient cost, dispensing fee, sales tax, and applicable administration fees paid by plans, beneficiaries, subsidies, and third parties; not Medicare payment or provider revenue.';
comment on column claims.part_d_geography_drug.lis_beneficiary_cost_share is
    'Aggregate annual amount paid by beneficiaries using the drug who received a low-income subsidy.';
comment on column claims.part_d_geography_drug.nonlis_beneficiary_cost_share is
    'Aggregate annual amount paid by beneficiaries using the drug who did not receive a low-income subsidy.';
