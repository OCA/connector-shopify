from unittest.mock import MagicMock, patch

from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, new_test_user

from ..lib.fulfillment import (
    FulfillmentAllocationError,
    allocate_fulfillment_lines,
)


class TestShopifyFulfillmentSync(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.instance = cls.env["shopify.instance"].create(
            {
                "name": "Fulfillment Shop",
                "shop_url": "fulfillment-shop.myshopify.com",
                "access_token": "test-token",
                "webhook_secret": "test-secret",
            }
        )
        cls.instance.state = "connected"
        cls.product = cls.env["product.product"].create(
            {
                "name": "Fulfilled Widget",
                "type": "consu",
                "is_storable": True,
                "company_id": cls.instance.company_id.id,
            }
        )
        template_binding = cls.env["shopify.product.template"].create(
            {
                "instance_id": cls.instance.id,
                "shopify_id": "gid://shopify/Product/600",
                "odoo_id": cls.product.product_tmpl_id.id,
                "status": "ACTIVE",
                "state": "synced",
            }
        )
        cls.variant_binding = cls.env["shopify.product.variant"].create(
            {
                "instance_id": cls.instance.id,
                "shopify_id": "gid://shopify/ProductVariant/601",
                "template_binding_id": template_binding.id,
                "odoo_id": cls.product.id,
                "state": "synced",
            }
        )
        cls.warehouse = cls.env["stock.warehouse"].search(
            [("company_id", "=", cls.instance.company_id.id)],
            limit=1,
        )
        cls.customer = cls.env["res.partner"].create(
            {
                "name": "Fulfillment Customer",
                "company_id": cls.instance.company_id.id,
            }
        )
        cls.sale = cls.env["sale.order"].create(
            {
                "partner_id": cls.customer.id,
                "company_id": cls.instance.company_id.id,
                "warehouse_id": cls.warehouse.id,
                "order_line": [
                    (
                        0,
                        0,
                        {
                            "product_id": cls.product.id,
                            "product_uom_qty": 1,
                            "price_unit": 10,
                        },
                    )
                ],
            }
        )
        cls.order_binding = cls.env["shopify.order"].create(
            {
                "instance_id": cls.instance.id,
                "shopify_id": "gid://shopify/Order/610",
                "odoo_id": cls.sale.id,
                "order_name": "#610",
                "state": "synced",
            }
        )
        cls.line_binding = cls.env["shopify.order.line"].create(
            {
                "instance_id": cls.instance.id,
                "shopify_id": "gid://shopify/LineItem/611",
                "order_binding_id": cls.order_binding.id,
                "odoo_id": cls.sale.order_line.id,
                "variant_shopify_id": cls.variant_binding.shopify_id,
                "state": "synced",
            }
        )

    def test_picking_validation_enqueues_fulfillment_job(self):
        self.env["stock.quant"]._update_available_quantity(
            self.product, self.warehouse.lot_stock_id, 1
        )
        self.sale.action_confirm()
        picking = self.sale.picking_ids
        picking.action_assign()
        picking.move_ids.quantity = 1

        with (
            patch.object(
                type(self.order_binding),
                "with_delay",
                return_value=self.order_binding,
            ) as with_delay,
            patch.object(
                type(self.order_binding),
                "_job_push_picking_fulfillment",
                return_value=True,
            ) as push,
        ):
            picking.button_validate()

        self.assertEqual(picking.state, "done")
        with_delay.assert_called_once()
        self.assertEqual(
            with_delay.call_args.kwargs["identity_key"],
            (
                f"shopify.fulfillment.push.{self.instance.id}."
                f"{self.order_binding.id}.{picking.id}"
            ),
        )
        push.assert_called_once_with(picking.id)

    def test_stock_user_can_validate_picking_of_shopify_order(self):
        stock_user = new_test_user(
            self.env,
            login="shopify_validate_stock_user",
            groups="stock.group_stock_user",
        )
        self.env["stock.quant"]._update_available_quantity(
            self.product, self.warehouse.lot_stock_id, 1
        )
        self.sale.action_confirm()
        picking = self.sale.picking_ids
        picking.action_assign()
        picking.move_ids.quantity = 1
        # A request starts with a cold cache: the order bindings must be fetched
        # with the user's access rights, which do not cover shopify.order.
        self.env.invalidate_all()

        with (
            patch.object(
                type(self.order_binding),
                "with_delay",
                return_value=self.order_binding,
            ) as with_delay,
            patch.object(
                type(self.order_binding),
                "_job_push_picking_fulfillment",
                return_value=True,
            ) as push,
        ):
            picking.with_user(stock_user).button_validate()

        self.assertEqual(picking.state, "done")
        with_delay.assert_called_once()
        push.assert_called_once_with(picking.id)
        self.env.invalidate_all()
        with self.assertRaises(AccessError):
            self.sale.with_user(stock_user).shopify_binding_ids.mapped("shopify_id")

    def test_partial_allocation_uses_only_delivered_quantity(self):
        allocation = allocate_fulfillment_lines(
            [
                {
                    "move_id": 1,
                    "line_id": self.line_binding.shopify_id,
                    "quantity": 2,
                }
            ],
            [
                {
                    "id": "gid://shopify/FulfillmentOrder/620",
                    "location_id": "gid://shopify/Location/1",
                    "status": "OPEN",
                    "line_items": [
                        {
                            "id": "gid://shopify/FulfillmentOrderLineItem/621",
                            "line_id": self.line_binding.shopify_id,
                            "remaining_quantity": 5,
                        }
                    ],
                }
            ],
            target_location_id="gid://shopify/Location/1",
        )

        self.assertEqual(allocation["groups"][0]["line_items"][0]["quantity"], 2)

    def test_location_mismatch_without_move_action_is_clear(self):
        with self.assertRaisesRegex(FulfillmentAllocationError, "cannot be fulfilled"):
            allocate_fulfillment_lines(
                [
                    {
                        "move_id": 1,
                        "line_id": self.line_binding.shopify_id,
                        "quantity": 1,
                    }
                ],
                [
                    {
                        "id": "gid://shopify/FulfillmentOrder/630",
                        "location_id": "gid://shopify/Location/2",
                        "status": "OPEN",
                        "supported_actions": [],
                        "line_items": [
                            {
                                "id": ("gid://shopify/FulfillmentOrderLineItem/631"),
                                "line_id": self.line_binding.shopify_id,
                                "remaining_quantity": 1,
                            }
                        ],
                    }
                ],
                target_location_id="gid://shopify/Location/1",
            )

    def test_connector_fulfillment_webhook_is_echo_guarded(self):
        fulfillment = self.env["shopify.fulfillment"].create(
            {
                "instance_id": self.instance.id,
                "shopify_id": "gid://shopify/Fulfillment/640",
                "order_binding_id": self.order_binding.id,
                "origin": "odoo",
                "state": "synced",
            }
        )
        client = MagicMock()
        client.execute.return_value = {
            "fulfillment": {
                "id": fulfillment.shopify_id,
                "status": "SUCCESS",
                "order": {"id": self.order_binding.shopify_id},
                "location": {"id": "gid://shopify/Location/1"},
                "trackingInfo": {},
                "fulfillmentLineItems": {"nodes": []},
            }
        }

        with (
            patch.object(
                type(self.instance),
                "_shopify_client",
                return_value=client,
            ),
            patch.object(
                type(fulfillment),
                "_auto_validate_external",
            ) as auto_validate,
        ):
            result = self.env["shopify.fulfillment"]._job_import_from_shopify(
                self.instance.id,
                fulfillment.shopify_id,
                {"order_id": 610, "status": "success"},
            )

        self.assertEqual(result, fulfillment.id)
        self.assertEqual(fulfillment.fulfillment_status, "SUCCESS")
        auto_validate.assert_not_called()

    # A fulfillment lowers Shopify's on-hand stock without an inventory
    # webhook, and Odoo takes the same units out with the delivery. The
    # recorded on-hand stock follows, so the next webhook does not apply them.

    def _recorded_stock(self, on_hand):
        location = self.env["shopify.location"].create(
            {
                "instance_id": self.instance.id,
                "shopify_id": "gid://shopify/Location/1",
                "name": "Fulfillment Location",
                "active": True,
                "state": "synced",
                "warehouse_id": self.warehouse.id,
                "odoo_location_id": self.warehouse.lot_stock_id.id,
            }
        )
        return self.env["shopify.inventory.state"].create(
            {
                "instance_id": self.instance.id,
                "variant_binding_id": self.variant_binding.id,
                "location_binding_id": location.id,
                "last_shopify_on_hand": on_hand,
                "shopify_snapshot_at": self.env.cr.now(),
            }
        )

    def test_a_pushed_fulfillment_is_taken_out_of_the_recorded_stock(self):
        state = self._recorded_stock(on_hand=5)
        fulfillment_order = self.env["shopify.fulfillment.order"].create(
            {
                "instance_id": self.instance.id,
                "shopify_id": "gid://shopify/FulfillmentOrder/650",
                "order_binding_id": self.order_binding.id,
                "assigned_location_shopify_id": "gid://shopify/Location/1",
                "fulfillment_status": "OPEN",
                "state": "synced",
            }
        )
        self.env["shopify.fulfillment.order.line"].create(
            {
                "fulfillment_order_id": fulfillment_order.id,
                "shopify_id": "gid://shopify/FulfillmentOrderLineItem/651",
                "order_line_binding_id": self.line_binding.id,
                "order_line_shopify_id": self.line_binding.shopify_id,
                "total_quantity": 1,
                "remaining_quantity": 1,
            }
        )
        self.env["stock.quant"]._update_available_quantity(
            self.product, self.warehouse.lot_stock_id, 1
        )
        self.sale.action_confirm()
        picking = self.sale.picking_ids
        picking.action_assign()
        picking.move_ids.quantity = 1
        with patch.object(
            type(self.order_binding), "with_delay", return_value=MagicMock()
        ):
            picking.button_validate()
        client = MagicMock()
        client.execute.return_value = {
            "fulfillmentCreate": {
                "fulfillment": {
                    "id": "gid://shopify/Fulfillment/660",
                    "status": "SUCCESS",
                    "location": {"id": "gid://shopify/Location/1"},
                    "trackingInfo": [],
                },
                "userErrors": [],
            }
        }

        with (
            patch.object(type(self.instance), "_shopify_client", return_value=client),
            patch.object(type(self.order_binding), "_job_refresh_fulfillment_orders"),
        ):
            self.order_binding._job_push_picking_fulfillment(picking.id)

        self.assertEqual(state.last_shopify_on_hand, 4)

    def test_an_external_fulfillment_is_taken_out_of_the_recorded_stock(self):
        state = self._recorded_stock(on_hand=5)
        client = MagicMock()
        client.execute.return_value = {
            "fulfillment": {
                "id": "gid://shopify/Fulfillment/670",
                "status": "SUCCESS",
                "order": {"id": self.order_binding.shopify_id},
                "location": {"id": "gid://shopify/Location/1"},
                "trackingInfo": [],
                "fulfillmentLineItems": {
                    "nodes": [
                        {
                            "id": "gid://shopify/FulfillmentLineItem/671",
                            "quantity": 1,
                            "lineItem": {"id": self.line_binding.shopify_id},
                        }
                    ]
                },
            }
        }

        with (
            patch.object(type(self.instance), "_shopify_client", return_value=client),
            patch.object(
                type(self.env["shopify.fulfillment"]), "_auto_validate_external"
            ),
        ):
            for _update in range(2):
                self.env["shopify.fulfillment"]._job_import_from_shopify(
                    self.instance.id,
                    "gid://shopify/Fulfillment/670",
                    {"order_id": 610, "status": "success"},
                )

        # A later update of the same fulfillment does not take it out again.
        self.assertEqual(state.last_shopify_on_hand, 4)

    def test_stock_user_can_cancel_picking_with_fulfillment(self):
        stock_user = new_test_user(
            self.env,
            login="shopify_cancel_stock_user",
            groups="stock.group_stock_user",
        )
        self.sale.action_confirm()
        picking = self.sale.picking_ids
        fulfillment = self.env["shopify.fulfillment"].create(
            {
                "instance_id": self.instance.id,
                "order_binding_id": self.order_binding.id,
                "picking_id": picking.id,
                "shopify_id": "gid://shopify/Fulfillment/620",
            }
        )
        # A request starts with a cold cache: the fulfillments must be fetched
        # with the user's access rights, which do not cover shopify.fulfillment.
        self.env.invalidate_all()

        picking.with_user(stock_user).action_cancel()

        self.assertEqual(picking.state, "cancel")
        log = self.env["shopify.log"].search(
            [
                ("res_model", "=", "shopify.fulfillment"),
                ("res_id", "=", fulfillment.id),
                ("level", "=", "warning"),
            ]
        )
        self.assertEqual(len(log), 1)
        self.assertIn(picking.name, log.message)
        self.env.invalidate_all()
        with self.assertRaises(AccessError):
            picking.with_user(stock_user).shopify_fulfillment_ids.mapped("shopify_id")
