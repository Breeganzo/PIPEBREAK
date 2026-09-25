-- Order-grain revenue, converted to USD at the rate effective on the order date.
-- Cancelled orders are excluded; refunds carry a negative sign.
with items as (
    select order_id, sum(line_amount_local) as revenue_local
    from {{ ref('stg_order_items') }}
    group by 1
)

select
    o.order_id,
    o.order_date,
    o.currency,
    o.status,
    o.ship_country,
    o.source_system,
    case when o.status = 'refunded' then -1 else 1 end * i.revenue_local
        as revenue_local,
    round(
        case when o.status = 'refunded' then -1 else 1 end
        * i.revenue_local * f.rate_to_usd, 2
    ) as revenue_usd
from {{ ref('stg_orders') }} o
join items i
    on i.order_id = o.order_id
join {{ ref('stg_fx') }} f
    on f.currency = o.currency
   and f.rate_date = o.order_date
where o.status <> 'cancelled'
