select
    order_date,
    count(*)                   as order_count,
    round(sum(revenue_usd), 2) as revenue_usd
from {{ ref('fct_order_revenue') }}
group by 1
order by 1
