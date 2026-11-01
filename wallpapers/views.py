"""The three public feeds.

The wallpaper feed requires a category_id; the unfiltered wallpaper list is
intentionally not exposed.
"""

from django.db.models import Count, Q
from django.http import Http404
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Category, Wallpaper
from .pagination import paginate
from .serializers import CategorySerializer, WallpaperByCategorySerializer


class FeedView(APIView):
    """Shared envelope: {"data": [...], "meta": {...}}, in that order."""

    serializer_class = None

    def get_queryset(self, request):
        raise NotImplementedError

    def get(self, request):
        items, meta = paginate(self.get_queryset(request), request)
        serializer = self.serializer_class(items, many=True, context={"request": request})
        return Response({"data": serializer.data, "meta": meta})


class CategoryListView(FeedView):
    """GET /api/v1/wallpapers/categories"""

    serializer_class = CategorySerializer

    def get_queryset(self, request):
        return (
            Category.objects.filter(is_active=True)
            .annotate(
                artwork_count=Count(
                    "wallpapers",
                    filter=Q(wallpapers__is_published=True),
                )
            )
            .order_by("-id")
        )


class WallpaperByCategoryView(FeedView):
    """GET /api/v1/wallpapers?category_id=N"""

    serializer_class = WallpaperByCategorySerializer

    def get_queryset(self, request):
        raw_category_id = request.query_params.get("category_id")
        if raw_category_id in (None, ""):
            raise Http404
        try:
            category_id = int(raw_category_id)
        except (TypeError, ValueError):
            raise ValidationError("category_id must be an integer.")
        if category_id < 1:
            raise ValidationError("category_id must be a positive integer.")

        return Wallpaper.objects.filter(
            category_id=category_id,
            category__is_active=True,
            is_published=True,
        )
