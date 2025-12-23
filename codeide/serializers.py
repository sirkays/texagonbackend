# app: ide
from rest_framework import serializers
from .models import CodeSnippet, CodeSubmission, CodeComment,CodeFile
from academics.models import StudentProfile
from learning.models import Lesson


class CodeFileSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = CodeFile
        fields = [
            "id", "created_at", "updated_at",
            "student", "lesson", "label",
            "original_name", "content_type", "size_bytes",
            "url",
        ]
        read_only_fields = [
            "id", "created_at", "updated_at", "student",
            "original_name", "content_type", "size_bytes", "url",
        ]

    def get_url(self, obj):
        request = self.context.get("request")
        file_url = obj.url  # this gives "/media/...."
        if request is not None:
            return request.build_absolute_uri(file_url)
        return file_url


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
    title = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    comments = CodeCommentSerializer(many=True, read_only=True)

    class Meta:
        model = CodeSubmission
        fields = [
            "id",
            "title",  # ✅ add
            "lesson",
            "student",
            "language",
            "code_text",
            "status",
            "score",
            "feedback",
            "correction_code",
            "graded_by",
            "graded_at",
            "created_at",
            "updated_at",
            "comments",
        ]
        read_only_fields = [
            "id",
            "student",
            "status",
            "score",
            "feedback",
            "correction_code",
            "graded_by",
            "graded_at",
            "created_at",
            "updated_at",
            "comments",
        ]

    def validate_title(self, value):
        # Normalize whitespace/empty titles to None
        if value is None:
            return None
        value = value.strip()
        return value or None



class TeacherUpdateSubmissionSerializer(serializers.ModelSerializer):
    """
    For teacher PATCH: allow grading or corrections (and—if you want—code update).
    """
    class Meta:
        model = CodeSubmission
        fields = ["code_text", "score", "feedback", "correction_code", "status"]



class SubmissionListSerializer(serializers.ModelSerializer):
    # Add title but normalize empty/" " to None
    title = serializers.SerializerMethodField()

    student_name = serializers.SerializerMethodField()
    lesson_title = serializers.CharField(source="lesson.name", read_only=True)
    course_name = serializers.SerializerMethodField()
    class_name = serializers.SerializerMethodField()

    class Meta:
        model = CodeSubmission
        fields = [
            "id",
            "title",         # ✅ added
            "created_at",    # ✅ already here, now frontend will display it
            "updated_at",
            "status",
            "language",
            "student_name",
            "lesson_title",
            "course_name",
            "class_name",
            "score",
        ]

    def get_title(self, obj: CodeSubmission):
        t = (obj.title or "").strip()
        return t or None

    def get_student_name(self, obj: CodeSubmission) -> str:
        u = getattr(getattr(obj.student, "user", None), "get_full_name", lambda: "")() or ""
        if u.strip():
            return u
        return getattr(getattr(obj.student, "user", None), "email", "") or f"student-{obj.student_id}"

    def get_course_name(self, obj: CodeSubmission) -> str:
        try:
            return obj.lesson.module.course.name
        except Exception:
            return ""

    def get_class_name(self, obj: CodeSubmission) -> str:
        # Prefer course.classroom if available; fall back to student's classroom
        try:
            room = obj.lesson.module.course.classroom
            if room:
                return room.name
        except Exception:
            pass
        try:
            room = obj.student.classroom
            if room:
                return room.name
        except Exception:
            pass
        return ""

class TeacherCodeCommentSerializer(serializers.ModelSerializer):
    author_name = serializers.SerializerMethodField()
    class Meta:
        model = CodeComment
        fields = ["id", "created_at", "author", "author_role", "author_name", "message"]

    def get_author_name(self, obj):
        u = getattr(obj, "author", None)
        if not u:
            return ""
        full = (getattr(u, "get_full_name", lambda: "")() or "").strip()
        return full or u.email or f"user-{getattr(u, 'pk', '')}"


class TeacherCodeSubmissionDetailSerializer(serializers.ModelSerializer):
    student_name = serializers.SerializerMethodField()
    student_id = serializers.IntegerField(source="student.id", read_only=True)
    lesson = serializers.SerializerMethodField()
    course = serializers.SerializerMethodField()
    classroom = serializers.SerializerMethodField()
    comments = TeacherCodeCommentSerializer(many=True, read_only=True)

    class Meta:
        model = CodeSubmission
        fields = [
            "id", "created_at", "updated_at",
            "status", "language", "code_text",
            "score", "feedback", "correction_code",
            "graded_at", "graded_by_id",
            "student_id", "student_name",
            "lesson", "course", "classroom",
            "comments",
        ]

    def get_student_name(self, obj: CodeSubmission) -> str:
        u = getattr(getattr(obj.student, "user", None), "get_full_name", lambda: "")() or ""
        if u.strip():
            return u
        return getattr(getattr(obj.student, "user", None), "email", "") or f"student-{obj.student_id}"

    def get_lesson(self, obj):
        l = obj.lesson
        return {"id": getattr(l, "id", None), "title": getattr(l, "name", "")} if l else {"id": None, "title": ""}

    def get_course(self, obj):
        try:
            c = obj.lesson.module.course
        except Exception:
            c = None
        return {"id": getattr(c, "id", None), "name": getattr(c, "name", "")} if c else {"id": None, "name": ""}

    def get_classroom(self, obj):
        # Prefer course.classroom
        try:
            room = obj.lesson.module.course.classroom
            if room:
                return {"id": room.id, "name": room.name}
        except Exception:
            pass
        # Fallback student.classroom
        try:
            room = obj.student.classroom
            if room:
                return {"id": room.id, "name": room.name}
        except Exception:
            pass
        return {"id": None, "name": ""}



class StudentUpdateSubmissionSerializer(serializers.ModelSerializer):
    title = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    class Meta:
        model = CodeSubmission
        fields = ["title", "language", "code_text"]

    def validate_title(self, value):
        if value is None:
            return None
        value = value.strip()
        return value or None
