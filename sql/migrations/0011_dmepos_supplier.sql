create table claims.dmepos_supplier (
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
    supplier_ruca text,
    supplier_ruca_description text,
    supplier_country text,
    supplier_specialty_description text,
    supplier_specialty_source text,
    total_hcpcs_codes bigint not null check (total_hcpcs_codes >= 0),
    total_beneficiaries bigint check (total_beneficiaries is null or total_beneficiaries >= 0),
    total_claims bigint not null check (total_claims >= 0),
    total_services numeric not null check (total_services >= 0),
    total_submitted_charge numeric not null check (total_submitted_charge >= 0),
    total_medicare_allowed_amount numeric not null
        check (total_medicare_allowed_amount >= 0),
    total_medicare_payment_amount numeric not null
        check (total_medicare_payment_amount >= 0),
    total_medicare_standardized_payment_amount numeric not null
        check (total_medicare_standardized_payment_amount >= 0),
    dme_suppression_indicator text,
    dme_total_hcpcs_codes bigint check (dme_total_hcpcs_codes is null or dme_total_hcpcs_codes >= 0),
    dme_total_beneficiaries bigint
        check (dme_total_beneficiaries is null or dme_total_beneficiaries >= 0),
    dme_total_claims bigint check (dme_total_claims is null or dme_total_claims >= 0),
    dme_total_services numeric check (dme_total_services is null or dme_total_services >= 0),
    dme_submitted_charge numeric check (dme_submitted_charge is null or dme_submitted_charge >= 0),
    dme_medicare_allowed_amount numeric
        check (dme_medicare_allowed_amount is null or dme_medicare_allowed_amount >= 0),
    dme_medicare_payment_amount numeric
        check (dme_medicare_payment_amount is null or dme_medicare_payment_amount >= 0),
    dme_medicare_standardized_payment_amount numeric
        check (
            dme_medicare_standardized_payment_amount is null
            or dme_medicare_standardized_payment_amount >= 0
        ),
    pos_suppression_indicator text,
    pos_total_hcpcs_codes bigint check (pos_total_hcpcs_codes is null or pos_total_hcpcs_codes >= 0),
    pos_total_beneficiaries bigint
        check (pos_total_beneficiaries is null or pos_total_beneficiaries >= 0),
    pos_total_claims bigint check (pos_total_claims is null or pos_total_claims >= 0),
    pos_total_services numeric check (pos_total_services is null or pos_total_services >= 0),
    pos_submitted_charge numeric check (pos_submitted_charge is null or pos_submitted_charge >= 0),
    pos_medicare_allowed_amount numeric
        check (pos_medicare_allowed_amount is null or pos_medicare_allowed_amount >= 0),
    pos_medicare_payment_amount numeric
        check (pos_medicare_payment_amount is null or pos_medicare_payment_amount >= 0),
    pos_medicare_standardized_payment_amount numeric
        check (
            pos_medicare_standardized_payment_amount is null
            or pos_medicare_standardized_payment_amount >= 0
        ),
    drug_suppression_indicator text,
    drug_total_hcpcs_codes bigint
        check (drug_total_hcpcs_codes is null or drug_total_hcpcs_codes >= 0),
    drug_total_beneficiaries bigint
        check (drug_total_beneficiaries is null or drug_total_beneficiaries >= 0),
    drug_total_claims bigint check (drug_total_claims is null or drug_total_claims >= 0),
    drug_total_services numeric check (drug_total_services is null or drug_total_services >= 0),
    drug_submitted_charge numeric
        check (drug_submitted_charge is null or drug_submitted_charge >= 0),
    drug_medicare_allowed_amount numeric
        check (drug_medicare_allowed_amount is null or drug_medicare_allowed_amount >= 0),
    drug_medicare_payment_amount numeric
        check (drug_medicare_payment_amount is null or drug_medicare_payment_amount >= 0),
    drug_medicare_standardized_payment_amount numeric
        check (
            drug_medicare_standardized_payment_amount is null
            or drug_medicare_standardized_payment_amount >= 0
        ),
    beneficiary_average_age numeric
        check (beneficiary_average_age is null or beneficiary_average_age between 0 and 120),
    beneficiary_age_lt_65_count bigint
        check (beneficiary_age_lt_65_count is null or beneficiary_age_lt_65_count >= 0),
    beneficiary_age_65_74_count bigint
        check (beneficiary_age_65_74_count is null or beneficiary_age_65_74_count >= 0),
    beneficiary_age_75_84_count bigint
        check (beneficiary_age_75_84_count is null or beneficiary_age_75_84_count >= 0),
    beneficiary_age_gt_84_count bigint
        check (beneficiary_age_gt_84_count is null or beneficiary_age_gt_84_count >= 0),
    beneficiary_female_count bigint
        check (beneficiary_female_count is null or beneficiary_female_count >= 0),
    beneficiary_male_count bigint
        check (beneficiary_male_count is null or beneficiary_male_count >= 0),
    beneficiary_race_white_count bigint
        check (beneficiary_race_white_count is null or beneficiary_race_white_count >= 0),
    beneficiary_race_black_count bigint
        check (beneficiary_race_black_count is null or beneficiary_race_black_count >= 0),
    beneficiary_race_api_count bigint
        check (beneficiary_race_api_count is null or beneficiary_race_api_count >= 0),
    beneficiary_race_hispanic_count bigint
        check (beneficiary_race_hispanic_count is null or beneficiary_race_hispanic_count >= 0),
    beneficiary_race_native_indian_count bigint
        check (
            beneficiary_race_native_indian_count is null
            or beneficiary_race_native_indian_count >= 0
        ),
    beneficiary_race_other_count bigint
        check (beneficiary_race_other_count is null or beneficiary_race_other_count >= 0),
    beneficiary_nondual_count bigint
        check (beneficiary_nondual_count is null or beneficiary_nondual_count >= 0),
    beneficiary_dual_count bigint
        check (beneficiary_dual_count is null or beneficiary_dual_count >= 0),
    beneficiary_adhd_other_conduct_disorder_percent numeric
        check (
            beneficiary_adhd_other_conduct_disorder_percent is null
            or beneficiary_adhd_other_conduct_disorder_percent >= 0
        ),
    beneficiary_alcohol_drug_use_disorder_percent numeric
        check (
            beneficiary_alcohol_drug_use_disorder_percent is null
            or beneficiary_alcohol_drug_use_disorder_percent >= 0
        ),
    tobacco_pct numeric check (tobacco_pct is null or tobacco_pct >= 0),
    beneficiary_alzheimer_non_alzheimer_dementia_percent numeric
        check (
            beneficiary_alzheimer_non_alzheimer_dementia_percent is null
            or beneficiary_alzheimer_non_alzheimer_dementia_percent >= 0
        ),
    beneficiary_anxiety_disorder_percent numeric
        check (
            beneficiary_anxiety_disorder_percent is null
            or beneficiary_anxiety_disorder_percent >= 0
        ),
    beneficiary_bipolar_disorder_percent numeric
        check (
            beneficiary_bipolar_disorder_percent is null
            or beneficiary_bipolar_disorder_percent >= 0
        ),
    beneficiary_mood_disorder_percent numeric
        check (
            beneficiary_mood_disorder_percent is null
            or beneficiary_mood_disorder_percent >= 0
        ),
    beneficiary_depression_percent numeric
        check (
            beneficiary_depression_percent is null
            or beneficiary_depression_percent >= 0
        ),
    beneficiary_personality_disorder_percent numeric
        check (
            beneficiary_personality_disorder_percent is null
            or beneficiary_personality_disorder_percent >= 0
        ),
    beneficiary_ptsd_percent numeric
        check (beneficiary_ptsd_percent is null or beneficiary_ptsd_percent >= 0),
    beneficiary_schizophrenia_other_psychotic_disorder_percent numeric
        check (
            beneficiary_schizophrenia_other_psychotic_disorder_percent is null
            or beneficiary_schizophrenia_other_psychotic_disorder_percent >= 0
        ),
    asthma_pct numeric check (asthma_pct is null or asthma_pct >= 0),
    beneficiary_atrial_fibrillation_percent numeric
        check (
            beneficiary_atrial_fibrillation_percent is null
            or beneficiary_atrial_fibrillation_percent >= 0
        ),
    beneficiary_cancer_percent numeric
        check (beneficiary_cancer_percent is null or beneficiary_cancer_percent >= 0),
    beneficiary_chronic_kidney_disease_percent numeric
        check (
            beneficiary_chronic_kidney_disease_percent is null
            or beneficiary_chronic_kidney_disease_percent >= 0
        ),
    copd_pct numeric check (copd_pct is null or copd_pct >= 0),
    beneficiary_diabetes_percent numeric
        check (beneficiary_diabetes_percent is null or beneficiary_diabetes_percent >= 0),
    beneficiary_heart_failure_non_ischemic_heart_disease_percent numeric
        check (
            beneficiary_heart_failure_non_ischemic_heart_disease_percent is null
            or beneficiary_heart_failure_non_ischemic_heart_disease_percent >= 0
        ),
    beneficiary_hyperlipidemia_percent numeric
        check (
            beneficiary_hyperlipidemia_percent is null
            or beneficiary_hyperlipidemia_percent >= 0
        ),
    beneficiary_hypertension_percent numeric
        check (
            beneficiary_hypertension_percent is null
            or beneficiary_hypertension_percent >= 0
        ),
    beneficiary_ischemic_heart_disease_percent numeric
        check (
            beneficiary_ischemic_heart_disease_percent is null
            or beneficiary_ischemic_heart_disease_percent >= 0
        ),
    beneficiary_osteoporosis_percent numeric
        check (
            beneficiary_osteoporosis_percent is null
            or beneficiary_osteoporosis_percent >= 0
        ),
    beneficiary_parkinson_disease_percent numeric
        check (
            beneficiary_parkinson_disease_percent is null
            or beneficiary_parkinson_disease_percent >= 0
        ),
    beneficiary_arthritis_percent numeric
        check (
            beneficiary_arthritis_percent is null
            or beneficiary_arthritis_percent >= 0
        ),
    beneficiary_stroke_tia_percent numeric
        check (
            beneficiary_stroke_tia_percent is null
            or beneficiary_stroke_tia_percent >= 0
        ),
    average_risk_score numeric
        check (average_risk_score is null or average_risk_score >= 0),
    constraint dmepos_supplier_numeric_values_finite check (
        total_services < 'Infinity'::numeric
        and total_submitted_charge < 'Infinity'::numeric
        and total_medicare_allowed_amount < 'Infinity'::numeric
        and total_medicare_payment_amount < 'Infinity'::numeric
        and total_medicare_standardized_payment_amount < 'Infinity'::numeric
        and (dme_total_services is null or dme_total_services < 'Infinity'::numeric)
        and (dme_submitted_charge is null or dme_submitted_charge < 'Infinity'::numeric)
        and (
            dme_medicare_allowed_amount is null
            or dme_medicare_allowed_amount < 'Infinity'::numeric
        )
        and (
            dme_medicare_payment_amount is null
            or dme_medicare_payment_amount < 'Infinity'::numeric
        )
        and (
            dme_medicare_standardized_payment_amount is null
            or dme_medicare_standardized_payment_amount < 'Infinity'::numeric
        )
        and (pos_total_services is null or pos_total_services < 'Infinity'::numeric)
        and (pos_submitted_charge is null or pos_submitted_charge < 'Infinity'::numeric)
        and (
            pos_medicare_allowed_amount is null
            or pos_medicare_allowed_amount < 'Infinity'::numeric
        )
        and (
            pos_medicare_payment_amount is null
            or pos_medicare_payment_amount < 'Infinity'::numeric
        )
        and (
            pos_medicare_standardized_payment_amount is null
            or pos_medicare_standardized_payment_amount < 'Infinity'::numeric
        )
        and (drug_total_services is null or drug_total_services < 'Infinity'::numeric)
        and (drug_submitted_charge is null or drug_submitted_charge < 'Infinity'::numeric)
        and (
            drug_medicare_allowed_amount is null
            or drug_medicare_allowed_amount < 'Infinity'::numeric
        )
        and (
            drug_medicare_payment_amount is null
            or drug_medicare_payment_amount < 'Infinity'::numeric
        )
        and (
            drug_medicare_standardized_payment_amount is null
            or drug_medicare_standardized_payment_amount < 'Infinity'::numeric
        )
        and (beneficiary_average_age is null or beneficiary_average_age < 'Infinity'::numeric)
        and (
            beneficiary_adhd_other_conduct_disorder_percent is null
            or beneficiary_adhd_other_conduct_disorder_percent < 'Infinity'::numeric
        )
        and (
            beneficiary_alcohol_drug_use_disorder_percent is null
            or beneficiary_alcohol_drug_use_disorder_percent < 'Infinity'::numeric
        )
        and (tobacco_pct is null or tobacco_pct < 'Infinity'::numeric)
        and (
            beneficiary_alzheimer_non_alzheimer_dementia_percent is null
            or beneficiary_alzheimer_non_alzheimer_dementia_percent < 'Infinity'::numeric
        )
        and (
            beneficiary_anxiety_disorder_percent is null
            or beneficiary_anxiety_disorder_percent < 'Infinity'::numeric
        )
        and (
            beneficiary_bipolar_disorder_percent is null
            or beneficiary_bipolar_disorder_percent < 'Infinity'::numeric
        )
        and (
            beneficiary_mood_disorder_percent is null
            or beneficiary_mood_disorder_percent < 'Infinity'::numeric
        )
        and (
            beneficiary_depression_percent is null
            or beneficiary_depression_percent < 'Infinity'::numeric
        )
        and (
            beneficiary_personality_disorder_percent is null
            or beneficiary_personality_disorder_percent < 'Infinity'::numeric
        )
        and (
            beneficiary_ptsd_percent is null
            or beneficiary_ptsd_percent < 'Infinity'::numeric
        )
        and (
            beneficiary_schizophrenia_other_psychotic_disorder_percent is null
            or beneficiary_schizophrenia_other_psychotic_disorder_percent
                < 'Infinity'::numeric
        )
        and (asthma_pct is null or asthma_pct < 'Infinity'::numeric)
        and (
            beneficiary_atrial_fibrillation_percent is null
            or beneficiary_atrial_fibrillation_percent < 'Infinity'::numeric
        )
        and (
            beneficiary_cancer_percent is null
            or beneficiary_cancer_percent < 'Infinity'::numeric
        )
        and (
            beneficiary_chronic_kidney_disease_percent is null
            or beneficiary_chronic_kidney_disease_percent < 'Infinity'::numeric
        )
        and (copd_pct is null or copd_pct < 'Infinity'::numeric)
        and (
            beneficiary_diabetes_percent is null
            or beneficiary_diabetes_percent < 'Infinity'::numeric
        )
        and (
            beneficiary_heart_failure_non_ischemic_heart_disease_percent is null
            or beneficiary_heart_failure_non_ischemic_heart_disease_percent
                < 'Infinity'::numeric
        )
        and (
            beneficiary_hyperlipidemia_percent is null
            or beneficiary_hyperlipidemia_percent < 'Infinity'::numeric
        )
        and (
            beneficiary_hypertension_percent is null
            or beneficiary_hypertension_percent < 'Infinity'::numeric
        )
        and (
            beneficiary_ischemic_heart_disease_percent is null
            or beneficiary_ischemic_heart_disease_percent < 'Infinity'::numeric
        )
        and (
            beneficiary_osteoporosis_percent is null
            or beneficiary_osteoporosis_percent < 'Infinity'::numeric
        )
        and (
            beneficiary_parkinson_disease_percent is null
            or beneficiary_parkinson_disease_percent < 'Infinity'::numeric
        )
        and (
            beneficiary_arthritis_percent is null
            or beneficiary_arthritis_percent < 'Infinity'::numeric
        )
        and (
            beneficiary_stroke_tia_percent is null
            or beneficiary_stroke_tia_percent < 'Infinity'::numeric
        )
        and (average_risk_score is null or average_risk_score < 'Infinity'::numeric)
    ),
    primary key (source_release_id, supplier_npi)
);

