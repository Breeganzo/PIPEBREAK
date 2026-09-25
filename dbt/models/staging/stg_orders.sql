select
    order_id,
    customer_id,
    cast(order_ts as timestamp)   as order_ts,
    cast(order_ts as date)        as order_date,
    status,
    currency,
    ship_country,
    source_system
from raw_orders
