select
    carrier,
    count(*)                                      as shipment_count,
    round(avg(transit_hours), 2)                  as avg_transit_hours,
    round(sum(weight_kg), 2)                      as total_weight_kg,
    round(sum(cost_usd), 2)                       as total_cost_usd,
    round(sum(cost_usd) / nullif(sum(weight_kg), 0), 4) as cost_per_kg
from {{ ref('stg_shipments') }}
group by 1
order by 1
