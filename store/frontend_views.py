from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from django.contrib import messages
from django.db.models import F
from django.http import JsonResponse
from decimal import Decimal
from django.contrib.postgres.search import SearchVector, SearchQuery, SearchRank
from django.conf import settings
from django.contrib.auth import authenticate, login, logout, get_user_model
from django.core.mail import send_mail
import uuid as uuid_module
from .models import (
    Product, Cart, CartItem, StoreConfiguration, UserStoreProfile,
    SavedItem, Address, Order, OrderItem, Payment, BNPLPlanTemplate,
    BNPLAgreement, Coupon, Ticket, TicketMessage,
)
from .utils import is_coupon_usable, calc_discount
from accounts.models import EmailOTP
from .views import _get_or_create_cart
from billing.utils import generate_payment_link, confirm_transaction

User = get_user_model()

def store_home(request):
    products = Product.objects.filter(is_active=True).prefetch_related('images')
    
    category = request.GET.get('category')
    if category:
        if category == 'laptops':
            products = products.filter(product_type='laptop')
        elif category == 'robotics':
            # Assuming robotics falls under hardware or gadget, or we can just filter by title
            from django.db.models import Q
            products = products.filter(Q(product_type__in=['hardware', 'gadget']) | Q(title__icontains='robot') | Q(description__icontains='robot'))
        elif category == 'iot':
            products = products.filter(product_type='iot_device')
            
    context = {'products': products, 'current_category': category}
    return render(request, 'store/store_home.html', context)

def store_detail(request, slug):
    product = get_object_or_404(Product, slug=slug, is_active=True)
    # Get related products (just first 6 for now)
    related_products = Product.objects.filter(is_active=True).exclude(id=product.id)[:6]
    
    # Get current cart quantity for this product
    cart = _get_or_create_cart(request)
    cart_item = cart.items.filter(product=product).first()
    cart_qty = cart_item.quantity if cart_item else 1

    is_saved = False
    if request.user.is_authenticated:
        is_saved = SavedItem.objects.filter(user=request.user, product=product).exists()

    context = {
        'product': product, 
        'related_products': related_products, 
        'cart_qty': cart_qty,
        'is_saved': is_saved,
    }
    return render(request, 'store/store_detail.html', context)

def store_search(request):
    from django.db.models import Q
    from django.core.paginator import Paginator

    query = request.GET.get('q', '').strip()
    sort = request.GET.get('sort', 'best')
    min_price = request.GET.get('min_price', '').strip()
    max_price = request.GET.get('max_price', '').strip()
    product_type_filter = request.GET.get('product_type', '').strip()
    min_rating = request.GET.get('min_rating', '').strip()
    page_num = request.GET.get('page', 1)

    products = Product.objects.filter(is_active=True).prefetch_related('images')

    if query:
        terms = query.split()
        q_filter = Q()
        for term in terms:
            q_filter |= (
                Q(title__icontains=term) |
                Q(brand__icontains=term) |
                Q(description__icontains=term) |
                Q(product_type__icontains=term) |
                Q(category__name__icontains=term) |
                Q(features__icontains=term)
            )
        phrase_filter = (
            Q(title__icontains=query) |
            Q(brand__icontains=query) |
            Q(description__icontains=query) |
            Q(category__name__icontains=query)
        )

        # Try PostgreSQL FTS first, fall back to icontains if unavailable/empty
        try:
            fts_products = products.annotate(
                search=SearchVector('title', weight='A') +
                       SearchVector('brand', weight='B') +
                       SearchVector('category__name', weight='C') +
                       SearchVector('description', weight='D'),
                rank=SearchRank(
                    SearchVector('title', weight='A') +
                    SearchVector('brand', weight='B') +
                    SearchVector('category__name', weight='C') +
                    SearchVector('description', weight='D'),
                    SearchQuery(query)
                )
            ).filter(
                Q(search=SearchQuery(query)) | phrase_filter | q_filter,
                is_active=True
            ).distinct().order_by('-rank')

            if not fts_products.exists():
                raise ValueError("FTS empty")
            products = fts_products
        except Exception:
            products = products.filter(q_filter | phrase_filter).distinct()

    # --- Filters ---
    if product_type_filter:
        products = products.filter(product_type=product_type_filter)

    if min_price:
        try:
            products = products.filter(price__gte=float(min_price))
        except ValueError:
            pass

    if max_price:
        try:
            products = products.filter(price__lte=float(max_price))
        except ValueError:
            pass

    if min_rating:
        try:
            products = products.filter(rating__gte=float(min_rating))
        except ValueError:
            pass

    # --- Sorting ---
    if sort == 'price_asc':
        products = products.order_by('price')
    elif sort == 'price_desc':
        products = products.order_by('-price')
    elif sort == 'top_rated':
        products = products.order_by('-rating')
    elif sort == 'newest':
        products = products.order_by('-created_at')

    total_count = products.count()

    # --- Pagination: 20 per page ---
    paginator = Paginator(products, 20)
    try:
        page_obj = paginator.page(page_num)
    except Exception:
        page_obj = paginator.page(1)

    # Build a query string without 'page' so pagination links can append ?page=N
    get_params = request.GET.copy()
    get_params.pop('page', None)
    filter_querystring = get_params.urlencode()

    context = {
        'query': query,
        'products': page_obj,          # paginated products
        'page_obj': page_obj,
        'paginator': paginator,
        'total_count': total_count,
        'sort': sort,
        'min_price': min_price,
        'max_price': max_price,
        'product_type_filter': product_type_filter,
        'min_rating': min_rating,
        'product_types': Product.ProductType.choices,
        'filter_querystring': filter_querystring,
    }
    return render(request, 'store/store_search.html', context)


