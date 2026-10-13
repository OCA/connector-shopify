from odoo import fields, models


class ITHShopifyLocationSupplierStock(models.Model):
    _inherit = "shopify.location"

    use_supplier_stock = fields.Boolean(
        string="Use Supplier Stock",
        default=False,
        help=(
            "Use the shared external supplier availability registry "
            "instead of physical Odoo stock for this Shopify location."
        ),
    )
