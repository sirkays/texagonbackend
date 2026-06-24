from django.urls import path
from . import frontend_views

app_name = 'store_frontend'

urlpatterns = [
    path('', frontend_views.store_home, name='home'),
    path('search/', frontend_views.store_search, name='search'),
    path('product/<slug:slug>/', frontend_views.store_detail, name='detail'),
    path('cart/', frontend_views.store_cart, name='cart'),
    path('cart/add/', frontend_views.store_add_to_cart, name='add_to_cart'),
    path('cart/update/<uuid:item_id>/', frontend_views.store_update_cart, name='update_cart'),
    path('cart/remove/<uuid:item_id>/', frontend_views.store_remove_from_cart, name='remove_from_cart'),
    path('checkout/', frontend_views.store_checkout, name='checkout'),
    path('checkout/pay/', frontend_views.store_initiate_payment, name='initiate_payment'),
    path('checkout/bnpl/', frontend_views.store_initiate_bnpl, name='initiate_bnpl'),
    path('checkout/callback/', frontend_views.store_payment_callback, name='payment_callback'),
    path('cart/coupon/apply/', frontend_views.store_apply_coupon, name='apply_coupon'),
    path('cart/coupon/remove/', frontend_views.store_remove_coupon, name='remove_coupon'),
    path('auth/', frontend_views.store_auth, name='auth'),
    path('verify/', frontend_views.store_verify, name='verify'),
    path('profile/', frontend_views.store_profile, name='profile'),
    path('profile/save/toggle/<uuid:product_id>/', frontend_views.store_toggle_save, name='toggle_save'),
    path('notifications/', frontend_views.store_notifications, name='notifications'),
    path('notifications/read/<int:notification_id>/', frontend_views.store_mark_notification_read, name='mark_notification_read'),
    path('notifications/read-all/', frontend_views.store_mark_all_notifications_read, name='mark_all_notifications_read'),
    path('logout/', frontend_views.store_logout, name='logout'),
]