def store_add_to_cart(request):
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    if request.method == "POST":
        product_id = request.POST.get("product_id")
        qty_str = request.POST.get("quantity", "1").strip()
        try:
            qty = int(qty_str) if qty_str else 1
        except ValueError:
            qty = 1
            
        action = request.POST.get("action", "add")
        buy_now = request.POST.get("buy_now") == "true"

        if product_id:
            cart = _get_or_create_cart(request)
            product = get_object_or_404(Product, pk=product_id, is_active=True)
            item, created = CartItem.objects.get_or_create(
                cart=cart,
                product=product,
                defaults={"quantity": qty},
            )
            if not created:
                if action == "set":
                    item.quantity = qty
                else:
                    item.quantity = F("quantity") + qty
                item.save(update_fields=["quantity"])
            cart_count = cart.items.count()
            if is_ajax:
                return JsonResponse({
                    'success': True,
                    'message': f"'{product.title}' added to cart!",
                    'cart_count': cart_count,
                    'buy_now': buy_now,
                })
            if buy_now:
                return redirect('store_frontend:checkout')
    if is_ajax:
        return JsonResponse({'success': False, 'message': 'Invalid request'}, status=400)
    return redirect('store_frontend:cart')

def store_update_cart(request, item_id):
    if request.method == "POST":
        cart = _get_or_create_cart(request)
        item = get_object_or_404(CartItem, pk=item_id, cart=cart)
        action = request.POST.get("action")
        
        if action == "increase":
            item.quantity = F("quantity") + 1
            item.save(update_fields=["quantity"])
        elif action == "decrease":
            if item.quantity > 1:
                item.quantity = F("quantity") - 1
                item.save(update_fields=["quantity"])
            else:
                item.delete()
    return redirect('store_frontend:cart')

def store_remove_from_cart(request, item_id):
    if request.method == "POST":
        cart = _get_or_create_cart(request)
        item = get_object_or_404(CartItem, pk=item_id, cart=cart)
        item.delete()
        messages.info(request, "Item removed.")
    next_url = request.GET.get('next') or request.META.get('HTTP_REFERER') or reverse('store_frontend:cart')
    return redirect(next_url)

def store_cart(request):
    cart = _get_or_create_cart(request)
    config = StoreConfiguration.get_solo()

    items = cart.items.select_related('product').all()
    subtotal = sum((item.product.price * item.quantity for item in items))

    # Apply Shipping
    shipping_total = config.flat_shipping_rate if items else Decimal("0.00")

    # Apply Tax
    tax_total = (subtotal * config.tax_rate_percent / Decimal("100.0")).quantize(Decimal("0.01"))

    # Coupon / discount
    applied_coupon = cart.coupon if (cart.coupon and is_coupon_usable(cart.coupon)) else None
    discount_total = calc_discount(subtotal, applied_coupon)
    discounted_subtotal = (subtotal - discount_total).quantize(Decimal("0.01"))

    grand_total = (discounted_subtotal + shipping_total + tax_total).quantize(Decimal("0.01"))

    # BNPL — based on the post-discount grand total
    all_bnpl_eligible = all(item.product.bnpl_enabled for item in items)
    bnpl_installment = (grand_total / Decimal("4")).quantize(Decimal("0.01")) if (grand_total and all_bnpl_eligible) else None

    context = {
        'cart': cart,
        'items': items,
        'subtotal': subtotal,
        'discount_total': discount_total,
        'applied_coupon': applied_coupon,
        'shipping_total': shipping_total,
        'tax_total': tax_total,
        'tax_rate_percent': config.tax_rate_percent,
        'grand_total': grand_total,
        'bnpl_installment': bnpl_installment,
        'all_bnpl_eligible': all_bnpl_eligible,
    }
    return render(request, 'store/store_cart.html', context)


def store_apply_coupon(request):
    """Validate and attach a coupon code to the cart."""
    if request.method != 'POST':
        return redirect('store_frontend:cart')

    code = request.POST.get('coupon_code', '').strip().upper()
    if not code:
        messages.error(request, "Please enter a coupon code.")
        return redirect('store_frontend:cart')

    try:
        coupon = Coupon.objects.get(code__iexact=code)
    except Coupon.DoesNotExist:
        messages.error(request, f"Coupon \"{code}\" is not valid.")
        return redirect('store_frontend:cart')

    if not is_coupon_usable(coupon):
        messages.error(request, f"Coupon \"{code}\" has expired or is no longer available.")
        return redirect('store_frontend:cart')

    cart = _get_or_create_cart(request)
    cart.coupon = coupon
    cart.save(update_fields=['coupon'])

    if coupon.discount_type == Coupon.PERCENT:
        messages.success(request, f"Coupon \"{code}\" applied — {coupon.value}% off!")
    else:
        messages.success(request, f"Coupon \"{code}\" applied — NGN{coupon.value:,.0f} off!")

    return redirect('store_frontend:cart')


def store_remove_coupon(request):
    """Remove the currently applied coupon from the cart."""
    if request.method == 'POST':
        cart = _get_or_create_cart(request)
        cart.coupon = None
        cart.save(update_fields=['coupon'])
        messages.info(request, "Coupon removed.")
    return redirect('store_frontend:cart')

