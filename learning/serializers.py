from rest_framework import serializers
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from learning.models import Course

class CourseGeneralActivationSerializer(serializers.ModelSerializer):
    # accept ISO string or null from frontend
    general_activation_date = serializers.DateTimeField(required=False, allow_null=True)

    class Meta:
        model = Course
        fields = ("general_activation", "general_activation_date")

    def validate(self, attrs):
        ga = attrs.get("general_activation", getattr(self.instance, "general_activation", False))
        gad = attrs.get("general_activation_date", getattr(self.instance, "general_activation_date", None))

        # If turning off, clear date
        if ga is False:
            attrs["general_activation_date"] = None
            return attrs

        # If turning on and date provided, ensure aware
        if ga is True and gad:
            if timezone.is_naive(gad):
                attrs["general_activation_date"] = timezone.make_aware(gad, timezone.get_current_timezone())

        # You can enforce "must provide date when enabled" if you want:
        # if ga is True and not gad:
        #     raise serializers.ValidationError({"general_activation_date": "Required when general_activation is true."})

        return attrs
