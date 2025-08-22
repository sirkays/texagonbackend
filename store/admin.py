from django.contrib import admin
from .models import ProductCategory, Product, Order, OrderItem

@admin.register(ProductCategory)
class ProductCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "parent", "created_at")
    list_filter = ("organization",)
    search_fields = ("name", "organization__name")
    autocomplete_fields = ("organization", "parent")

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("sku", "title", "organization", "category", "price", "sale_price", "stock_qty", "is_digital", "is_active", "created_at")
    list_filter = ("organization", "category", "is_digital", "is_active")
    search_fields = ("sku", "title", "organization__name", "category__name")
    autocomplete_fields = ("organization", "category")

class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    autocomplete_fields = ("product",)

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "organization", "user", "status", "subtotal", "discount", "tax", "total", "currency", "paid_at", "created_at")
    list_filter = ("organization", "status", "currency", "paid_at")
    search_fields = ("id", "organization__name", "user__username")
    autocomplete_fields = ("organization", "user")
    inlines = [OrderItemInline]

@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ("order", "product", "quantity", "unit_price", "created_at")
    list_filter = ("product",)
    search_fields = ("order__id", "product__sku", "product__title")
    autocomplete_fields = ("order", "product")
