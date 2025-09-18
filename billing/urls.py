from django.urls import path
from .views import (update_subscription_payment,create_subscription_payment,fetch_parent_invoices,confirm_payement)

urlpatterns = [
    path("api/create/subscription/payment/", create_subscription_payment, name="create_subscription_payment"),
    path("api/update/subscription/<str:reference>/payment/", update_subscription_payment, name="update_subscription_payment"),

    path("api/fetch/invoices/", fetch_parent_invoices, name="fetch_parent_invoices"),


    path("api/confirm/payment/", confirm_payement, name="confirm_payement"),
]
