alter table relationships.open_payments_general
    add column product_slots jsonb not null default '[]'::jsonb
        check (jsonb_typeof(product_slots) = 'array');

comment on column relationships.open_payments_general.product_slots is
    'Published product fields grouped by their original Open Payments slot ordinal. Null fields remain within populated slots so names, categories, NDCs, and device identifiers cannot be falsely paired after normalization.';
