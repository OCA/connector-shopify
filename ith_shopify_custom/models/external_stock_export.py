from odoo import api, models


class ITHExternalStockExport(models.Model):
    _inherit = "ith.external.stock"

    def _enqueue_external_inventory(self):
        locations = self.env["shopify.location"].sudo().search([
            ("use_supplier_stock", "=", True),
            ("active", "=", True),
            ("instance_id.active", "=", True),
        ])

        Variant = self.env["shopify.product.variant"].sudo()

        for stock in self:
            for location in locations:
                variants = Variant.search([
                    ("sku_snapshot", "=", stock.sku),
                    ("instance_id", "=", location.instance_id.id),
                    ("state", "=", "synced"),
                    ("inventory_sync_enabled", "=", True),
                    ("template_binding_id.inventory_sync_enabled", "=", True),
                    ("inventory_tracked", "=", True),
                    ("inventory_item_shopify_id", "!=", False),
                ])

                for variant in variants:
                    if not variant._is_storable_inventory_product():
                        continue

                    variant.with_delay(
                        channel="root.shopify.external",
                        description="ITH External inventory update",
                        identity_key=(
                            f"ith.external.inventory."
                            f"{location.instance_id.id}."
                            f"{variant.id}.{location.id}"
                        ),
                    )._job_push_inventory_level(location.id)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._enqueue_external_inventory()
        return records

    def write(self, vals):
        result = super().write(vals)
        if "quantity" in vals:
            self._enqueue_external_inventory()
        return result
