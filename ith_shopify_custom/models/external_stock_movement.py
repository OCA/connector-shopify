from odoo import fields, models


class ITHExternalStockMovement(models.Model):
    _name = "ith.external.stock.movement"
    _description = "External Supplier Availability Movement"
    _order = "id desc"

    stock_id = fields.Many2one(
        "ith.external.stock",
        required=True,
        index=True,
        ondelete="restrict",
    )
    sku = fields.Char(
        related="stock_id.sku",
        store=True,
        readonly=True,
    )
    quantity_delta = fields.Float(required=True)
    fulfilled_quantity = fields.Float(
        string="Fulfilled Quantity",
        default=0.0,
        help="Quantity already shipped and not releasable by allocation changes.",
    )
    reserved_quantity = fields.Float(
        string="Reserved Quantity",
        default=0.0,
        help="Outstanding quantity allocated to external supplier stock.",
    )
    shortage_quantity = fields.Float(
        string="Unfulfilled Supplier Availability",
        default=0.0,
        help="Quantity not covered by supplier availability at reservation time.",
    )
    order_quantity = fields.Float(
        string="Latest Order Quantity",
        default=0.0,
        help="Last known quantity of this order line.",
    )
    last_applied_snapshot_version = fields.Integer(
        string="Last Applied Snapshot Version",
        default=0,
    )
    source = fields.Selection(
        [
            ("shopify", "Shopify Order"),
            ("amazon", "Amazon Order"),
            ("odoo", "Odoo Order"),
            ("manual", "Manual Adjustment"),
        ],
        required=True,
    )
    reference = fields.Char(required=True, index=True)
    source_line_key = fields.Char(required=True, index=True)
    snapshot_version = fields.Integer(required=True, default=0)
    applied = fields.Boolean(default=False, readonly=True)

    _unique_source_line = models.Constraint(
        "UNIQUE(source, source_line_key)",
        "This external stock movement was already registered.",
    )
