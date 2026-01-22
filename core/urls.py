# urls.py
from django.urls import path
from .views import (active_modules_for_user,leaderboard_seasons_view,leaderboard_season_detail_view,leaderboard_season_set_active_view,
course_pass_criteria_view,admin_categories_list_create,admin_categories_detail,admin_products_list_create,
admin_products_detail,admin_product_images_upload,admin_product_images_detail)

urlpatterns = [
    path("api/academics/modules/active/", active_modules_for_user, name="active-modules-for-user"),
    path("api/admin/settings/leaderboard-seasons/", leaderboard_seasons_view),
    path("api/admin/settings/leaderboard-seasons/<int:season_id>/", leaderboard_season_detail_view),
    path("api/admin/settings/leaderboard-seasons/<int:season_id>/set-active/", leaderboard_season_set_active_view),
    path("api/admin/courses/<int:course_id>/pass-criteria", course_pass_criteria_view),

    # Admin store
    path("api/admin/store/categories", admin_categories_list_create),
    path("api/admin/store/categories/<uuid:category_id>", admin_categories_detail),

    path("api/admin/store/products", admin_products_list_create),
    path("api/admin/store/products/<uuid:product_id>", admin_products_detail),

    path("api/admin/store/products/<uuid:product_id>/images/upload", admin_product_images_upload),
    path("api/admin/store/product-images/<uuid:image_id>", admin_product_images_detail),
]
