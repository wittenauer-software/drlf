create table claims.part_b_provider (
    source_release_id bigint not null
        references metadata.source_release(source_release_id),
    data_year smallint not null check (data_year between 2013 and 2200),
    rendering_npi text not null check (rendering_npi ~ '^[0-9]{10}$'),
    provider_last_org_name text,
    provider_first_name text,
    provider_middle_initial text,
    provider_credentials text,
    entity_code text not null check (entity_code in ('I', 'O')),
    provider_address_line1 text,
    provider_address_line2 text,
    provider_city text,
    provider_state text,
    provider_state_fips text,
    provider_zip5 text,
    provider_ruca text,
    provider_ruca_description text,
    provider_country text,
    provider_type text not null,
    medicare_participation_indicator text not null
        check (medicare_participation_indicator in ('Y', 'N')),
    total_hcpcs_codes bigint not null check (total_hcpcs_codes >= 0),
    total_beneficiaries bigint not null check (total_beneficiaries >= 0),
    total_services numeric not null check (total_services >= 0),
    total_submitted_charge numeric not null check (total_submitted_charge >= 0),
    total_medicare_allowed_amount numeric not null
        check (total_medicare_allowed_amount >= 0),
    total_medicare_payment_amount numeric not null
        check (total_medicare_payment_amount >= 0),
    total_medicare_standardized_amount numeric not null
        check (total_medicare_standardized_amount >= 0),
    drug_suppression_indicator text,
    drug_total_hcpcs_codes bigint,
    drug_total_beneficiaries bigint,
    drug_total_services numeric,
    drug_submitted_charge numeric,
    drug_medicare_allowed_amount numeric,
    drug_medicare_payment_amount numeric,
    drug_medicare_standardized_amount numeric,
    medical_suppression_indicator text,
    medical_total_hcpcs_codes bigint,
    medical_total_beneficiaries bigint,
    medical_total_services numeric,
    medical_submitted_charge numeric,
    medical_medicare_allowed_amount numeric,
    medical_medicare_payment_amount numeric,
    medical_medicare_standardized_amount numeric,
    beneficiary_average_age numeric,
    beneficiary_age_lt_65_count bigint,
    beneficiary_age_65_74_count bigint,
    beneficiary_age_75_84_count bigint,
    beneficiary_age_gt_84_count bigint,
    beneficiary_female_count bigint,
    beneficiary_male_count bigint,
    beneficiary_race_white_count bigint,
    beneficiary_race_black_count bigint,
    beneficiary_race_api_count bigint,
    beneficiary_race_hispanic_count bigint,
    beneficiary_race_native_indian_count bigint,
    beneficiary_race_other_count bigint,
    beneficiary_dual_count bigint,
    beneficiary_nondual_count bigint,
    beneficiary_adhd_other_conduct_disorder_percent numeric,
    beneficiary_alcohol_drug_use_disorder_percent numeric,
    tobacco_pct numeric,
    beneficiary_alzheimer_non_alzheimer_dementia_percent numeric,
    beneficiary_anxiety_disorder_percent numeric,
    beneficiary_bipolar_disorder_percent numeric,
    beneficiary_mood_disorder_percent numeric,
    beneficiary_depression_percent numeric,
    beneficiary_personality_disorder_percent numeric,
    beneficiary_ptsd_percent numeric,
    beneficiary_schizophrenia_other_psychotic_disorder_percent numeric,
    asthma_pct numeric,
    beneficiary_atrial_fibrillation_percent numeric,
    beneficiary_cancer_percent numeric,
    beneficiary_chronic_kidney_disease_percent numeric,
    copd_pct numeric,
    beneficiary_diabetes_percent numeric,
    beneficiary_heart_failure_non_ischemic_heart_disease_percent numeric,
    beneficiary_hyperlipidemia_percent numeric,
    beneficiary_hypertension_percent numeric,
    beneficiary_ischemic_heart_disease_percent numeric,
    beneficiary_osteoporosis_percent numeric,
    beneficiary_parkinson_disease_percent numeric,
    beneficiary_arthritis_percent numeric,
    beneficiary_stroke_tia_percent numeric,
    average_risk_score numeric,
    primary key (source_release_id, rendering_npi)
);

create index part_b_provider_type_year_idx
    on claims.part_b_provider (provider_type, data_year);

create index part_b_provider_state_year_idx
    on claims.part_b_provider (provider_state, data_year);

