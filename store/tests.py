from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from decimal import Decimal
from store.models import Order, Payment, Ticket

User = get_user_model()

class TicketSystemTests(TestCase):
    def setUp(self):
        # Create user accounts
        self.user1 = User.objects.create_user(
            username="customer1@example.com",
            email="customer1@example.com",
            password="testpassword123",
            first_name="Alice"
        )
        self.user2 = User.objects.create_user(
            username="customer2@example.com",
            email="customer2@example.com",
            password="testpassword123",
            first_name="Bob"
        )
        
        # Create an order for user1
        self.order1 = Order.objects.create(
            user=self.user1,
            status=Order.Status.PENDING,
            subtotal=Decimal("15000.00"),
            grand_total=Decimal("15000.00")
        )
        
        # Create a payment for user1's order
        self.payment1 = Payment.objects.create(
            order=self.order1,
            provider=Payment.Provider.PAYSTACK,
            status=Payment.Status.CAPTURED,
            amount=Decimal("15000.00"),
            provider_ref="pstk_ref_123456"
        )
        
        # Create an order for user2
        self.order2 = Order.objects.create(
            user=self.user2,
            status=Order.Status.PENDING,
            subtotal=Decimal("8000.00"),
            grand_total=Decimal("8000.00")
        )

    def test_ticket_model_creation(self):
        """Test that a Ticket instance can be created and has correct defaults."""
        ticket = Ticket.objects.create(
            user=self.user1,
            ticket_type=Ticket.TicketType.ORDER,
            order=self.order1,
            subject="Damaged Item",
            description="The product arrived with a cracked screen.",
            priority=Ticket.Priority.HIGH
        )
        self.assertEqual(ticket.user, self.user1)
        self.assertEqual(ticket.ticket_type, Ticket.TicketType.ORDER)
        self.assertEqual(ticket.order, self.order1)
        self.assertEqual(ticket.payment, None)
        self.assertEqual(ticket.subject, "Damaged Item")
        self.assertEqual(ticket.status, Ticket.Status.OPEN)
        self.assertEqual(ticket.priority, Ticket.Priority.HIGH)
        self.assertIn("Ticket #", str(ticket))
        self.assertIn("Damaged Item", str(ticket))

    def test_profile_page_tickets_tab_get(self):
        """Test GET request to tickets tab on profile page."""
        self.client.login(username="customer1@example.com", password="testpassword123")
        response = self.client.get(reverse("store_frontend:profile"), {"tab": "tickets"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Support Tickets")
        # Check pre-filled inputs context variables
        self.assertEqual(response.context["active_tab"], "tickets")
        self.assertEqual(list(response.context["tickets"]), [])
        self.assertEqual(response.context["selected_order_id"], "")

    def test_profile_page_tickets_tab_prefilled_get(self):
        """Test GET request to tickets tab with pre-filled query parameters."""
        self.client.login(username="customer1@example.com", password="testpassword123")
        # Request with order_id and new=true
        response = self.client.get(reverse("store_frontend:profile"), {
            "tab": "tickets",
            "new": "true",
            "order_id": str(self.order1.id)
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_order_id"], str(self.order1.id))
        self.assertEqual(response.context["show_new_form"], True)

    def test_submit_ticket_success(self):
        """Test submitting a ticket successfully via POST."""
        self.client.login(username="customer1@example.com", password="testpassword123")
        post_data = {
            "action": "create_ticket",
            "ticket_type": "order",
            "order_id": str(self.order1.id),
            "priority": "high",
            "subject": "Missing items",
            "description": "One of the items in my order was not in the box."
        }
        response = self.client.post(reverse("store_frontend:profile"), post_data)
        
        # Verify redirect back to tickets tab
        self.assertRedirects(response, reverse("store_frontend:profile") + "?tab=tickets")
        
        # Check that Ticket was created in DB
        ticket = Ticket.objects.get(user=self.user1)
        self.assertEqual(ticket.ticket_type, Ticket.TicketType.ORDER)
        self.assertEqual(ticket.order, self.order1)
        self.assertEqual(ticket.subject, "Missing items")
        self.assertEqual(ticket.description, "One of the items in my order was not in the box.")
        self.assertEqual(ticket.priority, Ticket.Priority.HIGH)

    def test_submit_ticket_invalid_order_ownership(self):
        """Test that user cannot submit a ticket for an order they do not own."""
        self.client.login(username="customer1@example.com", password="testpassword123")
        post_data = {
            "action": "create_ticket",
            "ticket_type": "order",
            "order_id": str(self.order2.id), # order2 belongs to user2
            "priority": "medium",
            "subject": "Unauthorized report",
            "description": "Trying to report someone else's order."
        }
        response = self.client.post(reverse("store_frontend:profile"), post_data)
        # Should redirect back to tickets form with error
        self.assertRedirects(response, reverse("store_frontend:profile") + "?tab=tickets&new=true")
        
        # Confirm no ticket was created
        self.assertEqual(Ticket.objects.count(), 0)

    def test_submit_ticket_invalid_payment_ownership(self):
        """Test that user cannot submit a ticket for a payment transaction they do not own."""
        # Create a payment for user2's order
        payment2 = Payment.objects.create(
            order=self.order2,
            provider=Payment.Provider.FLUTTERWAVE,
            status=Payment.Status.CAPTURED,
            amount=Decimal("8000.00")
        )
        
        self.client.login(username="customer1@example.com", password="testpassword123")
        post_data = {
            "action": "create_ticket",
            "ticket_type": "payment",
            "payment_id": str(payment2.id), # payment2 belongs to user2
            "priority": "medium",
            "subject": "Unauthorized transaction report",
            "description": "Trying to report someone else's payment."
        }
        response = self.client.post(reverse("store_frontend:profile"), post_data)
        # Should redirect back to tickets form with error
        self.assertRedirects(response, reverse("store_frontend:profile") + "?tab=tickets&new=true")
        
        # Confirm no ticket was created
        self.assertEqual(Ticket.objects.count(), 0)

    def test_get_ticket_detail_view(self):
        """Test GET request to a specific ticket conversation thread."""
        ticket = Ticket.objects.create(
            user=self.user1,
            ticket_type=Ticket.TicketType.GENERAL,
            subject="Question about pricing",
            description="Is shipping free for members?",
        )
        self.client.login(username="customer1@example.com", password="testpassword123")
        response = self.client.get(reverse("store_frontend:profile"), {
            "tab": "tickets",
            "ticket_id": str(ticket.id)
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_ticket"], ticket)
        self.assertContains(response, "Question about pricing")
        self.assertContains(response, "Is shipping free for members?")

    def test_reply_ticket_success(self):
        """Test user successfully replying to a ticket."""
        ticket = Ticket.objects.create(
            user=self.user1,
            ticket_type=Ticket.TicketType.GENERAL,
            subject="Question",
            description="Desc",
        )
        self.client.login(username="customer1@example.com", password="testpassword123")
        post_data = {
            "action": "reply_ticket",
            "ticket_id": str(ticket.id),
            "message": "This is my follow-up reply."
        }
        response = self.client.post(reverse("store_frontend:profile"), post_data)
        self.assertRedirects(response, reverse("store_frontend:profile") + f"?tab=tickets&ticket_id={ticket.id}")
        
        # Verify message created
        from store.models import TicketMessage
        msg = TicketMessage.objects.get(ticket=ticket)
        self.assertEqual(msg.sender, self.user1)
        self.assertEqual(msg.message, "This is my follow-up reply.")
        self.assertEqual(msg.is_admin, False)

    def test_reply_reopens_resolved_ticket(self):
        """Test that replying to a resolved ticket changes status back to open."""
        ticket = Ticket.objects.create(
            user=self.user1,
            ticket_type=Ticket.TicketType.GENERAL,
            subject="Question",
            description="Desc",
            status=Ticket.Status.RESOLVED
        )
        self.client.login(username="customer1@example.com", password="testpassword123")
        post_data = {
            "action": "reply_ticket",
            "ticket_id": str(ticket.id),
            "message": "Reopen please."
        }
        self.client.post(reverse("store_frontend:profile"), post_data)
        
        # Check status reopened
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, Ticket.Status.OPEN)

    def test_get_unauthorized_ticket_detail(self):
        """Test that user cannot view detail page of another user's ticket."""
        # Create ticket for user2
        ticket2 = Ticket.objects.create(
            user=self.user2,
            ticket_type=Ticket.TicketType.GENERAL,
            subject="Private ticket",
            description="Secret details",
        )
        # Login as user1
        self.client.login(username="customer1@example.com", password="testpassword123")
        response = self.client.get(reverse("store_frontend:profile"), {
            "tab": "tickets",
            "ticket_id": str(ticket2.id)
        })
        # Should return 404 since user1 doesn't own it
        self.assertEqual(response.status_code, 404)

    def test_reply_unauthorized_ticket(self):
        """Test that user cannot reply to another user's ticket."""
        ticket2 = Ticket.objects.create(
            user=self.user2,
            ticket_type=Ticket.TicketType.GENERAL,
            subject="Private ticket",
            description="Secret details",
        )
        self.client.login(username="customer1@example.com", password="testpassword123")
        post_data = {
            "action": "reply_ticket",
            "ticket_id": str(ticket2.id),
            "message": "Hack reply."
        }
        response = self.client.post(reverse("store_frontend:profile"), post_data)
        # Should redirect with error (or return 404 in try/except)
        self.assertRedirects(response, reverse("store_frontend:profile") + "?tab=tickets")
        
        # Ensure no message created
        from store.models import TicketMessage
        self.assertEqual(TicketMessage.objects.count(), 0)

