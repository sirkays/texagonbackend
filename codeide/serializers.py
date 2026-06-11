# codeide/serializers.py

from rest_framework import serializers
from .models import CodeSnippet, CodeProject, ProjectFile, CodeComment, CodeFile, Folder


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
# CodeFile (IDE uploads — not project submissions)
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


# ---------------------------------------------------------------------------
# CodeComment
# ---------------------------------------------------------------------------
class CodeCommentSerializer(serializers.ModelSerializer):
    author_name = serializers.SerializerMethodField()

    class Meta:
        model = CodeComment
        fields = ["id", "author", "author_role", "author_name", "message", "created_at"]
        read_only_fields = ["id", "author", "author_role", "author_name", "created_at"]

    def get_author_name(self, obj):
        u = obj.author
        return (getattr(u, "get_full_name", lambda: "")() or u.email or str(u.pk))


# ---------------------------------------------------------------------------
# ProjectFile — individual file within a project
# ---------------------------------------------------------------------------
class ProjectFileSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProjectFile
        fields = [
            "id", "path", "language", "code_text",
            "is_binary", "content_type", "size_bytes",
            "correction_code",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


# ---------------------------------------------------------------------------
# CodeProject — list view (teacher submissions list)
# ---------------------------------------------------------------------------
class CodeProjectListSerializer(serializers.ModelSerializer):
    student_name = serializers.SerializerMethodField()
    lesson_title = serializers.CharField(source="lesson.name", read_only=True)
    course_name = serializers.SerializerMethodField()
    class_name = serializers.SerializerMethodField()
    file_count = serializers.SerializerMethodField()
    file_languages = serializers.SerializerMethodField()
    file_names = serializers.SerializerMethodField()

    class Meta:
        model = CodeProject
        fields = [
            "id", "title", "created_at", "updated_at",
            "status", "student_name",
            "lesson_title", "course_name", "class_name", "score",
            "file_count", "file_languages", "file_names",
        ]

    def get_file_count(self, obj):
        if hasattr(obj, "_prefetched_objects_cache") and "files" in obj._prefetched_objects_cache:
            return len(obj._prefetched_objects_cache["files"])
        return obj.files.count()

    def get_file_languages(self, obj):
        if hasattr(obj, "_prefetched_objects_cache") and "files" in obj._prefetched_objects_cache:
            langs = set(f.language for f in obj._prefetched_objects_cache["files"])
        else:
            langs = set(obj.files.values_list("language", flat=True).distinct())
        return sorted(langs) if langs else []

    def get_file_names(self, obj):
        if hasattr(obj, "_prefetched_objects_cache") and "files" in obj._prefetched_objects_cache:
            return [f.path for f in obj._prefetched_objects_cache["files"]]
        return list(obj.files.values_list("path", flat=True))

    def get_student_name(self, obj):
        u = getattr(getattr(obj.student, "user", None), "get_full_name", lambda: "")() or ""
        if u.strip():
            return u
        return getattr(getattr(obj.student, "user", None), "email", "") or f"student-{obj.student_id}"

    def get_course_name(self, obj):
        try:
            return obj.lesson.module.course.name
        except Exception:
            return ""

    def get_class_name(self, obj):
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


# ---------------------------------------------------------------------------
# CodeProject — detail view (teacher viewing a submission)
# ---------------------------------------------------------------------------
class CodeProjectDetailSerializer(serializers.ModelSerializer):
    student_name = serializers.SerializerMethodField()
    student_id = serializers.IntegerField(source="student.id", read_only=True)
    lesson = serializers.SerializerMethodField()
    course = serializers.SerializerMethodField()
    classroom = serializers.SerializerMethodField()
    comments = CodeCommentSerializer(many=True, read_only=True)
    files = ProjectFileSerializer(many=True, read_only=True)

    class Meta:
        model = CodeProject
        fields = [
            "id", "title", "created_at", "updated_at",
            "status", "score", "feedback",
            "graded_at", "graded_by_id",
            "student_id", "student_name",
            "lesson", "course", "classroom",
            "comments", "files",
        ]

    def get_student_name(self, obj):
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


# ---------------------------------------------------------------------------
# Student-facing project serializer (lighter than teacher detail)
# ---------------------------------------------------------------------------
class StudentProjectSerializer(serializers.ModelSerializer):
    files = ProjectFileSerializer(many=True, read_only=True)
    comments = CodeCommentSerializer(many=True, read_only=True)
    graded_by_name = serializers.SerializerMethodField()
    lesson_title = serializers.SerializerMethodField()

    class Meta:
        model = CodeProject
        fields = [
            "id", "title", "lesson", "status",
            "score", "feedback",
            "graded_at", "graded_by_id", "graded_by_name",
            "lesson_title",
            "created_at", "updated_at",
            "files", "comments",
        ]

    def get_graded_by_name(self, obj):
        if not obj.graded_by:
            return None
        u = getattr(obj.graded_by, "user", None)
        if not u:
            return None
        return (getattr(u, "get_full_name", lambda: "")() or u.email or str(u.pk))

    def get_lesson_title(self, obj):
        return getattr(obj.lesson, "name", "") if obj.lesson else ""