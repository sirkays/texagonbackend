from celery import shared_task
from django.utils import timezone

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def generate_invoices_for_org(self, org_id: int):
    from billing.services.subscription_invoicing import generate_parent_children_subscription_invoices

    try:
        generate_parent_children_subscription_invoices(
            org_id=org_id,
            now=timezone.now(),
            dry_run=False,
        )
    except Exception as e:
        raise self.retry(exc=e)
