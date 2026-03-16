from django.shortcuts import render, get_object_or_404
from django.db.models import Q
from .models import StudentProject, ProjectCategory, ProjectTag
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse
from django.views.decorators.http import require_GET

from .seeder import seed_projects


@staff_member_required
@require_GET
def seed_projects_view(request):
    flush = request.GET.get("flush") == "1"

    logs = seed_projects(flush=flush)
    return HttpResponse("<br>".join(logs))


def project_list(request):
    projects = (
        StudentProject.objects.filter(is_published=True)
        .select_related("category")
        .prefetch_related("tags")
    )

    # ── Category filter ──────────────────────────────────────────
    active_category = request.GET.get("category", "all")
    if active_category != "all":
        projects = projects.filter(category__slug=active_category)

    # ── Difficulty filter ────────────────────────────────────────
    active_difficulty = request.GET.get("difficulty", "all")
    if active_difficulty != "all":
        projects = projects.filter(difficulty=active_difficulty)

    # ── Search ───────────────────────────────────────────────────
    query = request.GET.get("q", "").strip()
    if query:
        projects = projects.filter(
            Q(title__icontains=query)
            | Q(excerpt__icontains=query)
            | Q(student_name__icontains=query)
            | Q(student_school__icontains=query)
            | Q(category__name__icontains=query)
            | Q(tags__name__icontains=query)
        ).distinct()

    categories = ProjectCategory.objects.all()

    context = {
        "projects":          projects,
        "categories":        categories,
        "active_category":   active_category,
        "active_difficulty": active_difficulty,
        "query":             query,
    }
    return render(request, "projects/project_list.html", context)


def project_detail(request, slug):
    project = get_object_or_404(
        StudentProject.objects.select_related("category")
        .prefetch_related("tags"),
        slug=slug,
        is_published=True,
    )

    related = (
        StudentProject.objects.filter(is_published=True, category=project.category)
        .exclude(pk=project.pk)
        .select_related("category")
        .order_by("-is_featured", "-completed_at")[:3]
    )

    context = {
        "project": project,
        "related": related,
    }
    return render(request, "projects/project_detail.html", context)