# academics/management/commands/create_demo_students.py

import re
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from orgs.models import Organization, OrganizationMembership
from billing.models import UserAccountSubscription, SubscriptionPlan
from academics.models import StudentProfile


User = get_user_model()


class Command(BaseCommand):
    help = "Create demo student accounts with active subscriptions"

    def add_arguments(self, parser):
        parser.add_argument(
            "--org",
            type=int,
            required=True,
            help="Organization PK",
        )

        parser.add_argument(
            "--password",
            type=str,
            default="Demo@123",
            help="Default password for all students",
        )

    def handle(self, *args, **options):
        org_pk = options["org"]
        default_password = options["password"]

        try:
            organization = Organization.objects.get(pk=org_pk)
        except Organization.DoesNotExist:
            raise CommandError(f"Organization with pk={org_pk} does not exist.")

        try:
            plan = SubscriptionPlan.objects.get(pk=2)
        except SubscriptionPlan.DoesNotExist:
            raise CommandError("SubscriptionPlan with pk=2 does not exist.")

        students = [
            "AKAMISOKO DIVINE",
            "AFAMDI SHALLOM",
            "ANIKOH MELVIN",
            "BAYO NICOLE",
            "BENSON WEALTH",
            "BEULAH JOSEPH",
            "CHUKWUNONSO AKPA EMMANUEL",
            "CHIZARAM UGOUCHUKWU",
            "CHIMAMANDA UMEOBI",
            "LYDIA DONALD DAVID",
            "ESTHER UWEM JOSEPH",
            "EDWIN BLESSING",
            "FAVOUR LAWRENCE",
            "HONOUR EJIM",
            "KAMSY FRANK UNOGU",
            "KAMSIYOCHUKWU UMEOBI",
            "IFECHUKWU UGWU GLORY",
            "GIDEON OGBONNA",
            "JEDIDIAH AJEWOLE",
            "NEEYUM GEORGE",
            "PRAISE CHINEDU OLUNEMEREM",
            "UBAKA EMMANUELLA CHIOMA",
            "PETER-ABU RAYKING OKAOJO",
            "OKEUDO JESSE CHUKWUEMEKA",
            "OVIE ESOMU PAUL",
            "AFAOWU JOYLYN",
            "AHUMBE NNAEMEKA",
            "ANNIE IHUOMA E.",
            "ADORA ANAGA",
            "ABEZE LAURA",
            "AGU VICTOR CHIDIEBUBE",
            "ZIENACHIMKA BLOSSOM SAMUEL",
            "AJAEGBU CHIKAYIMA PRAISE",
            "CHINAKA FAITH",
            "IFEOLUWA T. SAMUEL",
            "KASSABIAN JANET AKABI",
            "RICHARD CHIMAMANDA AVERE",
            "ALEGBELEYE MICHEAL PAUL",
            "SUCCESS IFEANYI",
            "UCHEAGA EMMANUELLA",
            "URAH DELIGHT",
            "UCHECHUKWU K. ODIRICHUKWU",
            "UGWULOR DEBORAH",
            "NGWU KAMSIYOCHUKWU",
            "JESSICA JEREMIAH",
            "MAYONMI FAVOUR",
            "OHABUIRO CHIDERA",
            "AFOLABI IREMIDE PRAISE",
            "OBASI CHINONSO",
            "ABDULKHALQ ABUBAKAR ALIYU",
            "ORAKPO CHIMAMANDA",
            "BONIFACE CHEKWUBE",
            "AKAEGBU KIARA",
            "ALEX AZUBIKE",
            "BERNARD VICTORY",
            "CHIMDI SAMUEL N.",
            "DESMOND EMMANUELLA",
            "DOUGLAS DOMINION",
            "EREKE JOVAN FELIX",
            "IHOTU PAUL- IFENNE BENEDICTA",
            "ELISHA ELIEL P.",
            "IWARVE JOSHUA O.",
            "JOHN DAVID-PRAISE",
            "NWACHUKWU MARVEVELLOUS",
            "ETOH BLESSED SOKUDILE",
            "MARTIN-DURU HEPHZIBAH CHIDUBEM",
            "OKONKWO FAVOUR",
            "ONUH COLLINS",
            "MCKIZITO VANESSA",
            "ONYEKWERE VICTORIA",
            "CHIDI MIRACLE",
            "UMAGBAI PAULA",
            "UZOAGU CALEB",
            "ISRAEL OCHEME",
            "MADUMERE GRATEFUL NMESOMA",
            "PENIEL AMUTA",
            "ACHUAGU KOSISOCHKWU PASCALINE",
            "AKAMISOKO KING DAVID",
            "ADUKU DANIEL DIVINE OMAOJO",
            "DANIEL OBENI OHIWERA RYAN",
            "EDET FAMOUS HOPE",
            "IVANA O. AGADA",
            "EKPANG SOPHIA",
            "EZE EMEKA GLORIA CHINAZA",
            "FIDEMILA JOHN",
            "UDOKA CHIDMMA PRAISE",
            "UCHE EZE CHIMAMANDA",
            "FAITH MICHAEL",
            "KALIO IBIYE SOTONYE",
            "NWANKWO COVENANT",
            "OBI CHIDIEBUBE FAVOUR",
            "OBINNA CHIMUANYA TEHILA",
            "OKE VICTOR",
            "PAUL SUNDAY",
            "UMUKORO DAUWA PRECIOUS",
            "UNDELIKWO U. DANIELLA",
            "DANIEL SAMSON EBUBECHUKWU",
            "EROMOSELE JESSE",
            "ENYE NZUBE",
            "AFAGWU DIVINE",
            "EHIS ANGEL",
        ]

        created_count = 0
        skipped_count = 0
        failed_count = 0

        for full_name in students:
            full_name = full_name.strip()

            if not full_name:
                continue

            name_parts = full_name.split()

            if len(name_parts) < 2:
                self.stdout.write(
                    self.style.WARNING(f"Skipping invalid name: {full_name}")
                )
                skipped_count += 1
                continue

            first_name = name_parts[0].title()
            last_name = " ".join(name_parts[1:]).title()

            clean_name = re.sub(r"[^a-zA-Z0-9]", "", full_name).lower()
            email = f"{clean_name}demo@learn.techxagonacademy.com"

            if User.objects.filter(email=email).exists():
                self.stdout.write(
                    self.style.WARNING(f"User already exists: {email}")
                )
                skipped_count += 1
                continue

            try:
                with transaction.atomic():
                    user = User.objects.create_user(
                        email=email,
                        password=default_password,
                        first_name=first_name,
                        last_name=last_name,
                        is_active=True,
                        is_generated=True,
                        primary_org=organization,
                    )

                    StudentProfile.objects.create(
                        user=user,
                        organization=organization,
                    )

                    OrganizationMembership.objects.create(
                        user=user,
                        organization=organization,
                        role=OrganizationMembership.Role.STUDENT,
                        is_active=True,
                    )

                    start_at = timezone.now()
                    end_at = start_at + timedelta(days=30)

                    UserAccountSubscription.objects.create(
                        organization=organization,
                        user=user,
                        plan=plan,
                        status=UserAccountSubscription.Status.ACTIVE,
                        start_at=start_at,
                        end_at=end_at,
                        auto_renew=True,
                        amount=Decimal("0.00"),
                        currency="NGN",
                    )

                created_count += 1

                self.stdout.write(
                    self.style.SUCCESS(f"Created: {full_name} -> {email}")
                )

            except Exception as e:
                failed_count += 1
                self.stdout.write(
                    self.style.ERROR(f"Failed creating {full_name}: {str(e)}")
                )

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"DONE. Created={created_count}, Skipped={skipped_count}, Failed={failed_count}"
            )
        )