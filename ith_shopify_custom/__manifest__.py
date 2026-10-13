{
    "name": "ITH Shopify Custom",
    "version": "19.0.1.0.0",
    "summary": "Ireland Tackle Hub custom Shopify connector behaviour",
    "license": "AGPL-3",
    "depends": ["shopify_connector"],
    "data": [
        "data/external_queue.xml",
        "data/external_queue_cron.xml",
        "views/external_stock_import_views.xml",
        "security/ir.model.access.csv",],
    "installable": True,
    "application": False,
}