@login_required(login_url='store_frontend:auth')
def store_checkout(request):
    cart = _get_or_create_cart(request)
    config = StoreConfiguration.get_solo()

    items = list(cart.items.select_related('product').prefetch_related('product__images').all())

    if not items:
        messages.info(request, "Your cart is empty. Add items before checking out.")
        return redirect('store_frontend:cart')

    subtotal = sum((item.product.price * item.quantity for item in items))
    shipping_total = config.flat_shipping_rate if items else Decimal("0.00")
    tax_total = (subtotal * config.tax_rate_percent / Decimal("100.0")).quantize(Decimal("0.01"))

    # Coupon / discount
    applied_coupon = cart.coupon if (cart.coupon and is_coupon_usable(cart.coupon)) else None
    discount_total = calc_discount(subtotal, applied_coupon)
    discounted_subtotal = (subtotal - discount_total).quantize(Decimal("0.01"))
    grand_total = (discounted_subtotal + shipping_total + tax_total).quantize(Decimal("0.01"))

    # BNPL eligibility — all items in cart must have bnpl_enabled=True
    all_bnpl_eligible = all(item.product.bnpl_enabled for item in items)
    bnpl_ineligible_items = [item.product.title for item in items if not item.product.bnpl_enabled]
    bnpl_plan = BNPLPlanTemplate.objects.filter(active=True).order_by('created_at').first()
    num_installments = bnpl_plan.num_installments if bnpl_plan else 4
    bnpl_installment = (grand_total / Decimal(str(num_installments))).quantize(Decimal("0.01")) if (grand_total and all_bnpl_eligible) else Decimal("0.00")

    # Pre-populate from user profile
    profile = getattr(request.user, 'store_profile', None)
    default_address = Address.objects.filter(user=request.user, is_default=True).first()

    context = {
        'cart': cart,
        'items': items,
        'subtotal': subtotal,
        'discount_total': discount_total,
        'applied_coupon': applied_coupon,
        'shipping_total': shipping_total,
        'tax_total': tax_total,
        'tax_rate_percent': config.tax_rate_percent,
        'grand_total': grand_total,
        'bnpl_installment': bnpl_installment,
        'bnpl_plan': bnpl_plan,
        'all_bnpl_eligible': all_bnpl_eligible,
        'bnpl_ineligible_items': bnpl_ineligible_items,
        'num_installments': num_installments,
        'profile': profile,
        'default_address': default_address,
    }
    return render(request, 'store/store_checkout.html', context)

from django.http import JsonResponse

def store_auth(request):
    if request.user.is_authenticated:
        return redirect('store_frontend:profile')

    if request.method == "POST":
        action = request.POST.get("action")
        
        is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest' or 'application/json' in request.headers.get('Accept', '')
        
        if action == "signin":
            email = request.POST.get("email")
            password = request.POST.get("password")
            user = authenticate(request, username=email, password=password)
            if user is not None:
                if not hasattr(user, 'store_profile'):
                    UserStoreProfile.objects.create(user=user, email_verified=True)
                login(request, user)
                if is_ajax:
                    return JsonResponse({"success": True, "redirect_url": reverse('store_frontend:profile')})
                return redirect('store_frontend:profile')
            else:
                if is_ajax:
                    return JsonResponse({"success": False, "error": "Invalid email or password."})
                messages.error(request, "Invalid email or password.")
                
        elif action == "signup":
            first_name = request.POST.get("first_name", "")
            last_name = request.POST.get("last_name", "")
            email = request.POST.get("email")
            password = request.POST.get("password")
            
            if User.objects.filter(email=email).exists():
                if is_ajax:
                    return JsonResponse({"success": False, "error": "An account with that email already exists."})
                messages.error(request, "An account with that email already exists.")
            else:
                user = User.objects.create_user(email=email, password=password, first_name=first_name, last_name=last_name)
                user.is_active = True
                user.save()
                
                profile = UserStoreProfile.objects.create(user=user)
                
                account_signup_email = getattr(settings, 'ACCOUNT_SIGNUP_EMAIL', False)
                if account_signup_email:
                    profile.email_verified = False
                    profile.save()
                    
                    otp = EmailOTP.create_for_user(user, minutes_valid=10)
                    try:
                        send_mail(
                            subject="Verify your Techxagon Store account",
                            message=f"Your verification code is {otp.code}",
                            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                            recipient_list=[user.email],
                            fail_silently=False,
                        )
                    except Exception:
                        pass
                        
                    login(request, user, backend='django.contrib.auth.backends.ModelBackend')
                    if is_ajax:
                        return JsonResponse({"success": True, "redirect_url": reverse('store_frontend:verify')})
                    return redirect('store_frontend:verify')
                else:
                    profile.email_verified = True
                    profile.save()
                    login(request, user, backend='django.contrib.auth.backends.ModelBackend')
                    if is_ajax:
                        return JsonResponse({"success": True, "redirect_url": reverse('store_frontend:home')})
                    messages.success(request, "Account created successfully!")
                    return redirect('store_frontend:home')
                    
    return render(request, 'store/store_auth.html')

def store_verify(request):
    if not request.user.is_authenticated:
        return redirect('store_frontend:auth')
        
    profile = getattr(request.user, 'store_profile', None)
    if profile and profile.email_verified:
        return redirect('store_frontend:profile')

    if request.method == "POST":
        action = request.POST.get("action", "verify")
        if action == "resend":
            otp = EmailOTP.create_for_user(request.user, minutes_valid=10)
            try:
                send_mail(
                    subject="Verify your Techxagon Store account",
                    message=f"Your new verification code is {otp.code}",
                    from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                    recipient_list=[request.user.email],
                    fail_silently=False,
                )
                messages.success(request, "A new code has been sent to your email.")
            except Exception:
                messages.error(request, "Failed to send email.")
            return redirect('store_frontend:verify')

        code = request.POST.get("code", "")
        valid_otp = EmailOTP.objects.filter(user=request.user, code=code, used=False).first()
        if valid_otp and valid_otp.is_valid():
            valid_otp.used = True
            valid_otp.save()
            
            if profile:
                profile.email_verified = True
                profile.save()
                
            messages.success(request, "Email verified successfully!")
            return redirect('store_frontend:profile')
        else:
            messages.error(request, "Invalid or expired verification code.")

    return render(request, 'store/store_verify.html')

