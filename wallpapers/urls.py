from django.urls import re_path

from .views import CategoryListView, WallpaperByCategoryView

urlpatterns = [
    re_path(r"^wallpapers/categories/?$", CategoryListView.as_view(), name="wallpaper-categories"),
    re_path(r"^wallpapers/?$", WallpaperByCategoryView.as_view(), name="wallpapers-by-category"),
]
