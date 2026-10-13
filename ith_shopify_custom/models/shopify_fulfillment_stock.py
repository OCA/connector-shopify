from odoo import models
from odoo.exceptions import UserError


class ShopifyOrderExternalStock(models.Model):
    _inherit = "shopify.order"

    def _ith_external_allocation_lines(self):
        """Read Shopify allocations for supplier-backed fulfillment orders."""
        self.ensure_one()
        allocations = {}

        for fulfillment in self.fulfillment_order_ids:
            location = fulfillment.assigned_location_id

            if not location:
                if fulfillment.line_ids:
                    raise UserError(
                        self.env._(
                            "Shopify fulfillment order %s has an unknown warehouse. "
                            "External stock reconciliation was stopped.",
                            fulfillment.shopify_id,
                        )
                    )
                continue

            if not location.use_supplier_stock:
                continue

            for line in fulfillment.line_ids:
                order_line = line.order_line_binding_id.odoo_id
                if not order_line or not order_line.product_id:
                    raise UserError(
                        self.env._(
                            "Shopify External fulfillment line %s has no linked Odoo product.",
                            line.shopify_id,
                        )
                    )

                sku = order_line.product_id.default_code
                if not sku:
                    raise UserError(
                        self.env._(
                            "Shopify External fulfillment line %s has no product SKU.",
                            line.shopify_id,
                        )
                    )

                if not line.order_line_shopify_id:
                    raise UserError(
                        self.env._(
                            "External fulfillment line %s has no Shopify order line ID.",
                            line.shopify_id,
                        )
                    )

                key = f"{self.instance_id.id}:{line.order_line_shopify_id}"

                if key not in allocations:
                    allocations[key] = {
                        "sku": sku,
                        "quantity": 0,
                        "reference": self.shopify_id,
                        "source_line_key": key,
                    }
                elif allocations[key]["sku"] != sku:
                    raise UserError(
                        self.env._(
                            "Conflicting SKU for Shopify order line %s.",
                            line.order_line_shopify_id,
                        )
                    )

                if not 0 <= line.remaining_quantity <= line.total_quantity:
                    raise UserError(
                        self.env._(
                            "Invalid Shopify fulfillment quantities for line %s.",
                            line.shopify_id,
                        )
                    )

                allocations[key]["quantity"] += line.remaining_quantity

        return list(allocations.values())

    def _ith_external_fulfilled_quantities(self):
        """Return successfully shipped External quantities by Shopify order line."""
        self.ensure_one()
        quantities = {}

        fulfillments = self.env["shopify.fulfillment"].sudo().search([
            ("order_binding_id", "=", self.id),
            ("fulfillment_status", "=", "SUCCESS"),
        ])

        external_locations = set(
            self.env["shopify.location"].sudo().search([
                ("instance_id", "=", self.instance_id.id),
                ("use_supplier_stock", "=", True),
            ]).mapped("shopify_id")
        )

        for fulfillment in fulfillments:
            if fulfillment.location_shopify_id not in external_locations:
                continue

            payload = fulfillment.raw_payload or {}
            connection = payload.get("fulfillmentLineItems")

            if not isinstance(connection, dict) or not isinstance(
                connection.get("nodes"), list
            ):
                raise UserError(
                    self.env._(
                        "Successful External fulfillment %s has incomplete line data.",
                        fulfillment.shopify_id,
                    )
                )

            line_items = connection["nodes"]
            if not line_items:
                raise UserError(
                    self.env._(
                        "Successful External fulfillment %s has no shipment lines.",
                        fulfillment.shopify_id,
                    )
                )

            for line in line_items:
                order_line_id = str((line.get("lineItem") or {}).get("id") or "")
                if not order_line_id:
                    raise UserError(
                        self.env._("External fulfillment has a line without an order line ID.")
                    )

                quantity = int(line.get("quantity") or 0)
                if quantity < 0:
                    raise UserError(
                        self.env._("External fulfillment contains a negative quantity.")
                    )

                key = f"{self.instance_id.id}:{order_line_id}"
                quantities[key] = quantities.get(key, 0) + quantity

        return quantities

    def _ith_external_target_quantities(self):
        """Calculate reserved and shipped quantities without double counting."""
        self.ensure_one()

        allocations = self._ith_external_allocation_lines()
        fulfilled = self._ith_external_fulfilled_quantities()
        targets = {}

        for allocation in allocations:
            key = allocation["source_line_key"]
            shipped = fulfilled.get(key, 0)
            allocated = allocation["quantity"]

            if allocated < 0 or shipped < 0:
                raise UserError(
                    self.env._("Negative Shopify External allocation.")
                )

            targets[key] = {
                **allocation,
                "reserved_quantity": allocated,
                "fulfilled_quantity": shipped,
                "quantity": allocated + shipped,
            }

        # Completed shipments remain accounted for even when their
        # fulfillment allocation has disappeared.
        for key, shipped in fulfilled.items():
            if key in targets:
                continue

            order_line_id = key.split(":", 1)[-1]
            binding = self.line_binding_ids.filtered(
                lambda line, oid=order_line_id: line.shopify_id == oid
            )[:1]

            product = binding.odoo_id.product_id
            sku = product.default_code if product else False
            if not sku:
                raise UserError(
                    self.env._(
                        "Completed External Shopify line %s has no linked SKU.",
                        order_line_id,
                    )
                )

            targets[key] = {
                "source_line_key": key,
                "reference": self.shopify_id,
                "sku": sku,
                "quantity": shipped,
                "reserved_quantity": 0,
                "fulfilled_quantity": shipped,
            }

        # Previously deducted quantities must not be released merely
        # because Shopify no longer returns their fulfillment allocations.
        movements = self.env["ith.external.stock.movement"].sudo().search([
            ("source", "=", "shopify"),
            ("reference", "=", self.shopify_id),
        ])

        for movement in movements:
            key = movement.source_line_key
            if key in targets:
                continue

            if movement.order_quantity > 0:
                targets[key] = {
                    "source_line_key": key,
                    "reference": self.shopify_id,
                    "sku": movement.sku,
                    "quantity": movement.order_quantity,
                    "reserved_quantity": max(
                        0,
                        movement.order_quantity - movement.fulfilled_quantity,
                    ),
                    "fulfilled_quantity": movement.fulfilled_quantity,
                    "requires_review": True,
                }

        return targets

    def _ith_reconcile_external_stock(self):
        """Reconcile confirmed Shopify external allocations with supplier stock."""
        with self.env.cr.savepoint():
            self.ensure_one()
            targets = self._ith_external_target_quantities()
            Stock = self.env["ith.external.stock"].sudo()
            Movement = self.env["ith.external.stock.movement"].sudo()
            results = []

            for key, target in targets.items():
                if target.get("requires_review"):
                    raise UserError(
                        self.env._(
                            "Shopify External allocation %s disappeared; manual review required.",
                            key,
                        )
                    )

                sku = target.get("sku")
                if not sku:
                    raise UserError(
                        self.env._("External allocation %s has no SKU.", key)
                    )

                existing = Movement.search([
                    ("source", "=", "shopify"),
                    ("source_line_key", "=", key),
                ], limit=1)

                if self.cancelled_at and existing and existing.order_quantity > 0:
                    raise UserError(
                        self.env._(
                            "Shopify order %s is cancelled. Supplier stock release "
                            "requires verified fulfillment data before reconciliation.",
                            self.shopify_id,
                        )
                    )

                if existing and target["quantity"] < existing.order_quantity:
                    raise UserError(
                        self.env._(
                            "Shopify External line %s decreased from %s to %s. "
                            "Cancellation or reassignment must be confirmed "
                            "before releasing supplier stock.",
                            key,
                            existing.order_quantity,
                            target["quantity"],
                        )
                    )

                result = Stock.reconcile_order_quantity(
                    sku,
                    target["quantity"],
                    "shopify",
                    self.shopify_id,
                    key,
                )

                movement = Movement.search([
                    ("source", "=", "shopify"),
                    ("source_line_key", "=", key),
                ], limit=1)

                if movement:
                    movement.write({
                        "reserved_quantity": target["reserved_quantity"],
                        "fulfilled_quantity": target["fulfilled_quantity"],
                    })

                results.append(result)

            return results