@login_required(login_url='store_frontend:auth')
def store_profile(request):
    profile, created = UserStoreProfile.objects.get_or_create(user=request.user)
    
    # Handle POST Actions
    if request.method == "POST":
        action = request.POST.get("action")
        
        if action == "update_settings":
            first_name = request.POST.get("first_name", "").strip()
            last_name = request.POST.get("last_name", "").strip()
            
            # Save User fields
            request.user.first_name = first_name
            request.user.last_name = last_name
            request.user.save()
            
            messages.success(request, "Account settings updated successfully!")
            return redirect(reverse('store_frontend:profile') + '?tab=settings')
            
        elif action == "add_address" or action == "edit_address":
            address_id = request.POST.get("address_id")
            full_name = request.POST.get("full_name", "").strip()
            line1 = request.POST.get("line1", "").strip()
            line2 = request.POST.get("line2", "").strip()
            city = request.POST.get("city", "").strip()
            state = request.POST.get("state", "").strip()
            postal_code = request.POST.get("postal_code", "").strip()
            country = request.POST.get("country", "").strip()
            phone = request.POST.get("phone", "").strip()
            is_default = request.POST.get("is_default") == "on"
            
            if not full_name or not line1 or not city or not country:
                messages.error(request, "Please fill in all required fields.")
                tab_suffix = '?tab=addresses'
                if address_id:
                    tab_suffix += f'&edit={address_id}'
                return redirect(reverse('store_frontend:profile') + tab_suffix)

            # Map common countries to 2-letter codes if they didn't input ISO code
            country_mapping = {
                "nigeria": "NG",
                "ghana": "GH",
                "kenya": "KE",
                "south africa": "ZA",
                "united states": "US",
                "united kingdom": "GB",
            }
            mapped_country = country_mapping.get(country.lower(), country[:2].upper())
            
            if address_id:
                # Edit Address
                address_obj = get_object_or_404(Address, id=address_id, user=request.user)
                address_obj.full_name = full_name
                address_obj.line1 = line1
                address_obj.line2 = line2
                address_obj.city = city
                address_obj.state = state
                address_obj.postal_code = postal_code
                address_obj.country = mapped_country
                address_obj.phone = phone
                address_obj.save()
                messages.success(request, "Address updated successfully!")
            else:
                # Add Address
                address_obj = Address.objects.create(
                    user=request.user,
                    full_name=full_name,
                    line1=line1,
                    line2=line2,
                    city=city,
                    state=state,
                    postal_code=postal_code,
                    country=mapped_country,
                    phone=phone,
                )
                messages.success(request, "Address added successfully!")
                
            if is_default:
                # Mark others as not default
                Address.objects.filter(user=request.user).exclude(id=address_obj.id).update(is_default=False)
                address_obj.is_default = True
                address_obj.save()
                
            return redirect(reverse('store_frontend:profile') + '?tab=addresses')
            
        elif action == "delete_address":
            address_id = request.POST.get("address_id")
            address_obj = get_object_or_404(Address, id=address_id, user=request.user)
            was_default = address_obj.is_default
            address_obj.delete()
            
            # If the deleted address was default, make another one default
            if was_default:
                new_default = Address.objects.filter(user=request.user).first()
                if new_default:
                    new_default.is_default = True
                    new_default.save()
                    
            messages.success(request, "Address deleted successfully!")
            return redirect(reverse('store_frontend:profile') + '?tab=addresses')
            
        elif action == "buy_again":
            product_id = request.POST.get("product_id")
            product = get_object_or_404(Product, id=product_id, is_active=True)
            cart = _get_or_create_cart(request)
            item, created = CartItem.objects.get_or_create(
                cart=cart,
                product=product,
                defaults={"quantity": 1},
            )
            if not created:
                item.quantity = F("quantity") + 1
                item.save(update_fields=["quantity"])
                
            messages.success(request, f"'{product.title}' added to cart.")
            return redirect('store_frontend:cart')

        elif action == "create_ticket":
            ticket_type = request.POST.get("ticket_type", "general")
            order_id = request.POST.get("order_id", "").strip()
            payment_id = request.POST.get("payment_id", "").strip()
            subject = request.POST.get("subject", "").strip()
            description = request.POST.get("description", "").strip()
            priority = request.POST.get("priority", "medium")

            if not subject or not description:
                messages.error(request, "Subject and description are required.")
                return redirect(reverse('store_frontend:profile') + '?tab=tickets&new=true')

            # Fetch objects and validate ownership
            order_obj = None
            if order_id:
                try:
                    order_obj = Order.objects.get(id=order_id, user=request.user)
                except (Order.DoesNotExist, Exception):
                    messages.error(request, "Invalid order selected.")
                    return redirect(reverse('store_frontend:profile') + '?tab=tickets&new=true')

            payment_obj = None
            if payment_id:
                try:
                    payment_obj = Payment.objects.get(id=payment_id, order__user=request.user)
                except (Payment.DoesNotExist, Exception):
                    messages.error(request, "Invalid payment transaction selected.")
                    return redirect(reverse('store_frontend:profile') + '?tab=tickets&new=true')

            Ticket.objects.create(
                user=request.user,
                ticket_type=ticket_type,
                order=order_obj,
                payment=payment_obj,
                subject=subject,
                description=description,
                priority=priority
            )
            
            # Send dynamic custom notification to user about the ticket creation
            try:
                from notifications.services import dispatch
                from notifications.models import Notification
                from notifications.messages import MessageSpec
                
                ticket_created_spec = MessageSpec(
                    kind=Notification.Kind.SYSTEM,
                    title_template="Support ticket submitted: {{ data.subject }}",
                    body_template="We received your ticket '{{ data.subject }}' (Priority: {{ data.priority|upper }}). Support staff will review it shortly.",
                )
                dispatch(
                    users=[request.user],
                    message=ticket_created_spec,
                    data={"subject": subject, "priority": priority},
                    send_email=False
                )
            except Exception:
                pass
                
            messages.success(request, "Your support ticket was submitted successfully! We will get back to you soon.")
            return redirect(reverse('store_frontend:profile') + '?tab=tickets')

        elif action == "reply_ticket":
            ticket_id = request.POST.get("ticket_id", "").strip()
            message_content = request.POST.get("message", "").strip()

            if not message_content:
                messages.error(request, "Message content cannot be empty.")
                return redirect(reverse('store_frontend:profile') + f'?tab=tickets&ticket_id={ticket_id}')

            try:
                ticket_obj = Ticket.objects.get(id=ticket_id, user=request.user)
            except (Ticket.DoesNotExist, Exception):
                messages.error(request, "Ticket not found.")
                return redirect(reverse('store_frontend:profile') + '?tab=tickets')

            TicketMessage.objects.create(
                ticket=ticket_obj,
                sender=request.user,
                message=message_content,
                is_admin=False
            )

            # Reopen the ticket status if it was closed or resolved
            if ticket_obj.status in [Ticket.Status.RESOLVED, Ticket.Status.CLOSED]:
                ticket_obj.status = Ticket.Status.OPEN
                ticket_obj.save()

            messages.success(request, "Your reply was submitted successfully.")
            return redirect(reverse('store_frontend:profile') + f'?tab=tickets&ticket_id={ticket_id}')


    # Handle GET request (Tab loading)
    tab = request.GET.get('tab', 'orders')
    context = {
        'active_tab': tab,
        'profile': profile,
    }
    
    if tab == 'orders':
        # Get orders
        status_filter = request.GET.get('status', 'all')
        orders_qs = Order.objects.filter(user=request.user).prefetch_related('items__product__images').order_by('-created_at')
        
        if status_filter != 'all':
            if status_filter == 'pending':
                orders_qs = orders_qs.filter(status=Order.Status.PENDING)
            elif status_filter == 'paid':
                orders_qs = orders_qs.filter(status=Order.Status.PAID)
            elif status_filter == 'fulfilled':
                orders_qs = orders_qs.filter(status=Order.Status.FULFILLED)
            else:
                orders_qs = orders_qs.filter(status=status_filter)
                
        context['orders'] = orders_qs
        context['status_filter'] = status_filter
        
    elif tab == 'saved':
        saved_items = SavedItem.objects.filter(user=request.user).select_related('product').prefetch_related('product__images')
        context['saved_items'] = saved_items
        
    elif tab == 'addresses':
        addresses = Address.objects.filter(user=request.user)
        context['addresses'] = addresses
        
        # Check if we are editing an address
        edit_id = request.GET.get('edit')
        if edit_id:
            context['edit_address'] = get_object_or_404(Address, id=edit_id, user=request.user)

    elif tab == 'payments':
        # List of all payments for user's orders
        payments = Payment.objects.filter(order__user=request.user).select_related('order').order_by('-created_at')
        context['payments'] = payments

    elif tab == 'tickets':
        # List of user's tickets
        tickets = Ticket.objects.filter(user=request.user).order_by('-created_at')
        context['tickets'] = tickets
        
        # Dropdowns
        context['user_orders'] = Order.objects.filter(user=request.user).order_by('-created_at')
        context['user_payments'] = Payment.objects.filter(order__user=request.user).order_by('-created_at')
        
        # Prefilled parameters
        context['selected_order_id'] = request.GET.get('order_id', '')
        context['selected_payment_id'] = request.GET.get('payment_id', '')
        context['show_new_form'] = request.GET.get('new') == 'true'

        # Ticket conversation thread / detail view
        ticket_id = request.GET.get('ticket_id')
        if ticket_id:
            active_ticket = get_object_or_404(Ticket, id=ticket_id, user=request.user)
            context['active_ticket'] = active_ticket
            context['ticket_messages'] = active_ticket.messages.all().select_related('sender')
            
    return render(request, 'store/store_profile.html', context)


