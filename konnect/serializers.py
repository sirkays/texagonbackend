from rest_framework import serializers
from .models import KonnectRoom


class KonnectRoomListSerializer(serializers.ModelSerializer):
    creator_name = serializers.SerializerMethodField()
    allowed_courses_count = serializers.IntegerField(source="allowed_courses.count", read_only=True)
    allowed_users_count = serializers.IntegerField(source="allowed_users.count", read_only=True)

    class Meta:
        model = KonnectRoom
        fields = [
            "id",
            "name",
            "room_id",
            "room_url",
            "status",
            "creator_name",
            "allowed_courses_count",
            "allowed_users_count",
            "created_at",
            "updated_at",
        ]

    def get_creator_name(self, obj):
        return f"{obj.creator.first_name} {obj.creator.last_name}".strip()