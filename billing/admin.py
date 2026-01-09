# admin.py
from django.contrib import admin, messages
from django.db.models import Q
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from .models import (
    SubscriptionPlan,
    OrganizationSubscription,
    SubscriptionInvoice,
    SubscriptionPayment,
    Complaint,
    ComplaintResponse,
    ComplaintAttachment,
    InvoiceType
)

# ---------- Helpers ----------
def _admin_change_url(obj):
    """Return the admin 'change' URL for any model instance, or None if obj is falsy."""
    if not obj:
        return None
    opts = obj._meta
    return reverse(f"admin:{opts.app_label}_{opts.model_name}_change", args=[obj.pk])


# ---------- Subscription / Billing ----------

@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ("name", "price", "billing_period", "student_limit", "created_at")
    list_filter = ("billing_period",)
    search_fields = ("name",)
    readonly_fields = ("created_at", "updated_at")
    # If NamedModel/TimeStampedModel expose these:
    # fall back gracefully if they don't exist
    def get_readonly_fields(self, request, obj=None):
        ro = list(super().get_readonly_fields(request, obj))
        for f in ("created_at", "updated_at"):
            if f not in [fld.name for fld in self.model._meta.get_fields()]:
                try:
                    ro.remove(f)
                except ValueError:
                    pass
        return ro


class SubscriptionPaymentInline(admin.TabularInline):
    model = SubscriptionPayment
    extra = 0
    fields = ("provider", "reference", "amount", "currency", "method", "status", "paid_at")
    readonly_fields = ("paid_at",)
    autocomplete_fields = ("invoice",)


@admin.register(OrganizationSubscription)
class OrganizationSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("organization", "plan", "status", "start_date", "end_date", "auto_renew", "created_at")
    list_filter = ("organization", "plan", "status", "auto_renew")
    search_fields = ("organization__name", "plan__name")
    autocomplete_fields = ("organization", "plan")
    list_select_related = ("organization", "plan")


@admin.register(SubscriptionInvoice)
class SubscriptionInvoiceAdmin(admin.ModelAdmin):
    list_display = (
        "organization_membership",
        "number",
        "subscription",
        "amount",
        "currency",
        "issued_at",
        "due_at",
        "status",
        "transaction_id",
        "created_at",
    )
    list_filter = ("currency", "status", "issued_at", "due_at")
    search_fields = (
        "number",
        "subscription__organization__name",
        "subscription__plan__name",
        "transaction_id",
    )
    autocomplete_fields = ("subscription", "organization_membership")
    inlines = [SubscriptionPaymentInline]
    list_select_related = ("subscription", "subscription__organization", "subscription__plan", "organization_membership")


@admin.register(SubscriptionPayment)
class SubscriptionPaymentAdmin(admin.ModelAdmin):
    list_display = ("invoice", "provider", "reference", "amount", "currency", "method", "status", "paid_at", "transaction_id", "created_at")
    list_filter = ("provider", "currency", "status", "method", "paid_at")
    search_fields = ("reference", "invoice__number", "transaction_id")
    autocomplete_fields = ("invoice",)
    list_select_related = ("invoice",)


# ---------- Complaints ----------

class ComplaintAttachmentInline(admin.TabularInline):
    model = ComplaintAttachment
    extra = 0
    fields = ("file", "original_name", "content_type", "uploaded_by", "uploaded_at")
    readonly_fields = ("uploaded_at",)
    autocomplete_fields = ("uploaded_by",)


class ComplaintResponseInline(admin.StackedInline):
    model = ComplaintResponse
    extra = 0
    fields = ("author", "author_name", "role", "message", "created_at")
    readonly_fields = ("created_at",)
    autocomplete_fields = ("author",)


class HasTransactionFilter(admin.SimpleListFilter):
    title = _("Has linked transaction")
    parameter_name = "has_txn"

    def lookups(self, request, model_admin):
        return (
            ("yes", _("Yes")),
            ("no", _("No")),
            ("payment", _("One-off Payment")),
            ("subscription", _("Subscription Payment")),
        )

    def queryset(self, request, queryset):
        val = self.value()
        if val == "yes":
            return queryset.filter(Q(payment__isnull=False) | Q(subscription_payment__isnull=False))
        if val == "no":
            return queryset.filter(payment__isnull=True, subscription_payment__isnull=True)
        if val == "payment":
            return queryset.filter(payment__isnull=False, subscription_payment__isnull=True)
        if val == "subscription":
            return queryset.filter(payment__isnull=True, subscription_payment__isnull=False)
        return queryset