class ShopifyExternalSnapshotReconciliation(models.Model):
    _inherit = "shopify.order"

    def _ith_reconcile_external_snapshot(self, payload):
        """Reconcile a validated Shopify snapshot; no API calls."""
        self.ensure_one()

        from ..shopify_external_query import (
            calculate_external_quantities,
        )

        Location = self.env["shopify.location"].sudo()
        locations = Location.search([
            ("instance_id", "=", self.instance_id.id),
        ])

        known_ids = set(locations.mapped("shopify_id"))
        external_ids = set(
            locations.filtered("use_supplier_stock").mapped("shopify_id")
        )

        with self.env.cr.savepoint():
            if not isinstance(payload, dict) or payload.get("id") != self.shopify_id:
                raise ValueError(
                    "Shopify snapshot order ID does not match Odoo order"
                )

            if payload.get("cancelledAt"):
                raise ValueError(
                    "Cancelled Shopify order requires verified supplier stock release"
                )

            quantities = calculate_external_quantities(
                payload, external_ids, known_ids
            )

            Stock = self.env["ith.external.stock"].sudo()
            Movement = self.env["ith.external.stock.movement"].sudo()
            results = []

            previous = Movement.search([
                ("source", "=", "shopify"),
                ("reference", "=", self.shopify_id),
            ])

            expected_keys = {
                f"{self.instance_id.id}:{line_id}"
                for line_id in quantities
            }

            for movement in previous:
                if (
                    movement.order_quantity > 0
                    and movement.source_line_key not in expected_keys
                ):
                    raise ValueError(
                        "Previously accounted Shopify External line disappeared: "
                        + movement.source_line_key
                    )

            for shopify_line_id, values in quantities.items():
                line = self.line_binding_ids.filtered(
                    lambda item, sid=shopify_line_id: item.shopify_id == sid
                )[:1]

                sku = line.odoo_id.product_id.default_code
                if not sku:
                    raise ValueError(
                        f"Missing canonical SKU for {shopify_line_id}"
                    )

                key = f"{self.instance_id.id}:{shopify_line_id}"

                existing = Movement.search([
                    ("source", "=", "shopify"),
                    ("source_line_key", "=", key),
                ], limit=1)

                if existing:
                    if values["total"] < existing.order_quantity:
                        raise ValueError(
                            "Unconfirmed Shopify External stock release: " + key
                        )
                    if values["fulfilled"] < existing.fulfilled_quantity:
                        raise ValueError(
                            "Previously shipped quantity decreased: " + key
                        )
                    if existing.sku != sku:
                        raise ValueError(
                            "Canonical SKU changed for Shopify line: " + key
                        )

                result = Stock.reconcile_order_quantity(
                    sku,
                    values["total"],
                    "shopify",
                    self.shopify_id,
                    key,
                )

                movement = self.env[
                    "ith.external.stock.movement"
                ].sudo().search([
                    ("source", "=", "shopify"),
                    ("source_line_key", "=", key),
                ], limit=1)

                movement.write({
                    "reserved_quantity": values["reserved"],
                    "fulfilled_quantity": values["fulfilled"],
                })

                results.append(result)

            return results