create index dmepos_supplier_specialty_year_idx
    on claims.dmepos_supplier (supplier_specialty_description, data_year);

create index dmepos_supplier_npi_year_idx
    on claims.dmepos_supplier (supplier_npi, data_year);

comment on table claims.dmepos_supplier is
    'Annual Medicare fee-for-service DMEPOS aggregates at source release and supplier NPI grain. Source condition-percentage values are retained exactly; values above 1 require separate source-quality resolution before case-mix use.';
comment on column claims.dmepos_supplier.supplier_npi is
    'Supplier NPI in the CMS source; it does not by itself establish ownership, fulfillment location, net revenue, or the final person retaining payment.';
comment on column claims.dmepos_supplier.total_services is
    'CMS service-unit total across DME, prosthetic/orthotic supplies, and drug categories; it is not a count of visits or unique claims.';
comment on column claims.dmepos_supplier.total_beneficiaries is
    'Distinct Original Medicare fee-for-service beneficiaries; a blank or source suppression marker is retained as unknown, not zero.';
comment on column claims.dmepos_supplier.total_submitted_charge is
    'Total supplier-submitted charges; not an allowed amount, Medicare payment, provider receipt, or program loss.';
comment on column claims.dmepos_supplier.total_medicare_allowed_amount is
    'Total Medicare allowed amount, including Medicare payment and beneficiary or third-party responsibility.';
comment on column claims.dmepos_supplier.total_medicare_payment_amount is
    'Total Medicare fee-for-service payment after deductible and coinsurance; not supplier net revenue, improper payment, or loss.';
comment on column claims.dmepos_supplier.total_medicare_standardized_payment_amount is
    'Total Medicare payment standardized for geographic differences; not actual paid dollars.';
comment on column claims.dmepos_supplier.asthma_pct is
    'Source-published condition percentage field. Some DMEPOS rows contain values above 1 despite proportion semantics; retain the source value but do not use it without a validated source-specific rule.';
comment on column claims.dmepos_supplier.supplier_address_line1 is
    'Source-published supplier address assertion; not necessarily a fulfillment, service, or beneficiary location.';
comment on column claims.dmepos_supplier.supplier_specialty_description is
    'CMS supplier specialty description from the source-designated specialty source; not necessarily an NPPES taxonomy.';
