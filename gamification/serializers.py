from rest_framework import serializers
from gamification.models import AchievementDefinition, Badge
from gamification.services.rules import SUPPORTED_METRICS

def validate_rule(rule: dict):
    rule = rule or {}
    metric = rule.get("metric")
    event_type = rule.get("event_type")
    target = rule.get("target")

    if metric not in SUPPORTED_METRICS:
        raise serializers.ValidationError({"rule": f"Unsupported metric: {metric}"})
    if not event_type or not isinstance(event_type, str):
        raise serializers.ValidationError({"rule": "event_type is required"})
    try:
        int(target)
    except Exception:
        raise serializers.ValidationError({"rule": "target must be a number"})

    # optional fields sanity checks
    if rule.get("window_days") is not None:
        try:
            int(rule.get("window_days"))
        except Exception:
            raise serializers.ValidationError({"rule": "window_days must be a number or null"})

    if metric == "distinct_count" and not rule.get("distinct_key"):
        raise serializers.ValidationError({"rule": "distinct_key is required for distinct_count"})

    return rule


class AchievementDefinitionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AchievementDefinition
        fields = [
            "id",
            "organization",
            "code",
            "title",
            "description",
            "icon",
            "category",
            "points",
            "is_active",
            "rule",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate_rule(self, value):
        return validate_rule(value)

    def validate_code(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("code is required")
        return value


class BadgeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Badge
        fields = [
            "id",
            "organization",
            "name",
            "icon_name",
            "color",
            "points",
            "criteria",
            "rules",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate_name(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("name is required")
        return value
