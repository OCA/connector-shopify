from odoo import models


class ShopifyProductTemplate(models.Model):
    _inherit = "shopify.product.template"

    def _product_set_input(
        self,
        instance,
        template,
        binding,
        *,
        preserved_file_ids=(),
        existing_product=None,
        recreate=False,
    ):
        product_input, file_targets = super()._product_set_input(
            instance,
            template,
            binding,
            preserved_file_ids=preserved_file_ids,
            existing_product=existing_product,
            recreate=recreate,
        )

        # ITH policy:
        # Shopify owns ALL product media.
        # Odoo must never send product files/images to Shopify.
        product_input.pop("files", None)

        # ProductSet can also attach a file directly to a variant.
        # Remove that too.
        for variant in product_input.get("variants", []):
            variant.pop("file", None)

        # No exported image targets should be processed later.
        return product_input, []

    def _update_export_bindings(self, *args, **kwargs):
        binding, append_media, detach_media = super()._update_export_bindings(
            *args, **kwargs
        )

        # ITH policy:
        # Never attach or detach Shopify variant media from Odoo.
        return binding, [], []


class ProductImage(models.Model):
    _inherit = "product.image"

    def _queue_shopify_image_exports(self):
        # Changes to Odoo product images must never trigger Shopify exports.
        return

    def unlink(self):
        # The base connector has separate export logic inside unlink(),
        # therefore force skip_shopify_export in the context.
        return super(
            ProductImage,
            self.with_context(skip_shopify_export=True),
        ).unlink()
