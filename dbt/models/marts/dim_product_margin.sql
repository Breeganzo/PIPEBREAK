-- Product-grain margin. Revenue is converted per line at the order-date rate;
-- cost is already stated in USD in the product master.
with lines as (
    select
        li.sku,
        li.quantity,
        case when o.status = 'refunded' then -1 else 1 end
            * li.line_amount_local * f.rate_to_usd as line_revenue_usd
    from {{ ref('stg_order_items') }} li
    join {{ ref('stg_orders') }} o
        on o.order_id = li.order_id
    join {{ ref('stg_fx') }} f
        on f.currency = o.currency
       and f.rate_date = o.order_date
    where o.status <> 'cancelled'
)

select
    p.sku,
    p.category,
    sum(l.quantity)                             as units_sold,
    round(sum(l.line_revenue_usd), 2)           as revenue_usd,
    round(sum(l.quantity * p.cost_usd), 2)      as cogs_usd,
    round(sum(l.line_revenue_usd) - sum(l.quantity * p.cost_usd), 2) as margin_usd,
    round(
        100.0 * (sum(l.line_revenue_usd) - sum(l.quantity * p.cost_usd))
        / nullif(sum(l.line_revenue_usd), 0), 2
    ) as margin_pct
from lines l
join {{ ref('stg_products') }} p
    on p.sku = l.sku
group by 1, 2
