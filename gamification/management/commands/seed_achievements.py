# achievements/management/commands/seed_achievements.py
from django.core.management.base import BaseCommand
from orgs.models import Organization
from gamification.models import AchievementDefinition, Badge

class Command(BaseCommand):
    help = "Seed default achievements and badges"

    def handle(self, *args, **kwargs):
        org = None  # global defaults; or pick an Organization

        defaults = [
            # code, title, description, icon, category, target_value, points
            ("first_steps", "First Steps", "Complete your first lesson", "star", "Getting Started", None, 50),
            ("code_warrior", "Code Warrior", "Complete 10 coding exercises", "trophy", "Coding", 10, 200),
            ("quiz_master", "Quiz Master", "Score 90% or higher on N quizzes", "target", "Assessment", 5, 300),
            ("streak_champion", "Streak Champion", "Maintain a N-day learning streak", "zap", "Consistency", 30, 500),
            ("course_conqueror", "Course Conqueror", "Complete N full courses", "award", "Completion", 3, 750),
        ]
        for code, title, desc, icon, cat, target, pts in defaults:
            AchievementDefinition.objects.update_or_create(
                code=code,
                defaults=dict(
                    organization=org, title=title, description=desc,
                    icon=icon, category=cat, target_value=target, points=pts, is_active=True
                ),
            )

        badges = [
            ("Bronze Learner", 1000,  "medal",  "bg-amber-600"),
            ("Silver Scholar", 5000,  "trophy", "bg-gray-400"),
            ("Gold Graduate",  10000, "crown",  "bg-yellow-500"),
            ("Diamond Elite",  25000, "gem",    "bg-blue-500"),
        ]
        for name, pts, icon, color in badges:
            Badge.objects.update_or_create(
                name=name,
                defaults=dict(points=pts, icon_name=icon, color=color, criteria=f"Earn {pts:,} points")
            )

        self.stdout.write(self.style.SUCCESS("Seeded achievements and badges"))
