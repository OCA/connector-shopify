import logging

from odoo import models

from odoo.addons.shopify_connector.lib.client import ShopifyUserError

_logger = logging.getLogger(__name__)


class ShopifyInstance(models.Model):
    _inherit = "shopify.instance"

    def _queue_draft_orders(self, date_from=False, date_to=False):
        try:
            return super()._queue_draft_orders(date_from, date_to)
        except ShopifyUserError as exc:
            message = str(exc)

            if "Access denied for draftOrders field" in message:
                _logger.warning(
                    "Skipping Shopify draft orders for instance %s: %s",
                    self.display_name,
                    message,
                )
                return 0

            raise
