-- The executive-facing model. This is usually where a data steward first
-- notices that something upstream has gone wrong.
select
    date_trunc('month', cast(order_date as date)) as month,
    round(sum(revenue_usd), 2)                    as revenue_usd,
    round(sum(cogs_usd), 2)                       as cogs_usd,
    round(
        100.0 * (sum(revenue_usd) - sum(cogs_usd)) / nullif(sum(revenue_usd), 0), 2
    ) as gross_margin_pct
from (
    select
        o.order_date,
        r.revenue_usd,
        (
            select sum(li.quantity * p.cost_usd)
            from {{ ref('stg_order_items') }} li
            join {{ ref('stg_products') }} p on p.sku = li.sku
            where li.order_id = o.order_id
        ) as cogs_usd
    from {{ ref('stg_orders') }} o
    join {{ ref('fct_order_revenue') }} r on r.order_id = o.order_id
) t
group by 1
order by 1
