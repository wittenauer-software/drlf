create schema if not exists relationships;

create table relationships.open_payments_general (
    source_release_id bigint not null
        references metadata.source_release(source_release_id),
    program_year smallint not null check (program_year between 2013 and 2200),
    record_id text not null,
    change_type text,
    covered_recipient_type text,
    covered_recipient_profile_id text,
    covered_recipient_npi text
        check (covered_recipient_npi is null or covered_recipient_npi ~ '^[0-9]{10}$'),
    covered_recipient_first_name text,
    covered_recipient_middle_name text,
    covered_recipient_last_name text,
    covered_recipient_name_suffix text,
    recipient_city text,
    recipient_state text,
    recipient_zip_code text,
    recipient_country text,
    recipient_primary_types text[] not null default '{}',
    recipient_specialties text[] not null default '{}',
    recipient_license_states text[] not null default '{}',
    submitting_entity_name text,
    paying_entity_id text not null,
    paying_entity_name text not null,
    paying_entity_state text,
    paying_entity_country text,
    total_amount_usd numeric(18, 2) not null check (total_amount_usd >= 0),
    payment_date date not null,
    number_of_payments bigint check (number_of_payments is null or number_of_payments > 0),
    form_of_payment text,
    nature_of_payment text,
    travel_city text,
    travel_state text,
    travel_country text,
    physician_ownership_indicator text,
    third_party_payment_recipient_indicator text,
    third_party_entity_name text,
    charity_indicator text,
    third_party_equals_covered_recipient_indicator text,
    contextual_information text,
    delay_in_publication_indicator text,
    dispute_status text,
    related_product_indicator text,
    product_coverage_indicators text[] not null default '{}',
    product_types text[] not null default '{}',
    product_categories text[] not null default '{}',
    product_names text[] not null default '{}',
    product_ndcs text[] not null default '{}',
    product_pdis text[] not null default '{}',
    payment_publication_date date,
    primary key (source_release_id, record_id)
);

create index open_payments_general_paying_entity_year_idx
    on relationships.open_payments_general (paying_entity_id, program_year);

create index open_payments_general_recipient_npi_year_idx
    on relationships.open_payments_general (covered_recipient_npi, program_year)
    where covered_recipient_npi is not null;

create index open_payments_general_nature_year_idx
    on relationships.open_payments_general (nature_of_payment, program_year);

create index open_payments_general_third_party_entity_year_idx
    on relationships.open_payments_general (third_party_entity_name, program_year)
    where third_party_entity_name is not null;

comment on schema relationships is
    'Effective-dated public relationship and transparency records kept separate from healthcare claims.';
comment on table relationships.open_payments_general is
    'CMS Open Payments General Payment records at source-release and Record_ID grain. These are reporting-entity-submitted transparency records, not healthcare claims.';
comment on column relationships.open_payments_general.paying_entity_id is
    'Open Payments identifier for the applicable manufacturer or GPO reported as making the payment; it is not a Medicare billing-provider identifier.';
comment on column relationships.open_payments_general.covered_recipient_npi is
    'NPI of the covered recipient when published. It does not establish a claims prescriber, dispenser, administrator, service location, biller, or Medicare payment recipient role.';
comment on column relationships.open_payments_general.total_amount_usd is
    'Gross payment or transfer-of-value amount reported to Open Payments. It is not a Medicare claim payment, pharmacy reimbursement, covered-recipient net income, or estimated program loss.';
comment on column relationships.open_payments_general.third_party_entity_name is
    'Published third-party entity receiving the transfer when supplied; blank does not establish that the covered recipient personally retained the value.';
