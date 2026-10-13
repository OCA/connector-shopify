from odoo import models


class ITHShopifyVariantSupplierInventory(models.Model):
    _inherit = "shopify.product.variant"

    def _odoo_inventory_quantity(self, location_binding, *, apply_export_policy=True):
        self.ensure_one()

        if not location_binding.use_supplier_stock:
            return super()._odoo_inventory_quantity(
                location_binding,
                apply_export_policy=apply_export_policy,
            )

        # Keep the canonical stock-product and SKU safety checks.
        if (
            not self.stock_product_id
            or not self.sku_snapshot
            or self.stock_product_id.default_code != self.sku_snapshot
        ):
            from odoo.exceptions import UserError
            raise UserError(
                self.env._(
                    "Supplier inventory export blocked: missing or mismatched "
                    "canonical stock product for Shopify variant %s.",
                    self.shopify_id,
                )
            )

        record = self.env["ith.external.stock"].sudo().search([
            ("sku", "=", self.sku_snapshot)
        ], limit=1)

        return int(record.quantity) if record else 0
