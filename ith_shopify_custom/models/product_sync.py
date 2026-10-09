from odoo import models


class ShopifyProductTemplateSync(models.Model):
    _inherit = "shopify.product.template"

    def _sync_images(self, template_binding, images, variants, *, prune):
        """Images remain in Shopify and are not imported into Odoo."""
        return None

    def _sync_variants(self, template_binding, variants, *, seed_all, prune):
        """Ignore Shopify barcodes while preserving normal SKU/variant sync."""
        variants_without_barcodes = [
            {**variant, "barcode": False}
            for variant in variants
        ]
        return super()._sync_variants(
            template_binding,
            variants_without_barcodes,
            seed_all=seed_all,
            prune=prune,
        )
