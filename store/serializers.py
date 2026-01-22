from rest_framework import serializers
from store.models import Review, Category, Product, ProductImage
from decimal import Decimal
from django.utils.text import slugify


class ReviewSerializer(serializers.ModelSerializer):
    user_name = serializers.SerializerMethodField()

    class Meta:
        model = Review
        fields = ["id", "product", "user", "user_name", "rating", "title", "body", "created_at", "updated_at"]
        read_only_fields = ["id", "product", "user", "user_name", "created_at", "updated_at"]

    def get_user_name(self, obj):
        u = obj.user
        return getattr(u, "get_full_name", lambda: "")() or getattr(u, "username", "") or getattr(u, "email", "")




class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "slug", "parent", "created_at", "updated_at"]


class ProductImageSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()

    class Meta:
        model = ProductImage
        fields = ["id", "product", "product_image", "url", "alt_text", "sort_order", "created_at"]

    def get_url(self, obj):
        req = self.context.get("request")
        return obj.get_absolute_url(req)


class ProductAdminSerializer(serializers.ModelSerializer):
    category_obj = CategorySerializer(source="category", read_only=True)
    images = ProductImageSerializer(many=True, read_only=True)
    pay_in_4_amount = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)

    class Meta:
        model = Product
        fields = [
            "id",
            "title",
            "slug",
            "product_type",
            "category",
            "category_obj",
            "description",
            "price",
            "pay_in_4_amount",
            "bnpl_enabled",
            "default_bnpl_plan",
            "rating",
            "rating_count",
            "is_digital",
            "sku",
            "stock",
            "is_active",
            "images",
            "created_at",
            "updated_at",
        ]

    def validate(self, attrs):
        is_digital = attrs.get("is_digital", getattr(self.instance, "is_digital", True))
        sku = attrs.get("sku", getattr(self.instance, "sku", ""))

        if not is_digital and not sku:
            raise serializers.ValidationError({"sku": "SKU is required for physical items."})

        price = attrs.get("price", getattr(self.instance, "price", None))
        if price is not None and Decimal(price) < Decimal("0.00"):
            raise serializers.ValidationError({"price": "Price must be >= 0.00"})
        return attrs

    def create(self, validated_data):
        # Ensure slug uniqueness (simple strategy)
        slug = validated_data.get("slug") or slugify(validated_data.get("title") or "")
        if slug:
            base = slug
            i = 1
            while Product.objects.filter(slug=slug).exists():
                i += 1
                slug = f"{base}-{i}"
            validated_data["slug"] = slug or "slug001"

        return super().create(validated_data)