@admin.register(Complaint)
class ComplaintAdmin(admin.ModelAdmin):
    date_hierarchy = "created_at"
    list_display = (
        "code",
        "title",
        "status",
        "priority",
        "transaction_link",
        "responses_count",
        "created_by",
        "assigned_to",
        "created_at",
        "updated_at",
    )
    list_filter = ("status", "priority", HasTransactionFilter, "created_at", "assigned_to", "created_by")
    search_fields = (
        "code",
        "title",
        "description",
        "author_name",  # from responses, but useful if you register responses admin separately
        "payment__provider_ref",
        "subscription_payment__reference",
    )
    readonly_fields = ("id", "code", "created_at", "updated_at", "responses_count", "transaction_identifier")
    autocomplete_fields = ("created_by", "assigned_to", "payment", "subscription_payment")
    inlines = [ComplaintAttachmentInline, ComplaintResponseInline]
    list_select_related = ("created_by", "assigned_to", "payment", "subscription_payment")

    # Nicely linked transaction field
    def transaction_link(self, obj: Complaint):
        ref = obj.transaction_identifier
        if not ref:
            return "—"
        if obj.payment:
            url = _admin_change_url(obj.payment)
            label = _("Payment")
        else:
            url = _admin_change_url(obj.subscription_payment)
            label = _("Subscription")
        if url:
            return format_html('<a href="{}">{}</a> &middot; <code>{}</code>', url, label, ref)
        return format_html("<code>{}</code>", ref)

    transaction_link.short_description = _("Transaction")

    # Actions
    actions = ["mark_open", "mark_in_progress", "mark_resolved", "mark_closed", "set_priority_low", "set_priority_medium", "set_priority_high"]

    def _bulk_set_status(self, request, queryset, status, label):
        updated = queryset.update(status=status)
        self.message_user(request, _("%(n)d complaint(s) marked %(label)s.") % {"n": updated, "label": label}, level=messages.SUCCESS)

    def mark_open(self, request, queryset):
        self._bulk_set_status(request, queryset, Complaint.Status.OPEN, _("Open"))
    mark_open.short_description = _("Mark selected as Open")

    def mark_in_progress(self, request, queryset):
        self._bulk_set_status(request, queryset, Complaint.Status.IN_PROGRESS, _("In Progress"))
    mark_in_progress.short_description = _("Mark selected In Progress")

    def mark_resolved(self, request, queryset):
        self._bulk_set_status(request, queryset, Complaint.Status.RESOLVED, _("Resolved"))
    mark_resolved.short_description = _("Mark selected Resolved")

    def mark_closed(self, request, queryset):
        self._bulk_set_status(request, queryset, Complaint.Status.CLOSED, _("Closed"))
    mark_closed.short_description = _("Mark selected Closed")

    def _bulk_set_priority(self, request, queryset, priority, label):
        updated = queryset.update(priority=priority)
        self.message_user(request, _("%(n)d complaint(s) set to %(label)s priority.") % {"n": updated, "label": label}, level=messages.SUCCESS)

    def set_priority_low(self, request, queryset):
        self._bulk_set_priority(request, queryset, Complaint.Priority.LOW, _("Low"))
    set_priority_low.short_description = _("Set priority: Low")

    def set_priority_medium(self, request, queryset):
        self._bulk_set_priority(request, queryset, Complaint.Priority.MEDIUM, _("Medium"))
    set_priority_medium.short_description = _("Set priority: Medium")

    def set_priority_high(self, request, queryset):
        self._bulk_set_priority(request, queryset, Complaint.Priority.HIGH, _("High"))
    set_priority_high.short_description = _("Set priority: High")


# Optional: register standalone admins for responses & attachments (useful for audits)

@admin.register(ComplaintResponse)
class ComplaintResponseAdmin(admin.ModelAdmin):
    list_display = ("complaint", "role", "author", "author_name", "created_at")
    list_filter = ("role", "created_at")
    search_fields = ("complaint__code", "complaint__title", "author_name", "message")
    autocomplete_fields = ("complaint", "author")
    date_hierarchy = "created_at"
    list_select_related = ("complaint", "author")


@admin.register(ComplaintAttachment)
class ComplaintAttachmentAdmin(admin.ModelAdmin):
    list_display = ("complaint", "original_name", "content_type", "uploaded_by", "uploaded_at")
    list_filter = ("content_type", "uploaded_at")
    search_fields = ("complaint__code", "complaint__title", "original_name", "file")
    autocomplete_fields = ("complaint", "uploaded_by")
    date_hierarchy = "uploaded_at"
    list_select_related = ("complaint", "uploaded_by")




def _admin_change_url(obj):
    if not obj:
        return None
    opts = obj._meta
    return reverse(f"admin:{opts.app_label}_{opts.model_name}_change", args=[obj.pk])


@admin.register(InvoiceType)
class InvoiceTypeAdmin(admin.ModelAdmin):
    # ---------- List view ----------
    list_display = (
        "invoice_type",
        "object_type",
        "object_id",
    )
    list_filter = (
        "invoice_type",
        "object_type",
    )
    search_fields = (
        "invoice__number",
        "invoice__transaction_id",
        "object_type",
        "object_id",
    )
    ordering = ("invoice_type", "object_type")
    autocomplete_fields = ("invoice",)
    list_select_related = ("invoice",)

    # ---------- Field layout ----------
    fieldsets = (
        (_("Invoice"), {
            "fields": ("invoice", "invoice_type"),
        }),
        (_("Linked Object"), {
            "description": _("Optional reference to the source object (e.g Tutor, Booking, Subscription)"),
            "fields": ("object_type", "object_id", "meta"),
        }),
    )
