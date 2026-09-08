create table claims.dmepos_supplier_service (
    source_release_id bigint not null
        references metadata.source_release(source_release_id),
    data_year smallint not null check (data_year between 2013 and 2200),
    supplier_npi text not null check (supplier_npi ~ '^[0-9]{10}$'),
    supplier_last_org_name text,
    supplier_first_name text,
    supplier_middle_initial text,
    supplier_credentials text,
    entity_code text not null check (entity_code in ('I', 'O')),
    supplier_address_line1 text,
    supplier_address_line2 text,
    supplier_city text,
    supplier_state text,
    supplier_state_fips text,
    supplier_zip5 text,
    supplier_ruca_category text,
    supplier_ruca text,
    supplier_ruca_description text,
    supplier_country text,
    supplier_specialty_code text,
    supplier_specialty_description text not null,
    supplier_specialty_source text not null,
    rbcs_level text,
    rbcs_id text,
    rbcs_description text,
    hcpcs_code text not null check (hcpcs_code ~ '^[A-Z0-9]{5}$'),
    hcpcs_description text not null,
    supplier_rental_indicator text not null
        check (supplier_rental_indicator in ('Y', 'N')),
    total_beneficiaries bigint check (
        total_beneficiaries is null or total_beneficiaries >= 0
    ),
    total_claims bigint not null check (total_claims >= 0),
    total_services numeric not null check (
        total_services >= 0 and total_services < 'Infinity'::numeric
    ),
    average_submitted_charge numeric not null check (
        average_submitted_charge >= 0
        and average_submitted_charge < 'Infinity'::numeric
    ),
    average_medicare_allowed_amount numeric not null check (
        average_medicare_allowed_amount >= 0
        and average_medicare_allowed_amount < 'Infinity'::numeric
    ),
    average_medicare_payment_amount numeric not null check (
        average_medicare_payment_amount >= 0
        and average_medicare_payment_amount < 'Infinity'::numeric
    ),
    average_medicare_standardized_amount numeric not null check (
        average_medicare_standardized_amount >= 0
        and average_medicare_standardized_amount < 'Infinity'::numeric
    ),
    primary key (
        source_release_id,
        supplier_npi,
        hcpcs_code,
        supplier_rental_indicator
    )
);

create index dmepos_supplier_service_npi_year_idx
    on claims.dmepos_supplier_service (supplier_npi, data_year);

create index dmepos_supplier_service_hcpcs_year_idx
    on claims.dmepos_supplier_service (hcpcs_code, data_year);

comment on table claims.dmepos_supplier_service is
    'Targeted public Medicare fee-for-service DMEPOS detail at source release, supplier NPI, HCPCS code, and source rental-indicator grain.';
comment on column claims.dmepos_supplier_service.supplier_npi is
    'Supplying-provider NPI reported on the DMEPOS claim; not necessarily the referring provider, corporate parent, fulfillment location, remittance account, or ultimate payee.';
comment on column claims.dmepos_supplier_service.supplier_address_line1 is
    'Source-published supplier address assertion; not necessarily a furnishing, shipping, beneficiary, or payment location.';
comment on column claims.dmepos_supplier_service.supplier_rental_indicator is
    'CMS rental indicator derived from an RR modifier in either of the first two claim-line modifier positions; it is not a complete description of product ownership or fulfillment.';
comment on column claims.dmepos_supplier_service.total_beneficiaries is
    'Distinct beneficiaries at the published supplier, HCPCS, and rental-indicator cell. Null means source-suppressed or unavailable, not zero; values cannot be summed across cells.';
comment on column claims.dmepos_supplier_service.total_claims is
    'Claims at the published cell. Claims may overlap across HCPCS cells and are not unique beneficiaries or visits.';
comment on column claims.dmepos_supplier_service.total_services is
    'HCPCS-defined product or service units at the published cell. Unit meaning varies by code and must not be interpreted as visits.';
comment on column claims.dmepos_supplier_service.average_submitted_charge is
    'Average submitted charge per published service unit; not an allowed amount, payment, receipt, or loss.';
comment on column claims.dmepos_supplier_service.average_medicare_allowed_amount is
    'Average allowed amount per published service unit, including Medicare payment and beneficiary or third-party cost sharing.';
comment on column claims.dmepos_supplier_service.average_medicare_payment_amount is
    'Average Medicare-paid amount per published service unit after deductible and coinsurance; not supplier income or an improper-payment estimate.';
comment on column claims.dmepos_supplier_service.average_medicare_standardized_amount is
    'Average standardized Medicare payment per published service unit with geographic payment differences removed; not actual payment.';
