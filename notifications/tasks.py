from celery import shared_task

@shared_task(bind=True, max_retries=5, default_retry_delay=60)
def send_email_task(self, *, to, subject, text_body, html_body, from_email):
    from .services import _send_email  # reuse your existing function

    try:
        _send_email(
            to=to,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            from_email=from_email,
        )
    except Exception as e:
        raise self.retry(exc=e)