create table claims.part_b_provider_service (
    source_release_id bigint not null
        references metadata.source_release(source_release_id),
    data_year smallint not null check (data_year between 2013 and 2200),
    rendering_npi text not null check (rendering_npi ~ '^[0-9]{10}$'),
    provider_last_org_name text,
    provider_first_name text,
    provider_middle_initial text,
    provider_credentials text,
    entity_code text not null check (entity_code in ('I', 'O')),
    provider_address_line1 text,
    provider_address_line2 text,
    provider_city text,
    provider_state text,
    provider_state_fips text,
    provider_zip5 text,
    provider_ruca text,
    provider_ruca_description text,
    provider_country text,
    provider_type text not null,
    medicare_participation_indicator text not null
        check (medicare_participation_indicator in ('Y', 'N')),
    hcpcs_code text not null,
    hcpcs_description text not null,
    hcpcs_drug_indicator text not null,
    place_of_service text not null check (place_of_service in ('F', 'O')),
    total_beneficiaries bigint not null check (total_beneficiaries >= 0),
    total_services numeric not null check (total_services >= 0),
    total_beneficiary_day_services numeric not null
        check (total_beneficiary_day_services >= 0),
    average_submitted_charge numeric not null check (average_submitted_charge >= 0),
    average_medicare_allowed_amount numeric not null
        check (average_medicare_allowed_amount >= 0),
    average_medicare_payment_amount numeric not null
        check (average_medicare_payment_amount >= 0),
    average_medicare_standardized_amount numeric not null
        check (average_medicare_standardized_amount >= 0),
    primary key (source_release_id, rendering_npi, hcpcs_code, place_of_service)
);

create index part_b_provider_service_npi_year_idx
    on claims.part_b_provider_service (rendering_npi, data_year);

create index part_b_provider_service_peer_idx
    on claims.part_b_provider_service (
        provider_type,
        data_year,
        hcpcs_code,
        place_of_service
    );

comment on table claims.part_b_provider is
    'Medicare fee-for-service Part B non-institutional aggregates at source release and rendering NPI grain.';
comment on table claims.part_b_provider_service is
    'Medicare fee-for-service Part B non-institutional aggregates at source release, rendering NPI, HCPCS code, and facility/non-facility place-of-service grain.';
comment on column claims.part_b_provider.rendering_npi is
    'Rendering-provider NPI reported on the claim; it does not by itself identify the billing entity, payment recipient, or physical service location.';
comment on column claims.part_b_provider.provider_type is
    'CMS provider type derived from the claim specialty associated with the provider''s largest number of services; not an NPPES taxonomy assertion.';
comment on column claims.part_b_provider.provider_address_line1 is
    'Provider address reported through NPPES; not necessarily the location where a service was furnished.';
comment on column claims.part_b_provider.total_services is
    'CMS service-unit measure. It is numeric and must not be interpreted as a count of visits or unique claims.';
comment on column claims.part_b_provider.total_submitted_charge is
    'Total amount submitted on Part B claim lines; not an allowed amount, Medicare payment, provider receipt, or program loss.';
comment on column claims.part_b_provider.total_medicare_allowed_amount is
    'Total Medicare allowed amount, including Medicare payment and beneficiary or third-party cost sharing.';
comment on column claims.part_b_provider.total_medicare_payment_amount is
    'Total Medicare payment after deductible and coinsurance; not total provider revenue.';
comment on column claims.part_b_provider.asthma_pct is
    'Percent of attributed beneficiaries meeting the CMS CCW asthma algorithm; subject to source suppression and top-coding rules.';
comment on column claims.part_b_provider.copd_pct is
    'Percent of attributed beneficiaries meeting the CMS CCW COPD algorithm; subject to source suppression and top-coding rules.';
comment on column claims.part_b_provider.tobacco_pct is
    'Percent of attributed beneficiaries meeting the CMS CCW tobacco-use-disorder algorithm; subject to source suppression and top-coding rules.';
comment on column claims.part_b_provider.average_risk_score is
    'Average CMS HCC risk score for the provider beneficiary population; context rather than proof of a service indication.';
comment on column claims.part_b_provider_service.rendering_npi is
    'Rendering-provider NPI reported on the claim; it does not by itself identify the billing entity or payment recipient.';
comment on column claims.part_b_provider_service.place_of_service is
    'CMS broad facility (F) or non-facility (O) grouping, not an exact service address.';
comment on column claims.part_b_provider_service.total_services is
    'CMS service units at NPI, HCPCS, and place-of-service grain. Numeric values are preserved without coercion to visit counts.';
comment on column claims.part_b_provider_service.total_beneficiary_day_services is
    'CMS beneficiary-day service measure at the published source grain; not a unique annual beneficiary count.';
comment on column claims.part_b_provider_service.average_submitted_charge is
    'Average submitted charge per published service unit; not an allowed amount or payment.';
comment on column claims.part_b_provider_service.average_medicare_allowed_amount is
    'Average allowed amount per published service unit, including Medicare payment and beneficiary or third-party cost sharing.';
comment on column claims.part_b_provider_service.average_medicare_payment_amount is
    'Average Medicare-paid amount per published service unit after deductible and coinsurance; not total provider revenue.';
