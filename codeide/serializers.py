# app: ide/serializers.py
from rest_framework import serializers
from .models import CodeSnippet, CodeSubmission, CodeComment, CodeFile, Folder
from academics.models import StudentProfile
from learning.models import Lesson
from django.db.models import Max
from django.db.models.functions import Lower, Trim


# Map language → default extension. Used everywhere a file_name is missing.
LANG_EXT_MAP = {
    "javascript": "js",
    "python": "py",
    "html": "html",
    "css": "css",
    "java": "java",
    "cpp": "cpp",
}


def default_filename_for(language: str, idx: int = 0) -> str:
    """Generate a stable default filename for a legacy submission with no file_name."""
    ext = LANG_EXT_MAP.get(language, language or "txt")
    suffix = "" if idx == 0 else f"-{idx}"
    return f"untitled{suffix}.{ext}"


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
            "id", "title", "file_name", "lesson", "student", "language",
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
            "id", "title", "file_name", "lesson", "student", "language", "code_text",
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
    file_count = serializers.SerializerMethodField()
    file_languages = serializers.SerializerMethodField()
    file_names = serializers.SerializerMethodField()

    class Meta:
        model = CodeSubmission
        fields = [
            "id", "title", "created_at", "updated_at",
            "status", "language", "student_name",
            "lesson_title", "course_name", "class_name", "score",
            "file_count", "file_languages", "file_names",
        ]

    def _get_sibling_qs(self, obj: CodeSubmission):
        """All submissions sharing the same (student, lesson, title)."""
        title = (obj.title or "").strip()
        if not title:
            return CodeSubmission.objects.filter(pk=obj.pk)
        return (
            CodeSubmission.objects
            .filter(student=obj.student, lesson=obj.lesson)
            .annotate(title_norm=Lower(Trim("title")))
            .filter(title_norm=title.lower())
        )

    def get_file_count(self, obj: CodeSubmission) -> int:
        return self._get_sibling_qs(obj).count()

    def get_file_languages(self, obj: CodeSubmission) -> list:
        langs = list(self._get_sibling_qs(obj).values_list("language", flat=True).distinct())
        return sorted(set(langs)) if langs else [obj.language]

    def get_file_names(self, obj: CodeSubmission) -> list:
        """Return the file names of every submission in this project."""
        siblings = self._get_sibling_qs(obj)
        names = []
        for sub in siblings.order_by("created_at", "id"):
            name = (sub.file_name or "").strip()
            if not name:
                name = default_filename_for(sub.language)
            names.append(name)
        return names

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
    """
    Per-file payload used inside `all_project_files`. We always populate
    `file_name` (filling in a sensible default for legacy rows) so the
    frontend never has to guess.
    """
    file_name = serializers.SerializerMethodField()

    class Meta:
        model = CodeSubmission
        fields = [
            "id", "title", "file_name", "created_at", "updated_at",
            "status", "language", "code_text",
            "score", "feedback", "correction_code",
            "graded_at", "graded_by_id",
        ]

    def get_file_name(self, obj):
        name = (obj.file_name or "").strip()
        if name:
            return name
        idx = self.context.get("legacy_idx", {}).get(obj.language, 0)
        return default_filename_for(obj.language, idx)


class TeacherCodeSubmissionDetailSerializer(serializers.ModelSerializer):
    student_name = serializers.SerializerMethodField()
    student_id = serializers.IntegerField(source="student.id", read_only=True)
    title = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    file_name = serializers.SerializerMethodField()

    lesson = serializers.SerializerMethodField()
    course = serializers.SerializerMethodField()
    classroom = serializers.SerializerMethodField()
    comments = TeacherCodeCommentSerializer(many=True, read_only=True)
    latest_same_title_submission = serializers.SerializerMethodField()
    all_project_files = serializers.SerializerMethodField()

    class Meta:
        model = CodeSubmission
        fields = [
            "id", "title", "file_name", "created_at", "updated_at",
            "status", "language", "code_text",
            "score", "feedback", "correction_code",
            "graded_at", "graded_by_id",
            "student_id", "student_name",
            "lesson", "course", "classroom",
            "comments",
            "latest_same_title_submission",
            "all_project_files",
        ]

    def get_file_name(self, obj):
        name = (obj.file_name or "").strip()
        return name or default_filename_for(obj.language)

    def get_all_project_files(self, obj: CodeSubmission):
        """
        Return ALL submissions sharing the same (student, lesson, title)
        as a flat list of project files — no de-duplication.

        Every CodeSubmission row that matches the project key is included
        so the teacher sees every file the student submitted.

        Sorted: HTML first, then CSS, JS, then everything else alphabetical.
        """
        title = (obj.title or "").strip()
        if not title:
            return [TeacherCodeSubmissionMiniSerializer(obj).data]

        siblings = (
            CodeSubmission.objects
            .filter(student=obj.student, lesson=obj.lesson, title__iexact=title)
            .order_by("created_at", "id")
        )

        files_payload = [
            TeacherCodeSubmissionMiniSerializer(sub).data
            for sub in siblings
        ]

        if not files_payload:
            files_payload = [TeacherCodeSubmissionMiniSerializer(obj).data]

        def lang_rank(lang: str) -> int:
            return {"html": 0, "css": 1, "javascript": 2}.get(lang, 3)

        files_payload.sort(
            key=lambda d: (
                lang_rank(d.get("language", "")),
                (d.get("file_name") or "").lower(),
            )
        )
        return files_payload

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