@login_required(login_url='store_frontend:auth')
def store_toggle_save(request, product_id):
    product = get_object_or_404(Product, id=product_id)
    saved_item = SavedItem.objects.filter(user=request.user, product=product).first()
    if saved_item:
        saved_item.delete()
        saved = False
        messages.info(request, f"'{product.title}' removed from saved items.")
    else:
        SavedItem.objects.create(user=request.user, product=product)
        saved = True
        messages.success(request, f"'{product.title}' saved to your items!")
        
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in request.headers.get('Accept', ''):
        return JsonResponse({'success': True, 'saved': saved})
        
    next_url = request.GET.get('next') or request.META.get('HTTP_REFERER') or reverse('store_frontend:profile')
    return redirect(next_url)


@login_required(login_url='store_frontend:auth')
def store_notifications(request):
    from notifications.models import Notification
    notifications = Notification.objects.filter(user=request.user).order_by('-created_at')
    return render(request, 'store/store_notifications.html', {'notifications': notifications})


@login_required(login_url='store_frontend:auth')
def store_mark_notification_read(request, notification_id):
    if request.method == 'POST':
        from notifications.models import Notification
        from django.utils import timezone
        try:
            n = Notification.objects.get(id=notification_id, user=request.user)
            if not n.is_read:
                n.is_read = True
                n.read_at = timezone.now()
                n.save(update_fields=['is_read', 'read_at'])
            return JsonResponse({'success': True})
        except Notification.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Not found'}, status=404)
    return JsonResponse({'success': False, 'error': 'Invalid request'}, status=400)


