from django.utils.text import slugify

from projects.models import ProjectCategory, ProjectTag, StudentProject

CATEGORIES = [
    ("Web Development", "web-development", "red"),
    ("Robotics", "robotics", "orange"),
    ("Data Science", "data-science", "blue"),
    ("Cybersecurity", "cybersecurity", "purple"),
    ("UI/UX Design", "ui-ux-design", "pink"),
    ("Programming", "programming", "red"),
]

TAGS = [
    "HTML", "CSS", "JavaScript", "Python", "Arduino",
    "Scratch", "Figma", "Data Analysis", "Line Follower",
    "Sensor", "React", "Flask",
]

PROJECTS = [
    dict(
        title="School Club Website",
        subtitle="A modern responsive site for a secondary school coding club",
        excerpt="A clean, responsive website built from scratch with HTML, CSS, and JavaScript — featuring an event calendar, member showcase, and contact form.",
        description="""
<p>This project was built by a JSS3 student as part of the Web Development track. The goal was to create a real online presence for their school's coding club.</p>
<h2>What was built</h2>
<ul>
  <li>Fully responsive landing page using HTML5 and CSS Grid</li>
  <li>JavaScript-powered event calendar</li>
  <li>Member showcase grid with hover effects</li>
  <li>Contact form with basic validation</li>
</ul>
<h2>Key learnings</h2>
<p>The student learned how to structure a professional webpage, implement responsive layouts without a framework, and use the DOM to add interactivity. The project was deployed to GitHub Pages.</p>
<pre><code>&lt;!-- Responsive grid with CSS only --&gt;
.members-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 1.5rem;
}</code></pre>
        """.strip(),
        category="Web Development",
        tags=["HTML", "CSS", "JavaScript"],
        difficulty="beginner",
        thumb_emoji="🌐",
        thumb_gradient="red-orange",
        student_name="Chidera Obi",
        student_school="Sacred Heart Seminary, Nsude",
        is_featured=True,
    ),
    dict(
        title="Line-Following Robot",
        subtitle="Autonomous robot that tracks a black line using IR sensors",
        excerpt="A two-wheel differential-drive robot that follows a black line on a white surface, built on Arduino Uno with dual IR sensors and a motor driver shield.",
        description="""
<p>Built during the Robotics track, this was a collaborative project involving two students. The robot uses two infra-red sensors to detect the boundary between a black line and white surface, adjusting motor speed to stay on course.</p>
<h2>Hardware used</h2>
<ul>
  <li>Arduino Uno microcontroller</li>
  <li>L298N dual H-bridge motor driver</li>
  <li>2× IR proximity sensors</li>
  <li>2× DC gear motors + chassis kit</li>
  <li>7.4V LiPo battery pack</li>
</ul>
<h2>How it works</h2>
<p>Each sensor returns a HIGH signal when it detects white and LOW over black. A simple decision tree in the loop function steers the robot: both sensors on white = go straight, left sensor on black = turn left, right sensor on black = turn right.</p>
<pre><code>void loop() {
  int leftVal  = digitalRead(LEFT_SENSOR);
  int rightVal = digitalRead(RIGHT_SENSOR);
  if (leftVal == HIGH && rightVal == HIGH) forward();
  else if (leftVal == LOW)  turnLeft();
  else if (rightVal == LOW) turnRight();
}</code></pre>
        """.strip(),
        category="Robotics",
        tags=["Arduino", "Sensor", "Line Follower"],
        difficulty="intermediate",
        thumb_emoji="🤖",
        thumb_gradient="orange-slate",
        student_name="Emeka Okafor",
        student_school="Emmanuel Anglican Secondary School",
        is_featured=True,
    ),
    dict(
        title="Student Performance Dashboard",
        subtitle="Data visualisation tool built with Python and Pandas",
        excerpt="An interactive dashboard that analyses class test scores, identifies struggling students, and produces clear charts for teacher decision-making.",
        description="""
<p>Created as part of the Data Science track, this project uses Python to process a real CSV dataset of mock exam results and produces a visual summary for teachers.</p>
<h2>Tools used</h2>
<ul>
  <li>Python 3 + Pandas for data processing</li>
  <li>Matplotlib + Seaborn for charts</li>
  <li>Jupyter Notebook for presentation</li>
</ul>
<h2>What the dashboard shows</h2>
<ul>
  <li>Class average per subject</li>
  <li>Individual student trend lines across terms</li>
  <li>Colour-coded performance heatmap</li>
  <li>Automated flag for students below 50%</li>
</ul>
<pre><code>import pandas as pd
import matplotlib.pyplot as plt

df = pd.read_csv("results.csv")
below_avg = df[df["score"] < 50]
print(f"{len(below_avg)} students need extra support")</code></pre>
        """.strip(),
        category="Data Science",
        tags=["Python", "Data Analysis"],
        difficulty="intermediate",
        thumb_emoji="📊",
        thumb_gradient="slate-red",
        student_name="Adaeze Nnaji",
        student_school="Mater Dei College",
        is_featured=True,
    ),
    dict(
        title="Password Strength Checker",
        subtitle="A cybersecurity tool built in Python",
        excerpt="A command-line tool that rates password strength, detects common patterns, and suggests improvements — built as an introduction to cybersecurity coding.",
        description="""
<p>Part of the Cybersecurity track, this project challenges students to think like security professionals by analysing what makes a password weak or strong.</p>
<h2>Features</h2>
<ul>
  <li>Checks length, character variety, and common patterns</li>
  <li>Returns a score from 0–100 with a strength label</li>
  <li>Suggests specific improvements to the user</li>
  <li>Detects and warns against dictionary words</li>
</ul>
<pre><code>def check_strength(password):
    score = 0
    if len(password) >= 12: score += 25
    if any(c.isupper() for c in password): score += 25
    if any(c.isdigit() for c in password): score += 25
    if any(c in "!@#$%^&*()" for c in password): score += 25
    return score</code></pre>
        """.strip(),
        category="Cybersecurity",
        tags=["Python"],
        difficulty="beginner",
        thumb_emoji="🔐",
        thumb_gradient="blue-teal",
        student_name="Tochukwu Eze",
        student_school="Emmanuel Anglican Secondary School",
        is_featured=False,
    ),
    dict(
        title="School App UI Design",
        subtitle="High-fidelity mobile app mockup for a school timetable app",
        excerpt="A complete UI/UX design for a secondary school timetable app — from user research and wireframes to polished Figma mockups with a light and dark mode.",
        description="""
<p>This UI/UX project demonstrates the full design process: research, ideation, wireframing, and high-fidelity prototyping.</p>
<h2>Design process</h2>
<ol>
  <li><strong>Research</strong> — interviewed 5 classmates about their biggest school-day frustrations</li>
  <li><strong>Define</strong> — key problem: students forget which classroom to go to next</li>
  <li><strong>Wireframes</strong> — low-fidelity sketches on paper, then Figma wireframes</li>
  <li><strong>High-fidelity mockups</strong> — full colour system, typography, and component library</li>
  <li><strong>Prototype</strong> — interactive Figma prototype with navigation flows</li>
</ol>
<h2>Design decisions</h2>
<p>Chose a high-contrast colour palette to ensure readability in bright outdoor light. Used a bottom navigation bar (thumb-friendly) and large tap targets to reduce errors.</p>
        """.strip(),
        category="UI/UX Design",
        tags=["Figma"],
        difficulty="beginner",
        thumb_emoji="🎨",
        thumb_gradient="purple-pink",
        student_name="Blessing Uchenna",
        student_school="Sacred Heart Seminary, Nsude",
        is_featured=False,
    ),
]


