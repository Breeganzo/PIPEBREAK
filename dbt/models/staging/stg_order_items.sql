select
    item_id,
    order_id,
    sku,
    quantity,
    unit_price,
    coalesce(discount_pct, 0)                            as discount_pct,
    quantity * unit_price * (1 - coalesce(discount_pct, 0)) as line_amount_local
from raw_order_items
