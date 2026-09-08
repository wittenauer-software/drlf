alter table relationships.open_payments_general
    add column teaching_hospital_ccn text,
    add column teaching_hospital_id text,
    add column teaching_hospital_name text,
    add column recipient_address_line1 text,
    add column recipient_address_line2 text,
    add column recipient_province text,
    add column recipient_postal_code text;

create index open_payments_general_teaching_hospital_year_idx
    on relationships.open_payments_general (teaching_hospital_id, program_year)
    where teaching_hospital_id is not null;

comment on column relationships.open_payments_general.teaching_hospital_id is
    'Open Payments teaching-hospital identifier when the published covered recipient is a teaching hospital.';
comment on column relationships.open_payments_general.recipient_address_line1 is
    'Published recipient business-address assertion for entity resolution; it does not establish a healthcare service location.';
