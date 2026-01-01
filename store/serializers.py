from rest_framework import serializers
from store.models import Review

class ReviewSerializer(serializers.ModelSerializer):
    user_name = serializers.SerializerMethodField()

    class Meta:
        model = Review
        fields = ["id", "product", "user", "user_name", "rating", "title", "body", "created_at", "updated_at"]
        read_only_fields = ["id", "product", "user", "user_name", "created_at", "updated_at"]

    def get_user_name(self, obj):
        u = obj.user
        return getattr(u, "get_full_name", lambda: "")() or getattr(u, "username", "") or getattr(u, "email", "")
