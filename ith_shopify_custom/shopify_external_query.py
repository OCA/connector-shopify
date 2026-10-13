"""Read-only Shopify GraphQL query for external supplier reconciliation."""

EXTERNAL_ORDER_QUERY = """
query ITHExternalOrder($id: ID!) {
  order(id: $id) {
    id
    cancelledAt

    fulfillmentOrders(first: 250) {
      pageInfo {
        hasNextPage
        endCursor
      }
      nodes {
        id
        status
        assignedLocation {
          location {
            id
          }
        }
        lineItems(first: 250) {
          pageInfo {
            hasNextPage
            endCursor
          }
          nodes {
            id
            totalQuantity
            remainingQuantity
            lineItem {
              id
            }
          }
        }
      }
    }

    fulfillments(first: 250) {
      id
      status
      location {
        id
      }
      fulfillmentLineItems(first: 250) {
        pageInfo {
          hasNextPage
          endCursor
        }
        nodes {
          id
          quantity
          lineItem {
            id
          }
        }
      }
    }
  }
}
"""


def validate_external_order_payload(payload):
    """Reject incomplete Shopify data before supplier stock reconciliation."""
    if not isinstance(payload, dict) or not payload.get("id"):
        raise ValueError("Missing Shopify order")

    def check_connection(connection, label):
        if not isinstance(connection, dict):
            raise ValueError(f"Missing {label}")
        page = connection.get("pageInfo")
        nodes = connection.get("nodes")
        if not isinstance(page, dict) or not isinstance(nodes, list):
            raise ValueError(f"Incomplete {label}")
        if page.get("hasNextPage") is not False:
            raise ValueError(f"Unverified pagination: {label}")
        return nodes

    orders = check_connection(
        payload.get("fulfillmentOrders"), "fulfillment orders"
    )
    for order in orders:
        if not isinstance(order, dict) or not order.get("id"):
            raise ValueError("Invalid fulfillment order")
        lines = check_connection(order.get("lineItems"), "allocation lines")
        for line in lines:
            if not isinstance(line, dict) or not (line.get("lineItem") or {}).get("id"):
                raise ValueError("Invalid allocation line")

    shipments = payload.get("fulfillments")
    if not isinstance(shipments, list) or len(shipments) >= 250:
        raise ValueError("Missing or potentially truncated shipment list")

    for shipment in shipments:
        if not isinstance(shipment, dict) or not shipment.get("id"):
            raise ValueError("Invalid shipment")
        lines = check_connection(
            shipment.get("fulfillmentLineItems"), "shipment lines"
        )
        for line in lines:
            if not isinstance(line, dict) or not (line.get("lineItem") or {}).get("id"):
                raise ValueError("Invalid shipment line")

    return True


def calculate_external_quantities(payload, external_location_ids, known_location_ids=None):
    """Calculate supplier quantities by Shopify order line."""
    validate_external_order_payload(payload)
    if known_location_ids is None:
        raise ValueError("Known Shopify locations are required")
    validate_external_locations(payload, known_location_ids)
    external_location_ids = set(external_location_ids)
    known_location_ids = set(known_location_ids)
    if not external_location_ids.issubset(known_location_ids):
        raise ValueError("External location is not registered")
    result = {}

    for fo in payload["fulfillmentOrders"]["nodes"]:
        location = ((fo.get("assignedLocation") or {}).get("location") or {})
        if location.get("id") not in external_location_ids:
            continue

        for line in fo["lineItems"]["nodes"]:
            key = line["lineItem"]["id"]
            remaining = line.get("remainingQuantity")
            total = line.get("totalQuantity")
            if (
                type(remaining) is not int
                or type(total) is not int
                or not 0 <= remaining <= total
            ):
                raise ValueError("Invalid allocation quantities")

            entry = result.setdefault(key, {"reserved": 0, "fulfilled": 0})
            entry["reserved"] += remaining

    for shipment in payload["fulfillments"]:
        if shipment.get("status") != "SUCCESS":
            continue
        location = shipment.get("location") or {}
        if location.get("id") not in external_location_ids:
            continue

        for line in shipment["fulfillmentLineItems"]["nodes"]:
            key = line["lineItem"]["id"]
            quantity = line.get("quantity")
            if type(quantity) is not int or quantity < 0:
                raise ValueError("Invalid shipped quantity")

            entry = result.setdefault(key, {"reserved": 0, "fulfilled": 0})
            entry["fulfilled"] += quantity

    for entry in result.values():
        entry["total"] = entry["reserved"] + entry["fulfilled"]

    return result



def validate_external_locations(payload, known_location_ids):
    """Reject allocations and shipments from unknown Shopify locations."""
    validate_external_order_payload(payload)
    known = set(known_location_ids)

    for fo in payload["fulfillmentOrders"]["nodes"]:
        location = ((fo.get("assignedLocation") or {}).get("location") or {})
        location_id = location.get("id")
        if location_id not in known:
            raise ValueError(f"Unknown fulfillment location: {location_id}")

    for shipment in payload["fulfillments"]:
        location = shipment.get("location") or {}
        location_id = location.get("id")
        if location_id not in known:
            raise ValueError(f"Unknown shipment location: {location_id}")

    return True
