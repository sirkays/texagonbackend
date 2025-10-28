from django.urls import path
from .views import (update_subscription_payment,create_subscription_payment,fetch_parent_invoices,confirm_payement,
transactions_list,
    create_complaint,
    list_complaints,
    get_complaint,
    add_complaint_response,
    update_complaint,
    delete_complaint_attachment,
    add_complaint_attachments
)

urlpatterns = [
    path("api/create/subscription/payment/", create_subscription_payment, name="create_subscription_payment"),
    path("api/update/subscription/<str:reference>/payment/", update_subscription_payment, name="update_subscription_payment"),

    path("api/fetch/invoices/", fetch_parent_invoices, name="fetch_parent_invoices"),


    path("api/confirm/payment/", confirm_payement, name="confirm_payement"),

    path("api/transactions-list/", transactions_list, name="transactions_list"),


    path("api/complaints/", create_complaint, name="complaints-create"),                      # POST
    path("api/complaints/list/", list_complaints, name="complaints-list"),                   # GET
    path("api/complaints/<uuid:complaint_id>/", get_complaint, name="complaints-detail"),    # GET
    path("api/complaints/<uuid:complaint_id>/responses/", add_complaint_response, name="complaints-add-response"),  # POST
    path("api/complaints/<uuid:complaint_id>/update/", update_complaint, name="complaints-update"),                 # PATCH
    path("api/complaints/<uuid:complaint_id>/attachments/", add_complaint_attachments, name="complaints-add-attachments"),  # POST (multipart)

    path("api/complaints/<uuid:complaint_id>/attachments/<uuid:attachment_id>/", delete_complaint_attachment, 
    name="delete-complaint-attachment"),
    
    # urls.py

]


