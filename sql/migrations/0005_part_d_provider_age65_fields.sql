alter table claims.part_d_provider_drug
    add column ge65_total_30day_fills numeric(18, 3),
    add column ge65_total_day_supply bigint;

comment on column claims.part_d_provider_drug.ge65_total_30day_fills is
    'Aggregate standardized 30-day fills attributed to beneficiaries age 65 and older when not suppressed.';
comment on column claims.part_d_provider_drug.ge65_total_day_supply is
    'Aggregate days supplied to beneficiaries age 65 and older when not suppressed.';
