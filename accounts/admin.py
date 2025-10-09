from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.db.models import Q, Count
from .models import AdminAccess, User
from django import forms
from orgs.models import Organization


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    # Show extra fields
    list_display = ( "email", "first_name", "last_name", "phone", "primary_org", "is_staff", "is_active")
    list_filter = ("is_staff", "is_superuser", "is_active", "groups")
    search_fields = ( "email", "first_name", "last_name", "phone")
    ordering = ("email",)

    fieldsets = (
        (None, {"fields": ( "password",)}),
        ("Personal info", {"fields": ("first_name", "last_name", "email", "phone", "avatar", "primary_org")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )

    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ( "email", "password1", "password2", "first_name", "last_name", "phone", "primary_org"),
        }),
    )


class AdminAccessForm(forms.ModelForm):
    class Meta:
        model = AdminAccess
        fields = ("user", "organizations", "selected_organization", "active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Large relations: quicker widgets
        self.fields["user"].widget = admin.widgets.ForeignKeyRawIdWidget(
            rel=AdminAccess._meta.get_field("user").remote_field,
            admin_site=admin.site,
        )

        # If you prefer the dual list box instead, keep this line in the ModelAdmin:
        # filter_horizontal = ("organizations",)

        # Dynamically constrain selected_organization to the chosen organizations
        # When form is bound, use POSTed org ids; otherwise fall back to instance.
        if self.is_bound:
            # organizations is a M2M; when bound, it's a list of IDs in POST data
            org_ids = self.data.getlist("organizations") or []
        else:
            org_ids = (
                list(self.instance.organizations.values_list("pk", flat=True))
                if self.instance and self.instance.pk
                else []
            )

        if org_ids:
            self.fields["selected_organization"].queryset = Organization.objects.filter(
                pk__in=org_ids
            )
        else:
            # nothing selected yet – show no choices to force user flow
            self.fields["selected_organization"].queryset = Organization.objects.none()

        # Nice hint for the UX
        self.fields["selected_organization"].help_text = (
            "Must be one of the organizations selected above."
        )

    def clean(self):
        cleaned = super().clean()
        sel_org = cleaned.get("selected_organization")
        orgs = cleaned.get("organizations")

        if sel_org and orgs and sel_org not in orgs:
            self.add_error(
                "selected_organization",
                "Selected organization must be in the organizations list.",
            )

        # Optional nicety: auto-fill if exactly one org chosen and none selected
        if not cleaned.get("selected_organization") and orgs and len(orgs) == 1:
            cleaned["selected_organization"] = next(iter(orgs))

        return cleaned


@admin.register(AdminAccess)
class AdminAccessAdmin(admin.ModelAdmin):
    form = AdminAccessForm

    list_display = (
        "user",
        "user_email",
        "user_is_staff",
        "user_is_superuser",
        "org_count",
        "selected_organization",
        "active",
    )
    list_filter = (
        "active",
        "user__is_staff",
        "user__is_superuser",
        "selected_organization",
        "organizations",
    )
    search_fields = (
        "user__email",
        "user__first_name",
        "user__last_name",
        "organizations__name",
    )

    # Faster widgets for big relations
    raw_id_fields = ("user",)
    filter_horizontal = ("organizations",)

    actions = ("activate_selected", "deactivate_selected")

    def get_queryset(self, request):
        qs = (
            super()
            .get_queryset(request)
            .select_related("user", "selected_organization")
            .prefetch_related("organizations")
            .annotate(_org_count=Count("organizations"))
        )
        return qs

    # Limit the User dropdown to staff OR superusers in the admin form
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "user":
            kwargs["queryset"] = User.objects.filter(
                Q(is_staff=True) | Q(is_superuser=True)
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    # Niceties for list display
    @admin.display(ordering="user__email", description="Email")
    def user_email(self, obj):
        return obj.user.email

    @admin.display(boolean=True, ordering="user__is_staff", description="Staff")
    def user_is_staff(self, obj):
        return obj.user.is_staff

    @admin.display(boolean=True, ordering="user__is_superuser", description="Superuser")
    def user_is_superuser(self, obj):
        return obj.user.is_superuser

    @admin.display(ordering="_org_count", description="# Orgs")
    def org_count(self, obj):
        return getattr(obj, "_org_count", obj.organizations.count())

    # Bulk actions
    def activate_selected(self, request, queryset):
        updated = queryset.update(active=True)
        self.message_user(request, f"{updated} admin access record(s) activated.")
    activate_selected.short_description = "Activate selected admin access"

    def deactivate_selected(self, request, queryset):
        updated = queryset.update(active=False)
        self.message_user(request, f"{updated} admin access record(s) deactivated.")
    deactivate_selected.short_description = "Deactivate selected admin access"