@login_required(login_url='store_frontend:auth')
def store_mark_all_notifications_read(request):
    if request.method == 'POST':
        from notifications.models import Notification
        from django.utils import timezone
        Notification.objects.filter(user=request.user, is_read=False).update(
            is_read=True,
            read_at=timezone.now()
        )
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in request.headers.get('Accept', ''):
            return JsonResponse({'success': True})
        return redirect('store_frontend:notifications')
    return JsonResponse({'success': False, 'error': 'Invalid request'}, status=400)


def _dispatch_order_notification(request, order, is_bnpl, agreement_id=None):
    from notifications.services import dispatch
    from notifications.events import ORDER_CREATED
    from django.conf import settings
    
    send_email = getattr(settings, "ACCOUNT_EMAIL_NOTIFICATION", True)
    
    # Build list of items
    items_data = []
    for item in order.items.all():
        items_data.append({
            "title": item.title_snapshot or item.product.title,
            "quantity": item.quantity,
            "line_total": str(item.line_total),
        })
        
    data = {
        "order_id": f"#{str(order.id)[:8].upper()}",
        "currency": "₦",
        "grand_total": str(order.grand_total),
        "is_bnpl": is_bnpl,
        "is_buy_now": False,
        "items": items_data,
        "cta": {
            "url": request.build_absolute_uri(reverse('store_frontend:profile') + '?tab=orders')
        }
    }
    
    if is_bnpl and agreement_id:
        try:
            from store.models import BNPLAgreement
            agreement = BNPLAgreement.objects.get(id=agreement_id)
            per_inst = (agreement.total_amount / Decimal(str(agreement.num_installments))).quantize(Decimal('0.01'))
            data.update({
                "bnpl_pay_today": str(per_inst),
                "bnpl_num_installments": agreement.num_installments,
                "bnpl_interval_days": agreement.interval_days,
            })
        except Exception:
            pass
            
    dispatch(
        users=[order.user] if order.user else [],
        message=ORDER_CREATED,
        ctx={"app_name": "Techxagon Store"},
        data=data,
        send_email=send_email,
    )


def _dispatch_order_cancelled_notification(request, order):
    from notifications.services import dispatch
    from notifications.events import ORDER_CANCELLED
    from django.conf import settings
    
    send_email = getattr(settings, "ACCOUNT_EMAIL_NOTIFICATION", True)
    
    dispatch(
        users=[order.user] if order.user else [],
        message=ORDER_CANCELLED,
        ctx={"app_name": "Techxagon Store"},
        data={
            "order_id": f"#{str(order.id)[:8].upper()}",
        },
        send_email=send_email,
    )


def store_logout(request):
    logout(request)
    messages.success(request, "You have been successfully logged out.")
    return redirect('store_frontend:auth')


@login_required(login_url='store_frontend:auth')
def store_initiate_payment(request):
    """Handles the checkout form POST for a full/immediate payment via Flutterwave."""
    if request.method != 'POST':
        return redirect('store_frontend:checkout')

    cart = _get_or_create_cart(request)
    config = StoreConfiguration.get_solo()
    items = list(cart.items.select_related('product').all())

    if not items:
        messages.error(request, "Your cart is empty.")
        return redirect('store_frontend:cart')

    # --- Collect form data ---
    first_name = request.POST.get('first_name', request.user.first_name).strip()
    last_name  = request.POST.get('last_name', request.user.last_name).strip()
    email      = request.POST.get('email', request.user.email).strip()
    phone      = request.POST.get('phone', '').strip()
    line1      = request.POST.get('line1', '').strip()
    city       = request.POST.get('city', '').strip()
    state      = request.POST.get('state', '').strip()
    notes      = request.POST.get('notes', '').strip()

    # --- Compute totals (with coupon) ---
    subtotal        = sum(item.product.price * item.quantity for item in items)
    shipping_total  = config.flat_shipping_rate
    tax_total       = (subtotal * config.tax_rate_percent / Decimal('100.0')).quantize(Decimal('0.01'))
    applied_coupon  = cart.coupon if (cart.coupon and is_coupon_usable(cart.coupon)) else None
    discount_total  = calc_discount(subtotal, applied_coupon)
    discounted_sub  = (subtotal - discount_total).quantize(Decimal('0.01'))
    grand_total     = (discounted_sub + shipping_total + tax_total).quantize(Decimal('0.01'))

    # --- Create Order ---
    order = Order.objects.create(
        user=request.user,
        status=Order.Status.PENDING,
        subtotal=subtotal,
        discount_total=discount_total,
        tax_total=tax_total,
        shipping_total=shipping_total,
        grand_total=grand_total,
        notes=notes,
    )
    for item in items:
        OrderItem.objects.create(
            order=order,
            product=item.product,
            title_snapshot=item.product.title,
            unit_price=item.product.price,
            quantity=item.quantity,
            line_total=(item.product.price * item.quantity).quantize(Decimal('0.01')),
        )

    # --- Burn coupon usage if one was applied ---
    if applied_coupon:
        applied_coupon.used_count = applied_coupon.used_count + 1
        applied_coupon.save(update_fields=['used_count'])
        cart.coupon = None
        cart.save(update_fields=['coupon'])

    # --- Create Payment record ---
    tx_ref = f"store-order-{order.id}"
    payment_obj = Payment.objects.create(
        order=order,
        provider=Payment.Provider.FLUTTERWAVE,
        status=Payment.Status.INITIATED,
        amount=grand_total,
        currency='NGN',
        provider_ref=tx_ref,
    )

    # --- Generate Flutterwave link ---
    redirect_url = request.build_absolute_uri(reverse('store_frontend:payment_callback'))
    customer_detail = {
        'email': email,
        'phonenumber': phone,
        'name': f"{first_name} {last_name}".strip(),
    }
    link = generate_payment_link(
        request=request,
        user_id=request.user.id,
        tx_ref=tx_ref,
        redirect_url=redirect_url,
        title='Techxagon Store Order',
        customer_detail=customer_detail,
        total_amount=float(grand_total),
        payment_plan='',
    )

    if not link:
        order.delete()
        messages.error(request, "We could not initiate payment at this time. Please try again.")
        return redirect('store_frontend:checkout')

    # Store order ID in session so we can look it up in callback
    request.session['pending_order_id'] = str(order.id)
    return redirect(link)


