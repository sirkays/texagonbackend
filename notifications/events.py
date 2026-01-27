# texagon_academy\texagonbackend\notifications\events.py
from .messages import MessageSpec
from .models import Notification

SYSTEM_WELCOME = MessageSpec(
    kind=Notification.Kind.SYSTEM,
    title_template="Welcome to {{ app_name }}",
    body_template="Hi {{ user.first_name|default:user.email }}, welcome aboard!",
    email_subject_template="Welcome to {{ app_name }} 🎉",
    email_html_template="emails/welcome.html",   # optional
    email_text_template="emails/welcome.txt",    # optional
    default_data={"cta": {"label": "Open dashboard", "url": "/dashboard"}},
)

PAYMENT_RECEIVED = MessageSpec(
    kind=Notification.Kind.PAYMENT,
    title_template="Payment received",
    body_template="We received your payment of ₦{{ data.amount }} for {{ data.item_name }}.",
    email_subject_template="Payment receipt: ₦{{ data.amount }}",
    email_html_template="emails/payment_receipt.html",
    email_text_template="emails/payment_receipt.txt",
)



PAYMENT_CONFIRMED = MessageSpec(
    kind=Notification.Kind.PAYMENT,
    title_template="Payment confirmed",
    body_template="Your payment of ₦{{ data.amount }} for invoice {{ data.invoice_number }} was successful.",
    email_subject_template="Payment confirmed: ₦{{ data.amount }}",
    email_html_template="emails/payment_receipt.html",
    email_text_template="emails/payment_receipt.txt",
    default_data={
        "cta": {"label": "View receipt", "url": ""},  # url will be filled at runtime
    },
)

COURSE_ENROLLED = MessageSpec(
    kind=Notification.Kind.COURSE,
    title_template="Enrolled: {{ data.course_title }}",
    body_template="You're now enrolled in {{ data.course_title }}. Start learning anytime.",
    email_subject_template="You're enrolled in {{ data.course_title }}",
)

INVOICE_GENERATED_PARENT = MessageSpec(
    kind=Notification.Kind.PAYMENT,
    title_template="New invoice generated",
    body_template="An invoice of ₦{{ data.amount }} has been generated for {{ data.student_name }}. Due: {{ data.due_at }}.",
    email_subject_template="New invoice: ₦{{ data.amount }} (Due {{ data.due_at }})",
    email_html_template="emails/invoice_generated.html",
    email_text_template="emails/invoice_generated.txt",
)



ORDER_CREATED = MessageSpec(
    kind=Notification.Kind.PAYMENT,
    title_template="Order created",
    body_template="Your order {{ data.order_id }} has been created. Total: {{ data.currency }} {{ data.grand_total }}.",
    email_subject_template="Order created: {{ data.currency }} {{ data.grand_total }}",
    email_html_template="emails/order_created.html",
    email_text_template="emails/order_created.txt",
)
