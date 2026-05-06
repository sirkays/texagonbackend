# app: ide/serializers.py
from rest_framework import serializers
from .models import CodeSnippet, CodeSubmission, CodeComment, CodeFile, Folder
from academics.models import StudentProfile
from learning.models import Lesson
from django.db.models import Max


# ---------------------------------------------------------------------------
# Folder
# ---------------------------------------------------------------------------
class FolderSerializer(serializers.ModelSerializer):
    path = serializers.CharField(read_only=True)
    snippet_count = serializers.SerializerMethodField()
    file_count = serializers.SerializerMethodField()

    class Meta:
        model = Folder
        fields = [
            "id", "name", "parent", "path",
            "snippet_count", "file_count",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "path", "snippet_count", "file_count", "created_at", "updated_at"]

    def get_snippet_count(self, obj):
        # Avoid extra queries if prefetched
        if hasattr(obj, "_prefetched_snippets_count"):
            return obj._prefetched_snippets_count
        return obj.snippets.count()

    def get_file_count(self, obj):
        if hasattr(obj, "_prefetched_files_count"):
            return obj._prefetched_files_count
        return obj.files.count()

    def validate_name(self, value):
        v = (value or "").strip()
        if not v:
            raise serializers.ValidationError("Folder name is required.")
        # Disallow path separators in the name itself
        if "/" in v or "\\" in v:
            raise serializers.ValidationError("Folder name cannot contain '/' or '\\'.")
        if len(v) > 128:
            raise serializers.ValidationError("Folder name too long.")
        return v


# ---------------------------------------------------------------------------
# CodeFile
# ---------------------------------------------------------------------------
class CodeFileSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = CodeFile
        fields = [
            "id", "created_at", "updated_at",
            "student", "lesson", "folder", "label",
            "original_name", "content_type", "size_bytes",
            "url",
        ]
        read_only_fields = [
            "id", "created_at", "updated_at", "student",
            "original_name", "content_type", "size_bytes", "url",
        ]

    def get_url(self, obj):
        request = self.context.get("request")
        file_url = obj.url
        if request is not None and file_url:
            return request.build_absolute_uri(file_url)
        return file_url


# ---------------------------------------------------------------------------
# CodeSnippet
# ---------------------------------------------------------------------------
class CodeSnippetSerializer(serializers.ModelSerializer):
    class Meta:
        model = CodeSnippet
        fields = [
            "id", "lesson", "folder", "title", "language",
            "code_text", "meta", "created_at", "updated_at",
        ]
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


class CodeSubmissionMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = CodeSubmission
        fields = [
            "id", "title", "lesson", "student", "language",
            "code_text", "created_at", "updated_at",
            "status", "score", "feedback", "correction_code",
        ]


class CodeSubmissionSerializer(serializers.ModelSerializer):
    title = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    comments = CodeCommentSerializer(many=True, read_only=True)

    graded_by_name = serializers.SerializerMethodField()
    latest_same_title_submission = serializers.SerializerMethodField()

    class Meta:
        model = CodeSubmission
        fields = [
            "id", "title", "lesson", "student", "language", "code_text",
            "status", "score", "feedback", "correction_code",
            "graded_by", "graded_by_name", "graded_at",
            "created_at", "updated_at", "comments",
            "latest_same_title_submission",
        ]

    def get_graded_by_name(self, obj):
        if not obj.graded_by:
            return None
        user = getattr(obj.graded_by, "user", None)
        if user:
            return user.get_full_name() or user.username or user.email
        return str(obj.graded_by)

    def get_latest_same_title_submission(self, obj):
        if not obj.title:
            return {}

        base = (
            CodeSubmission.objects
            .filter(
                student=obj.student,
                lesson=obj.lesson,
                title__iexact=obj.title.strip(),
            )
            .exclude(id=obj.id)
        )

        latest_times = (
            base.values("language")
            .annotate(latest_created_at=Max("created_at"))
        )

        if not latest_times:
            return {}

        out = {}
        for row in latest_times:
            lang = row["language"]
            dt = row["latest_created_at"]
            latest_obj = (
                base.filter(language=lang, created_at=dt)
                .order_by("-created_at", "-id")
                .first()
            )
            if latest_obj:
                out[lang] = CodeSubmissionMiniSerializer(latest_obj).data

        return out


class TeacherUpdateSubmissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = CodeSubmission
        fields = ["code_text", "score", "feedback", "correction_code", "status"]


class SubmissionListSerializer(serializers.ModelSerializer):
    title = serializers.SerializerMethodField()
    student_name = serializers.SerializerMethodField()
    lesson_title = serializers.CharField(source="lesson.name", read_only=True)
    course_name = serializers.SerializerMethodField()
    class_name = serializers.SerializerMethodField()

    class Meta:
        model = CodeSubmission
        fields = [
            "id", "title", "created_at", "updated_at",
            "status", "language", "student_name",
            "lesson_title", "course_name", "class_name", "score",
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


class TeacherCodeSubmissionMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = CodeSubmission
        fields = [
            "id", "title", "created_at", "updated_at",
            "status", "language", "code_text",
            "score", "feedback", "correction_code",
            "graded_at", "graded_by_id",
        ]


class TeacherCodeSubmissionDetailSerializer(serializers.ModelSerializer):
    student_name = serializers.SerializerMethodField()
    student_id = serializers.IntegerField(source="student.id", read_only=True)
    title = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    lesson = serializers.SerializerMethodField()
    course = serializers.SerializerMethodField()
    classroom = serializers.SerializerMethodField()
    comments = TeacherCodeCommentSerializer(many=True, read_only=True)
    latest_same_title_submission = serializers.SerializerMethodField()

    class Meta:
        model = CodeSubmission
        fields = [
            "id", "title", "created_at", "updated_at",
            "status", "language", "code_text",
            "score", "feedback", "correction_code",
            "graded_at", "graded_by_id",
            "student_id", "student_name",
            "lesson", "course", "classroom",
            "comments",
            "latest_same_title_submission",
        ]

    def get_latest_same_title_submission(self, obj: CodeSubmission):
        title = (obj.title or "").strip()
        if not title:
            return {}

        base = CodeSubmission.objects.filter(
            student=obj.student,
            lesson=obj.lesson,
            title__iexact=title,
        )

        latest_times = base.values("language").annotate(
            latest_created_at=Max("created_at")
        )

        out = {}
        for row in latest_times:
            lang = row["language"]
            dt = row["latest_created_at"]
            latest_obj = (
                base.filter(language=lang, created_at=dt)
                .order_by("-created_at", "-id")
                .first()
            )
            if not latest_obj or latest_obj.id == obj.id:
                continue
            out[lang] = TeacherCodeSubmissionMiniSerializer(latest_obj).data
        return out

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
        try:
            room = obj.lesson.module.course.classroom
            if room:
                return {"id": room.id, "name": room.name}
        except Exception:
            pass
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