@login_required(login_url='store_frontend:auth')
def store_initiate_bnpl(request):
    """Handles BNPL checkout — creates an order + BNPLAgreement and charges first installment."""
    if request.method != 'POST':
        return redirect('store_frontend:checkout')

    cart = _get_or_create_cart(request)
    config = StoreConfiguration.get_solo()
    items = list(cart.items.select_related('product').all())

    if not items:
        messages.error(request, "Your cart is empty.")
        return redirect('store_frontend:cart')

    # Block BNPL if any item is not BNPL-enabled
    if not all(item.product.bnpl_enabled for item in items):
        messages.error(request, "Some items in your cart are not eligible for Buy Now Pay Later. Please remove them or choose a different payment method.")
        return redirect('store_frontend:checkout')

    bnpl_plan = BNPLPlanTemplate.objects.filter(active=True).order_by('created_at').first()
    if not bnpl_plan:
        messages.error(request, "No BNPL plan is currently available. Please use a different payment method.")
        return redirect('store_frontend:checkout')

    # --- Collect form data ---
    first_name = request.POST.get('first_name', request.user.first_name).strip()
    last_name  = request.POST.get('last_name', request.user.last_name).strip()
    email      = request.POST.get('email', request.user.email).strip()
    phone      = request.POST.get('phone', '').strip()
    notes      = request.POST.get('notes', '').strip()

    # --- Compute totals (with coupon) ---
    subtotal        = sum(item.product.price * item.quantity for item in items)
    shipping_total  = config.flat_shipping_rate
    tax_total       = (subtotal * config.tax_rate_percent / Decimal('100.0')).quantize(Decimal('0.01'))
    applied_coupon  = cart.coupon if (cart.coupon and is_coupon_usable(cart.coupon)) else None
    discount_total  = calc_discount(subtotal, applied_coupon)
    discounted_sub  = (subtotal - discount_total).quantize(Decimal('0.01'))
    grand_total     = (discounted_sub + shipping_total + tax_total).quantize(Decimal('0.01'))

    # BNPL fees
    fee_flat   = bnpl_plan.customer_fee_flat or Decimal('0.00')
    fee_rate   = bnpl_plan.customer_fee_rate or Decimal('0.0000')
    fees       = (fee_flat + grand_total * fee_rate).quantize(Decimal('0.01'))
    total_bnpl = (grand_total + fees).quantize(Decimal('0.01'))
    n          = bnpl_plan.num_installments
    per_inst   = (total_bnpl / Decimal(str(n))).quantize(Decimal('0.01'))

    # --- Create Order ---
    order = Order.objects.create(
        user=request.user,
        status=Order.Status.PENDING,
        subtotal=subtotal,
        discount_total=discount_total,
        tax_total=tax_total,
        shipping_total=shipping_total,
        grand_total=grand_total,
        notes=notes,
    )
    for item in items:
        OrderItem.objects.create(
            order=order,
            product=item.product,
            title_snapshot=item.product.title,
            unit_price=item.product.price,
            quantity=item.quantity,
            line_total=(item.product.price * item.quantity).quantize(Decimal('0.01')),
        )

    # --- Burn coupon usage if one was applied ---
    if applied_coupon:
        applied_coupon.used_count = applied_coupon.used_count + 1
        applied_coupon.save(update_fields=['used_count'])
        cart.coupon = None
        cart.save(update_fields=['coupon'])

    # --- Create BNPLAgreement + schedule ---
    agreement = BNPLAgreement.objects.create(
        order=order,
        plan=bnpl_plan,
        provider=BNPLPlanTemplate.Provider.FLUTTERWAVE,
        status=BNPLAgreement.Status.PENDING,
        num_installments=n,
        interval_days=bnpl_plan.interval_days,
        take_downpayment_now=bnpl_plan.take_downpayment_now,
        currency='NGN',
        principal_amount=grand_total,
        customer_fee_flat=fee_flat,
        customer_fee_rate=fee_rate,
        total_amount=total_bnpl,
        amount_outstanding=total_bnpl,
    )
    agreement.initialize_schedule()

    # --- Charge first installment (downpayment) via Flutterwave ---
    tx_ref = f"store-bnpl-1-{order.id}"
    redirect_url = request.build_absolute_uri(reverse('store_frontend:payment_callback'))
    customer_detail = {
        'email': email,
        'phonenumber': phone,
        'name': f"{first_name} {last_name}".strip(),
    }
    link = generate_payment_link(
        request=request,
        user_id=request.user.id,
        tx_ref=tx_ref,
        redirect_url=redirect_url,
        title=f"Techxagon FlexPay — Payment 1 of {n}",
        customer_detail=customer_detail,
        total_amount=float(per_inst),
        payment_plan='',
    )

    if not link:
        agreement.delete()
        order.delete()
        messages.error(request, "We could not initiate BNPL payment at this time. Please try again.")
        return redirect('store_frontend:checkout')

    # Store in session for callback
    request.session['pending_order_id'] = str(order.id)
    request.session['pending_bnpl_agreement_id'] = str(agreement.id)
    return redirect(link)


