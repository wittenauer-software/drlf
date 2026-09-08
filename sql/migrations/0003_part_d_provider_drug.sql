create table claims.part_d_provider_drug (
    source_release_id bigint not null
        references metadata.source_release(source_release_id),
    data_year smallint not null check (data_year between 2013 and 2200),
    prescriber_npi text not null check (prescriber_npi ~ '^[0-9]{10}$'),
    prescriber_last_org_name text,
    prescriber_first_name text,
    prescriber_city text,
    prescriber_state text,
    prescriber_state_fips text,
    prescriber_type text,
    prescriber_type_source text,
    brand_name text not null,
    generic_name text not null,
    total_claims bigint not null check (total_claims >= 0),
    total_30day_fills numeric(18, 3),
    total_day_supply bigint,
    total_drug_cost numeric(18, 2) not null check (total_drug_cost >= 0),
    total_beneficiaries bigint,
    ge65_suppression_flag text,
    ge65_total_claims bigint,
    ge65_total_drug_cost numeric(18, 2),
    ge65_beneficiary_suppression_flag text,
    ge65_total_beneficiaries bigint,
    primary key (source_release_id, prescriber_npi, brand_name, generic_name)
);

create index part_d_provider_drug_npi_year_idx
    on claims.part_d_provider_drug (prescriber_npi, data_year);

create index part_d_provider_drug_brand_year_cost_idx
    on claims.part_d_provider_drug (brand_name, data_year, total_drug_cost desc);

comment on table claims.part_d_provider_drug is
    'Public Medicare Part D PDE aggregates at prescriber NPI, brand, generic, and year grain.';
comment on column claims.part_d_provider_drug.total_drug_cost is
    'Ingredient cost, dispensing fee, sales tax, and applicable administration fees paid by plans, beneficiaries, subsidies, and third parties; not Medicare payment or prescriber revenue.';
comment on column claims.part_d_provider_drug.prescriber_npi is
    'Prescriber identifier recorded on the PDE; not necessarily dispenser, administrator, service location, or payment recipient.';
