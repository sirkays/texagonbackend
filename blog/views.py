from django.shortcuts import render, get_object_or_404
from django.core.paginator import Paginator
from django.http import JsonResponse, HttpResponse
from django.db.models import Q
from django.views.decorators.http import require_POST,require_GET

from .models import BlogPost, Category, Tag, NewsletterSubscriber
from django.contrib.admin.views.decorators import staff_member_required
from .seeder import seed_blog


@staff_member_required
@require_GET
def seed_blog_view(request):
    flush = request.GET.get("flush") == "1"
    no_subscribers = request.GET.get("no_subscribers") == "1"

    posts = request.GET.get("posts")
    try:
        posts_count = int(posts) if posts else None
    except ValueError:
        return HttpResponse("Invalid posts value. It must be a number.", status=400)

    logs = seed_blog(
        flush=flush,
        posts_count=posts_count,
        no_subscribers=no_subscribers,
    )

    return HttpResponse("<br>".join(logs))

def blog_list(request):
    posts = (
        BlogPost.objects.filter(is_published=True)
        .select_related("author", "category", "author__author_profile")
        .prefetch_related("tags")
    )

    # ── Category filter ──────────────────────────────────────────────────────
    active_category = request.GET.get("category", "all")
    if active_category and active_category != "all":
        posts = posts.filter(category__slug=active_category)

    # ── Search ───────────────────────────────────────────────────────────────
    query = request.GET.get("q", "").strip()
    if query:
        posts = posts.filter(
            Q(title__icontains=query)
            | Q(excerpt__icontains=query)
            | Q(category__name__icontains=query)
            | Q(tags__name__icontains=query)
        ).distinct()

    # ── Featured post (only on the default "all" view, no search) ─────────────
    featured_post = None
    if active_category == "all" and not query:
        featured_post = (
            BlogPost.objects.filter(is_published=True, is_featured=True)
            .select_related("author", "category", "author__author_profile")
            .first()
        )
        if featured_post:
            posts = posts.exclude(pk=featured_post.pk)

    # ── Pagination ────────────────────────────────────────────────────────────
    paginator = Paginator(posts, 6)
    page_obj = paginator.get_page(request.GET.get("page"))

    # ── Sidebar data ─────────────────────────────────────────────────────────
    categories = Category.objects.all()
    recent_posts = (
        BlogPost.objects.filter(is_published=True)
        .select_related("category")
        .order_by("-published_at")[:3]
    )
    tags = Tag.objects.all()

    # Article counts per category for sidebar
    category_counts = {
        cat.slug: cat.posts.filter(is_published=True).count()
        for cat in categories
    }

    context = {
        "featured_post": featured_post,
        "page_obj": page_obj,
        "categories": categories,
        "category_counts": category_counts,
        "recent_posts": recent_posts,
        "tags": tags,
        "query": query,
        "active_category": active_category,
    }
    return render(request, "blog/blog_list.html", context)


def blog_detail(request, slug):
    post = get_object_or_404(
        BlogPost.objects.select_related("author", "category", "author__author_profile")
        .prefetch_related("tags"),
        slug=slug,
        is_published=True,
    )

    related_posts = (
        BlogPost.objects.filter(is_published=True, category=post.category)
        .exclude(pk=post.pk)
        .select_related("author", "category")
        .order_by("-published_at")[:2]
    )

    context = {
        "post": post,
        "related_posts": related_posts,
    }
    return render(request, "blog/blog_detail.html", context)


@require_POST
def newsletter_subscribe(request):
    email = request.POST.get("email", "").strip()
    if not email:
        return JsonResponse({"success": False, "message": "Please provide a valid email."})

    subscriber, created = NewsletterSubscriber.objects.get_or_create(email=email)
    if created:
        message = "Thank you for subscribing!"
    else:
        if not subscriber.is_active:
            subscriber.is_active = True
            subscriber.save(update_fields=["is_active"])
            message = "Welcome back! You've been resubscribed."
        else:
            message = "You're already subscribed!"

    return JsonResponse({"success": True, "message": message})