def seed_projects(flush=False):
    logs = []

    def ok(msg):
        logs.append(f"✔ {msg}")

    def warn(msg):
        logs.append(f"⚠ {msg}")

    if flush:
        logs.append("[flush] Removing existing project data...")
        StudentProject.objects.all().delete()
        ProjectCategory.objects.all().delete()
        ProjectTag.objects.all().delete()
        ok("Cleared.")

    logs.append("[1/3] Categories")
    cats = {}
    for name, slug, colour in CATEGORIES:
        obj, created = ProjectCategory.objects.get_or_create(
            slug=slug,
            defaults={"name": name, "colour": colour},
        )
        cats[name] = obj
        ok(f"{'Created' if created else 'Exists'} → {name}")

    logs.append("[2/3] Tags")
    tags = {}
    for name in TAGS:
        obj, created = ProjectTag.objects.get_or_create(
            slug=slugify(name),
            defaults={"name": name},
        )
        tags[name] = obj
        ok(f"{'Created' if created else 'Exists'} → #{name}")

    logs.append("[3/3] Projects")
    for item in PROJECTS:
        data = item.copy()  # important: don't mutate the original dict
        slug = slugify(f"{data['title']}-{data['student_name']}")

        if StudentProject.objects.filter(slug=slug).exists():
            warn(f"Skipped (exists) → {data['title']}")
            continue

        tag_names = data.pop("tags")
        cat_name = data.pop("category")

        project = StudentProject.objects.create(
            slug=slug,
            category=cats.get(cat_name),
            is_published=True,
            **data,
        )

        for t in tag_names:
            if t in tags:
                project.tags.add(tags[t])

        flag = " ⭐ featured" if project.is_featured else ""
        ok(f"Created → {project.title}{flag}")

    logs.append("✅ Projects seeding complete.")
    return logs