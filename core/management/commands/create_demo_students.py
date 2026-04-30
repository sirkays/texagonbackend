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

        # Full Student Python List (268 Names)

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
            "ALABI PEARL",
            "ANYAWU CHUKWUEMEKA",
            "ENOCK PROMISE",
            "UMUOKORO DAVID",
            "JERRYMIAH NDUKA HENRY",
            "JOHN LENAFI",
            "KANU GODSWILL",
            "LIKITA HAPPINESS",
            "KARIS MOMOH",
            "MICHAEL MAXWELL",
            "OBI CHIMAMANDA",
            "OGBE RACHAEL",
            "OGBUEHI MUNACHI",
            "OMALE EMMANUELLA PRINCESS",
            "ONYIA CHIMALIZU",
            "ODIDIKA MARIO",
            "UMUOKORO DAVID",
            "ZEWIGBO CHIMAMANDA",
            "ZISAN RAYMOND ELIZABETH",
            "AFAMDI SHECHINA",
            "BAKE DOMINION EMMANUEL",
            "CHUKWUEMEKA EMMANUEL",
            "DAVID AKPA CHUKWUBUIKE",
            "EDWIN EBUBE GRACE",
            "EMUOBOME ABUNDANCE",
            "ENO ALLILUYEVA CHICHETARAM",
            "MOGO JEFFREY",
            "NNEJI DIANA CHINOYEROM",
            "NWAJIDEOBI SAMUEL",
            "OBUMA MIRACLE AJAH",
            "ONAH-CHIGOZIRIM ANTHONIA",
            "ORJI CHMAMANDA",
            "PEREZ UCHECHUKWU",
            "SAMBA LYNN SHETTIMA",
            "SAMUEL CHIKAMJI",
            "SAMUELS INIOLUWA ISREAL",
            "UDEH JUDITH NKECHINYEREM",
            "EZIKE CHIBUNDU",
            "ANYIKIMBA GREAT DAVID",
            "AKAMISOKO TIMOTHY",
            "EMMANUEL VICTOR",
            "AGBADASLA VICTOR",
            "AGBOR N. BLOSSOM",
            "AINA MOFIYINFOLUWA SHEM",
            "CHINECHEM ONYEBUCHI",
            "ENYI KAYLA ENENE",
            "IDANG EXCELLENCE",
            "NAETONNA SAMUEL OHAETO",
            "NNODU CHRISTOPHER",
            "OBIORA DANIEL",
            "OKAFOR CHIDIOGO",
            "OMANEBU DESTINY ONYEDIKACHI",
            "OMOROJE GREATNESS",
            "ONYEKA-NONYELUM CHUKWUAKALISIA M.",
            "OSENI IRETOMIWA JOAN",
            "THOMAS DEFENCE DALONG",
            "UBAKA VICTORIA",
            "DANIEL ALEGEBLEYE",
            "ALOMAJA JONATHAN",
            "REX AKAISA UKEME",
            "ABRAHAM VICTOR BENSON",
            "JOSHUA DIVINE ZARA",
            "PHILIP SONNY AQUAOWO",
            "AKAMISOKO MARY SOKOJINUWON",
            "ONYEMACHI GIFT",
            "DAVID IDONYE",
            "ANIKAH MAURICE",
            "AGADA O BRIGHT",
            "AHUMIBE KELECHI DAVID",
            "ANYANWA GREAT",
            "AJUESHI THERESA E.",
            "BOSAH RAMSIY",
            "CHIMDI-LAMBERTS MIRACLE",
            "DAVID ONYENABAGHA",
            "EBOH AMARACHI",
            "EWURUM MELTON ITTE",
            "EROMOSER CHARIS",
            "NNAJI JOCHEBED AKACHUKWU",
            "JAMES FLOURISH CHIEMERIE",
            "MELETE DANIEL",
            "HILLARY NELLY MMASICHUKWU",
            "ADIELE MICHELLE UCHECHI",
            "KEFAS CHAT KEREN-HAPPUCH",
            "SULE CHRSTIANA OJIMAOJO",
            "UDOKA NMASICHUKWU FAITH",
            "ETOR BLISS SOKOYAME",
            "VICTOR ANITA",
            "OLANIPEKUN EMMANUELLE PRAISE",
            "BAMIGBOYE TREASURE O.A",
            "MATELU FORTUNE",
            "AMEH OFEDO-OJO FAITH",
            "OBASI IFEOMA C.S.",
            "UDOCHUKWU EXCEL M.",
            "OKE ELIJAH JOHN",
            "EWENIKE FAVOUR",
            "OYENEGBAHA DAVID",
            "CHUKWUMA BLESSED",
            "AWESON EMUOBOME",
            "BASSEY JEREMIAH",
            "DAUDA GOODHEART NUMSHU",
            "DENNIS DENNISON",
            "DANJUMA ESTHER T.",
            "ELIZABETH FRANCI",
            "ENENCHE VICTORY MIRACLE",
            "EJIM CHILETARAM EDEL",
            "IFEZUE KOSISO",
            "JOSHUA AGBADASOLA",
            "OKONKWO CANDEL",
            "OGUBZE ESTHER FAVOUR KANSIYOCHI",
            "OKOROEGBE AMARACHI GLORIA",
            "OGBU FRANKLYN IZUOMACHI",
            "MADUEKWE GODSWILL",
            "WILSON GAD",
            "MBAH MICHAEL CHIAGOZIE",
            "AMEH JOSHUA",
            "VICTOR JOVITA",
            "QUADRI MESSA",
            "ALEXANDER EMMANUEL CLETUS",
            "ODIDIKAN SMITH",
            "OCHEME PRAISE OCHE",
            "NNABUEZE EMMANUEL N.",
            "MIRANDA OGEYI OTACHE",
            "AGBADASOLA T. JAMES",
            "ACHI UWANNA",
            "ESSIEN DIVINE E.",
            "IFEANYI CHISOM EMILY",
            "IJEOMAH MITCHELLE C.",
            "JOHN JAMES",
            "MOMOH VICTORY",
            "OHABUIRO CHIDIMMA PEACE",
            "NNEJI DAVID C.",
            "OBIORA EUNICE C.",
            "OBINNA NICODEMUS OGBU",
            "OKOYE RAPHAEL",
            "OYINLOLA OLASUBOMI D",
            "USIFOH VICTORY",
            "UGWU VICTORY",
            "FELIX PEACE",
            "SUNDAY ANGEL C.",
            "DANIEL CHIKAMSO",
            "CHIMOBI JAMES DESTINY",
            "ELIZABETH FRANCIS",
            "UDO STELLA EMMANUEL",
            "PRECIOUS JERRY",
            "SIYAKA DANIEL",
            "AHARANWA ANETOCHUKWU DAVID",
            "AYODEJI AYOMIDE",
            "AYO-OLA VALERIE",
            "Nwaokoro Chukwuebuka Favour",
            "JAMES EDIOMO JOSEPH",
            "CHIMDI-LAMBERTS CHIKAIMA DEBORAH",
            "MOFIKOYA IFEOLUWA",
            "OJUDUN DANIEL",
            "OSENI VICTORIA",
            "OLANIPEKUN GABRIELLE",
            "OYESINA GIDEON",
            "JOSEPH ALEGELEYE",
            "CHIEMEKALUM VICTORY CHIKAMSO",
            "JIBRIN SONIA",
            "KASSABIAN JOHN",
            "NWANKWO DIVINE",
            "NYOM GRACE",
            "OGIJI REGINA",
            "OMANEBA HAPPINESS CHIKAMSO",
            "SOMTOCHUKWU EZE",
            "OZOVEHE ZENITH",
            "FRANCIS D. PATRICK",
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