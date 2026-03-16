import textwrap
from datetime import timedelta
from accounts.models import User
from django.utils import timezone
from django.utils.text import slugify

from blog.models import AuthorProfile, BlogPost, Category, NewsletterSubscriber, Tag


CATEGORIES = [
    "Coding",
    "Robotics",
    "Cybersecurity",
    "Design",
    "Data Science",
    "Career Tips",
]

TAGS = [
    "JavaScript",
    "Python",
    "WebDev",
    "Robotics",
    "Security",
    "DataScience",
    "HTML",
    "CSS",
    "AI",
    "MachineLearning",
    "BeginnerFriendly",
    "ProjectIdeas",
]

AUTHORS = [
    {
        "first_name": "sarah.johnson",
        "first_name": "Sarah",
        "last_name": "Johnson",
        "email": "sarah@techxagon.com",
        "title": "Coding Instructor",
        "bio": (
            "Sarah is an experienced coding instructor with 8+ years of experience "
            "teaching Python and web development to students of all ages."
        ),
    },
    {
        "first_name": "david.okafor",
        "first_name": "David",
        "last_name": "Okafor",
        "email": "david@techxagon.com",
        "title": "Robotics Engineer",
        "bio": (
            "David builds autonomous systems by day and teaches robotics workshops "
            "on weekends. He loves making hardware accessible to young engineers."
        ),
    },
    {
        "first_name": "amara.nwosu",
        "first_name": "Amara",
        "last_name": "Nwosu",
        "email": "amara@techxagon.com",
        "title": "Cybersecurity Analyst",
        "bio": (
            "Amara has worked in threat intelligence for over six years and is "
            "passionate about teaching digital safety to the next generation."
        ),
    },
    {
        "first_name": "felix.eze",
        "first_name": "Felix",
        "last_name": "Eze",
        "email": "felix@techxagon.com",
        "title": "UI/UX Designer",
        "bio": (
            "Felix is a product designer who believes great design is invisible. "
            "He runs Techxagon's design thinking curriculum."
        ),
    },
]

POSTS = [
    (
        "Building Your First Web App",
        "Coding",
        ["HTML", "CSS", "JavaScript", "WebDev", "BeginnerFriendly"],
        5,
        (
            "A complete beginner's guide to creating interactive web applications "
            "using HTML, CSS, and JavaScript. Learn best practices and avoid "
            "common pitfalls."
        ),
        textwrap.dedent("""\
            <p>Creating your first web application is one of the most rewarding experiences
            in a developer's career. In this guide we'll walk through everything you need
            to get a simple, interactive app running in the browser.</p>
        """),
    ),
    (
        "Getting Started with Python: A Beginner's Guide",
        "Coding",
        ["Python", "BeginnerFriendly", "DataScience"],
        8,
        (
            "Python has become one of the most popular programming languages in the "
            "world. Learn what makes it special and write your first program today."
        ),
        textwrap.dedent("""\
            <p>Python's clean syntax reads almost like plain English, which makes it the
            ideal first language for students of any age.</p>
        """),
    ),
    (
        "Introduction to Robotics",
        "Robotics",
        ["Robotics", "BeginnerFriendly", "ProjectIdeas"],
        6,
        (
            "Get started with robotics programming and learn how to control sensors "
            "and motors with beginner-friendly code examples."
        ),
        textwrap.dedent("""\
            <p>Robotics combines electronics, mechanics, and software into a single
            fascinating discipline.</p>
        """),
    ),
    (
        "Stay Safe Online: Cybersecurity Basics",
        "Cybersecurity",
        ["Security", "BeginnerFriendly"],
        5,
        (
            "Learn essential cybersecurity practices to protect yourself and your "
            "data online."
        ),
        textwrap.dedent("""\
            <p>Every connected device is a potential entry point for attackers.</p>
        """),
    ),
    (
        "Design Thinking for Problem Solvers",
        "Design",
        ["BeginnerFriendly", "ProjectIdeas"],
        4,
        (
            "Discover how design thinking methodology helps you tackle real-world "
            "problems creatively."
        ),
        textwrap.dedent("""\
            <p>Design thinking is a human-centred approach to innovation.</p>
        """),
    ),
    (
        "Python for Data Science: Getting Started",
        "Data Science",
        ["Python", "DataScience", "MachineLearning"],
        7,
        (
            "Explore Python fundamentals and start working with data science libraries."
        ),
        textwrap.dedent("""\
            <p>Python has become the lingua franca of data science.</p>
        """),
    ),
    (
        "Building a Smart Bot with AI",
        "Robotics",
        ["Robotics", "AI", "Python"],
        9,
        (
            "Combine robotics and artificial intelligence to create an intelligent bot."
        ),
        textwrap.dedent("""\
            <p>An autonomous bot that learns from its environment sounds futuristic.</p>
        """),
    ),
    (
        "JavaScript Basics for Beginners",
        "Coding",
        ["JavaScript", "WebDev", "BeginnerFriendly"],
        5,
        (
            "Master the fundamentals of JavaScript and start building interactive "
            "web experiences."
        ),
        textwrap.dedent("""\
            <p>JavaScript is the only programming language that runs natively in every
            web browser.</p>
        """),
    ),
    (
        "Protecting Your Digital Identity",
        "Cybersecurity",
        ["Security", "BeginnerFriendly"],
        5,
        (
            "Your digital identity is more valuable than you think."
        ),
        textwrap.dedent("""\
            <p>Identity theft affects millions of people every year.</p>
        """),
    ),
    (
        "CSS Grid and Flexbox: A Practical Guide",
        "Design",
        ["CSS", "WebDev", "BeginnerFriendly"],
        6,
        (
            "Stop fighting CSS layouts. Learn when to use Grid versus Flexbox."
        ),
        textwrap.dedent("""\
            <p>Two layout systems dominate modern CSS: Flexbox and Grid.</p>
        """),
    ),
    (
        "Machine Learning Fundamentals for Students",
        "Data Science",
        ["MachineLearning", "Python", "AI", "DataScience"],
        10,
        (
            "Demystify machine learning and build your first classifier."
        ),
        textwrap.dedent("""\
            <p>Machine learning isn't magic — it's maths and data.</p>
        """),
    ),
    (
        "How to Land Your First Tech Internship",
        "Career Tips",
        ["BeginnerFriendly", "ProjectIdeas"],
        6,
        (
            "A practical guide to getting your first internship in tech."
        ),
        textwrap.dedent("""\
            <p>You don't need a degree or years of experience to land your first
            tech internship.</p>
        """),
    ),
]

