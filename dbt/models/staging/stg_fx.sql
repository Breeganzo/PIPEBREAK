-- One rate per currency per day. Duplicates upstream would fan out revenue,
-- so the grain is enforced here rather than assumed.
select
    currency,
    cast(rate_date as date) as rate_date,
    max(rate_to_usd)        as rate_to_usd
from raw_fx_rates
group by 1, 2
