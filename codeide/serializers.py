# app: ide
from rest_framework import serializers
from .models import CodeSnippet, CodeSubmission, CodeComment


class CodeSnippetSerializer(serializers.ModelSerializer):
    class Meta:
        model = CodeSnippet
        fields = ["id", "lesson", "title", "language", "code_text", "meta", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


class CodeCommentSerializer(serializers.ModelSerializer):
    author_name = serializers.SerializerMethodField()

    class Meta:
        model = CodeComment
        fields = ["id", "author", "author_role", "author_name", "message", "created_at"]
        read_only_fields = ["id", "author", "author_role", "author_name", "created_at"]

    def get_author_name(self, obj):
        u = obj.author
        return (getattr(u, "get_full_name", lambda: "")() or u.email or str(u.pk))


class CodeSubmissionSerializer(serializers.ModelSerializer):
    comments = CodeCommentSerializer(many=True, read_only=True)

    class Meta:
        model = CodeSubmission
        fields = [
            "id", "lesson", "student", "language", "code_text",
            "status", "score", "feedback", "correction_code",
            "graded_by", "graded_at",
            "created_at", "updated_at",
            "comments",
        ]
        read_only_fields = [
            "id", "student", "status", "score", "feedback",
            "correction_code", "graded_by", "graded_at",
            "created_at", "updated_at", "comments",
        ]


class TeacherUpdateSubmissionSerializer(serializers.ModelSerializer):
    """
    For teacher PATCH: allow grading or corrections (and—if you want—code update).
    """
    class Meta:
        model = CodeSubmission
        fields = ["code_text", "score", "feedback", "correction_code", "status"]
