from django.contrib import admin
from .models import SubscriptionPlan, OrganizationSubscription, SubscriptionInvoice, SubscriptionPayment

@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ("name", "price", "billing_period", "student_limit", "created_at")
    list_filter = ("billing_period",)
    search_fields = ("name",)

class SubscriptionPaymentInline(admin.TabularInline):
    model = SubscriptionPayment
    extra = 0

@admin.register(OrganizationSubscription)
class OrganizationSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("organization", "plan", "status", "start_date", "end_date", "auto_renew", "created_at")
    list_filter = ("organization", "plan", "status", "auto_renew")
    search_fields = ("organization__name", "plan__name")
    autocomplete_fields = ("organization", "plan")

@admin.register(SubscriptionInvoice)
class SubscriptionInvoiceAdmin(admin.ModelAdmin):
    list_display = ("number", "subscription", "amount", "currency", "issued_at", "due_at", "status", "created_at")
    list_filter = ("currency", "status", "issued_at")
    search_fields = ("number", "subscription__organization__name", "subscription__plan__name")
    autocomplete_fields = ("subscription",)
    inlines = [SubscriptionPaymentInline]

@admin.register(SubscriptionPayment)
class SubscriptionPaymentAdmin(admin.ModelAdmin):
    list_display = ("invoice", "reference", "amount", "currency", "method", "status", "paid_at", "created_at")
    list_filter = ("currency", "status", "method", "paid_at")
    search_fields = ("reference", "invoice__number")
    autocomplete_fields = ("invoice",)