NEWSLETTER_EMAILS = [
    "student1@example.com",
    "student2@example.com",
    "parent@example.com",
    "teacher@techschool.edu",
    "curious@learner.io",
]


def seed_blog(flush=False, posts_count=None, no_subscribers=False):
    logs = []

    def ok(msg):
        logs.append(f"✔ {msg}")

    def info(msg):
        logs.append(f"• {msg}")

    def warn(msg):
        logs.append(f"⚠ {msg}")

    if posts_count is None:
        posts_count = len(POSTS)

    posts_count = min(posts_count, len(POSTS))

    if flush:
        logs.append("[flush] Removing existing blog data...")
        BlogPost.objects.all().delete()
        AuthorProfile.objects.all().delete()
        User.objects.filter(first_name__in=[a["first_name"] for a in AUTHORS]).delete()
        Category.objects.all().delete()
        Tag.objects.all().delete()
        NewsletterSubscriber.objects.all().delete()
        ok("Existing blog data removed.")

    # Categories
    categories = {}
    logs.append("[1/5] Categories")
    for name in CATEGORIES:
        obj, created = Category.objects.get_or_create(
            slug=slugify(name),
            defaults={"name": name},
        )
        categories[name] = obj
        ok(f"{'Created' if created else 'Exists'} → {name}")

    # Tags
    tags = {}
    logs.append("[2/5] Tags")
    for name in TAGS:
        obj, created = Tag.objects.get_or_create(
            slug=slugify(name),
            defaults={"name": name},
        )
        tags[name] = obj
        ok(f"{'Created' if created else 'Exists'} → #{name}")

    # Authors
    authors = {}
    logs.append("[3/5] Authors")
    for data in AUTHORS:
        user, user_created = User.objects.get_or_create(
            first_name=data["first_name"],
            defaults={
                "first_name": data["first_name"],
                "last_name": data["last_name"],
                "email": data["email"],
                "is_staff": True,
            },
        )

        if user_created:
            user.set_password("techxagon2024!")
            user.save()

        _, profile_created = AuthorProfile.objects.get_or_create(
            user=user,
            defaults={
                "title": data["title"],
                "bio": data["bio"],
            },
        )

        ok(f"{'Created' if user_created else 'Exists'} → {user.get_full_name()} ({data['title']})")
        if user_created:
            info("Password set to: techxagon2024!")

        authors[data["first_name"]] = user

    # Posts
    logs.append(f"[4/5] Blog Posts (creating up to {posts_count})")
    author_list = list(authors.values())
    now = timezone.now()
    post_data = POSTS[:posts_count]

    for index, (title, cat_name, tag_names, read_time, excerpt, content) in enumerate(post_data):
        slug = slugify(title)

        if BlogPost.objects.filter(slug=slug).exists():
            warn(f"Skipped (exists) → {title}")
            continue

        days_ago = index * (90 // max(len(post_data), 1))
        published_at = now - timedelta(days=days_ago)
        is_featured = index == 0
        author = author_list[index % len(author_list)]

        post = BlogPost.objects.create(
            title=title,
            slug=slug,
            excerpt=excerpt,
            content=content,
            category=categories.get(cat_name),
            author=author,
            read_time=read_time,
            is_featured=is_featured,
            is_published=True,
            published_at=published_at,
        )

        for tag_name in tag_names:
            if tag_name in tags:
                post.tags.add(tags[tag_name])

        ok(f"Created → {title}{' ⭐ featured' if is_featured else ''}")

    # Subscribers
    if not no_subscribers:
        logs.append("[5/5] Newsletter Subscribers")
        for email in NEWSLETTER_EMAILS:
            _, created = NewsletterSubscriber.objects.get_or_create(email=email)
            ok(f"{'Created' if created else 'Exists'} → {email}")

    logs.append("✅ Seeding complete.")

    return logs