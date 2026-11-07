{
    "name": "Shopify Connector Accounting",
    "summary": "Shopify Payments payouts, fees, disputes, and reconciliation",
    "version": "20.0.1.0.0",
    "category": "Accounting/Accounting",
    "author": "Sawir Studio, Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/connector-shopify",
    "license": "AGPL-3",
    "depends": ["shopify_connector", "account"],
    "data": [
        "data/queue_job.xml",
        "data/ir_cron.xml",
        "views/shopify_account_views.xml",
        "views/shopify_account_operability_views.xml",
        "security/ir.access.csv",
    ],
    "installable": True,
    "application": False,
}