class ShopifyExternalSnapshotFetch(models.Model):
    _inherit = "shopify.order"

    def _ith_fetch_and_reconcile_external(self):
        """Fetch and reconcile one Shopify order's External inventory."""
        self.ensure_one()

        from ..shopify_external_query import (
            EXTERNAL_ORDER_QUERY,
        )

        if not self.instance_id.active:
            raise UserError(
                "Shopify instance is disabled; external synchronization stopped."
            )

        if not self.shopify_id:
            raise UserError("Missing Shopify order ID.")

        response = self.instance_id._shopify_client().execute(
            EXTERNAL_ORDER_QUERY,
            {"id": self.shopify_id},
        )

        payload = response.get("order")
        if not payload:
            raise UserError("Shopify returned no order.")

        return self._ith_reconcile_external_snapshot(payload)


class ShopifyExternalAutomaticSync(models.Model):
    _inherit = "shopify.order"

    def _write_success(self, binding, sale_order, order_data, digest):
        result = super()._write_success(
            binding, sale_order, order_data, digest
        )

        enabled = self.env["ir.config_parameter"].sudo().get_param(
            "ith.external.shopify.auto_sync", "False"
        )

        if (
            enabled == "True"
            and binding.instance_id.active
            and not order_data.get("is_draft", False)
            and order_data.get("source") != "pos"
        ):
            binding.with_delay(
                channel="root.shopify.external",
                description="ITH External stock synchronization",
                identity_key=(
                    f"ith.external.sync."
                    f"{binding.instance_id.id}.{binding.shopify_id}"
                ),
            )._ith_fetch_and_reconcile_external()

        return result
