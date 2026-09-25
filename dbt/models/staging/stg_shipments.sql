select
    shipment_id,
    order_id,
    carrier,
    cast(dispatch_ts as timestamp) as dispatch_ts,
    cast(delivery_ts as timestamp) as delivery_ts,
    date_diff('hour', cast(dispatch_ts as timestamp), cast(delivery_ts as timestamp))
        as transit_hours,
    weight_kg,
    cost_usd
from raw_shipments
