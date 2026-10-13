import base64

from odoo import fields, models
from odoo.exceptions import UserError


class ITHExternalStockImport(models.TransientModel):
    _name = "ith.external.stock.import"
    _description = "Import Supplier Stock Excel"

    file = fields.Binary(
        string="Supplier Excel File",
        required=True,
        attachment=False,
    )
    filename = fields.Char(string="Filename")
    complete_snapshot = fields.Boolean(
        string="Zero missing SKUs (full snapshot)",
        required=True,
    )

    def action_import(self):
        self.ensure_one()

        if not self.env.user.has_group("stock.group_stock_manager"):
            raise UserError(
                self.env._("Only inventory managers can import supplier stock.")
            )

        if not (self.filename or "").lower().endswith(".xlsx"):
            raise UserError(self.env._("Please select an .xlsx Excel file."))

        try:
            content = base64.b64decode(self.file, validate=True)
        except (ValueError, base64.binascii.Error):
            raise UserError(self.env._("Invalid Excel file data."))

        result = self.env["ith.external.stock"].sudo().import_supplier_xlsx(
            content, zero_missing=self.complete_snapshot
        )

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": self.env._("Supplier Stock Imported"),
                "message": self.env._(
                    "%(imported)s SKUs processed; %(zeroed)s missing SKUs zeroed.",
                    imported=result["imported"],
                    zeroed=result["zeroed"],
                ),
                "type": "success",
                "sticky": True,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
