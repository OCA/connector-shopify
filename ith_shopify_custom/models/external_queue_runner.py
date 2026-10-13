from odoo import api, models


class ITHExternalQueueRunner(models.AbstractModel):
    _name = "ith.external.queue.runner"
    _description = "ITH External Stock Queue Runner"

    @api.model
    def run_pending_external_jobs(self):
        """Run at most one verified External job per cron transaction."""
        import traceback
        from odoo.addons.queue_job.job import Job
        from odoo.addons.queue_job.exception import RetryableJobError

        enabled = self.env["ir.config_parameter"].sudo().get_param(
            "ith.external.queue.enabled", "False"
        )
        if enabled != "True":
            return False

        self.env.flush_all()
        self.env.cr.execute("""
            SELECT id FROM queue_job
            WHERE state = 'pending'
              AND channel = 'root.shopify.external'
              AND (eta IS NULL OR eta <= (now() AT TIME ZONE 'UTC'))
              AND (
                (model_name = 'shopify.order'
                 AND method_name = '_ith_fetch_and_reconcile_external'
                 AND identity_key LIKE 'ith.external.sync.1.%')
                OR
                (model_name = 'shopify.product.variant'
                 AND method_name = '_job_push_inventory_level'
                 AND identity_key LIKE 'ith.external.inventory.1.%')
              )
            ORDER BY priority, date_created, id
            LIMIT 1 FOR NO KEY UPDATE SKIP LOCKED
        """)

        row = self.env.cr.fetchone()
        if not row:
            return False

        record = self.env["queue.job"].sudo().browse(row[0])
        if not self.validate_external_job(record):
            return False

        job = Job.load(self.env, record.uuid)

        try:
            with self.env.cr.savepoint():
                job.set_started()
                job.store()
                job.perform()
                job.set_done()
                job.store()
        except RetryableJobError as exc:
            job.postpone(result=str(exc), seconds=30)
            job.set_pending(reset_retry=False)
            job.store()
        except Exception:
            job.set_failed(exc_info=traceback.format_exc())
            job.store()

        return True

    @api.model
    def get_pending_external_jobs(self):
        """Return only jobs from the dedicated External channel."""
        return self.env["queue.job"].sudo().search([
            ("channel", "=", "root.shopify.external"),
            ("state", "=", "pending"),
            ("model_name", "in", [
                "shopify.order",
                "shopify.product.variant",
            ]),
            ("method_name", "in", [
                "_ith_fetch_and_reconcile_external",
                "_job_push_inventory_level",
            ]),
        ], order="id", limit=1)

    @api.model
    def validate_external_job(self, job_record):
        """Validate the exact Shopify instance and external location."""
        from odoo.addons.queue_job.job import Job

        assert len(job_record) == 1
        job = Job.load(self.env, job_record.uuid)

        instance = self.env["shopify.instance"].sudo().with_context(
            active_test=False
        ).browse(1)

        if not instance.exists() or instance.shop_url != "uitpac-aj.myshopify.com":
            return False

        if not instance.active:
            return False

        if job.channel != "root.shopify.external":
            return False

        key = job.identity_key or ""
        if job.method_name == "_ith_fetch_and_reconcile_external":
            if not key.startswith("ith.external.sync.1."):
                return False
        elif job.method_name == "_job_push_inventory_level":
            if not key.startswith("ith.external.inventory.1."):
                return False
        else:
            return False

        if job_record.state != "pending":
            return False

        if job.recordset.instance_id.id != instance.id:
            return False

        if job.method_name == "_ith_fetch_and_reconcile_external":
            return job.recordset._name == "shopify.order"

        if job.method_name == "_job_push_inventory_level":
            if job.recordset._name != "shopify.product.variant":
                return False
            if len(job.args) != 1:
                return False
            location = self.env["shopify.location"].sudo().browse(job.args[0])
            expected_key = (
                f"ith.external.inventory.{instance.id}."
                f"{job.recordset.id}.{location.id}"
            )
            return bool(
                location.exists()
                and location.instance_id.id == instance.id
                and location.name == "Shop Location External"
                and location.use_supplier_stock
                and key == expected_key
            )

        return False