def store_payment_callback(request):
    """Flutterwave redirects here after payment (full or BNPL first installment)."""
    status_param     = request.GET.get('status', '')
    transaction_id   = request.GET.get('transaction_id', '')
    tx_ref           = request.GET.get('tx_ref', '')

    order_id       = request.session.get('pending_order_id')
    agreement_id   = request.session.get('pending_bnpl_agreement_id')
    is_bnpl        = bool(agreement_id)

    # Look up the order
    order = None
    if order_id:
        try:
            order = Order.objects.get(id=order_id, user=request.user if request.user.is_authenticated else None)
        except (Order.DoesNotExist, Exception):
            pass

    if status_param == 'cancelled':
        if order:
            order.status = Order.Status.CANCELLED
            order.save(update_fields=['status'])
            _dispatch_order_cancelled_notification(request, order)
        messages.warning(request, "Payment was cancelled. Your order has been cancelled.")
        # Clean session
        request.session.pop('pending_order_id', None)
        request.session.pop('pending_bnpl_agreement_id', None)
        return render(request, 'store/store_order_confirmation.html', {
            'success': False,
            'cancelled': True,
            'order': order,
        })

    if not transaction_id or not order:
        messages.error(request, "Invalid payment callback. Please contact support.")
        request.session.pop('pending_order_id', None)
        request.session.pop('pending_bnpl_agreement_id', None)
        return redirect('store_frontend:cart')

    # --- Verify with Flutterwave ---
    result = confirm_transaction(transaction_id)

    flw_status = ''
    if result.get('ok') and result.get('data'):
        flw_status = (result['data'].get('status') or '').lower()

    if flw_status in ('successful', 'success'):
        if is_bnpl:
            # Mark order as paid (partial — first installment)
            order.status = Order.Status.PAID
            order.save(update_fields=['status'])

            # Update BNPLAgreement
            try:
                agreement = BNPLAgreement.objects.get(id=agreement_id)
                agreement.status = BNPLAgreement.Status.ACTIVE
                agreement.provider_checkout_id = transaction_id
                agreement.save(update_fields=['status', 'provider_checkout_id'])

                # Mark first installment as captured
                first_inst = agreement.installments.order_by('index').first()
                if first_inst:
                    per_inst = (agreement.total_amount / Decimal(str(agreement.num_installments))).quantize(Decimal('0.01'))
                    first_inst.mark_captured(per_inst)
            except BNPLAgreement.DoesNotExist:
                pass

            # Update/create the Payment record
            payment_obj, _ = Payment.objects.get_or_create(
                order=order,
                defaults={
                    'provider': Payment.Provider.FLUTTERWAVE,
                    'amount': order.grand_total,
                    'currency': 'NGN',
                    'provider_ref': tx_ref,
                }
            )
            payment_obj.status = Payment.Status.AUTHORIZED
            payment_obj.provider_ref = tx_ref
            payment_obj.save(update_fields=['status', 'provider_ref'])

        else:
            # Full payment — mark order as PAID
            order.status = Order.Status.PAID
            order.save(update_fields=['status'])
            order.reduce_stock()

            # Update Payment record
            try:
                payment_obj = Payment.objects.get(order=order)
                payment_obj.status = Payment.Status.CAPTURED
                payment_obj.provider_ref = tx_ref
                payment_obj.save(update_fields=['status', 'provider_ref'])
            except Payment.DoesNotExist:
                pass

        # Dispatch order notification
        _dispatch_order_notification(request, order, is_bnpl, agreement_id)

        # --- Clear the cart ---
        cart = _get_or_create_cart(request)
        cart.items.all().delete()
        cart.active = False
        cart.save(update_fields=['active'])

        # Clean session
        request.session.pop('pending_order_id', None)
        request.session.pop('pending_bnpl_agreement_id', None)

        return render(request, 'store/store_order_confirmation.html', {
            'success': True,
            'order': order,
            'is_bnpl': is_bnpl,
        })
    else:
        # Payment failed
        order.status = Order.Status.CANCELLED
        order.save(update_fields=['status'])
        _dispatch_order_cancelled_notification(request, order)
        request.session.pop('pending_order_id', None)
        request.session.pop('pending_bnpl_agreement_id', None)
        return render(request, 'store/store_order_confirmation.html', {
            'success': False,
            'cancelled': False,
            'order': order,
        })
