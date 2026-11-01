"""Response rows.

Key order is part of the contract: DRF builds the row in ``Meta.fields`` order
and JSONRenderer preserves it, so these tuples must not be "tidied up" into
alphabetical or model order.
"""

from datetime import timezone as dt_timezone

from django.conf import settings
from django.utils import timezone
from rest_framework import serializers

from .models import Category, Wallpaper, title_from_filename


class CategorySerializer(serializers.ModelSerializer):
    # Comes from the queryset annotation in the view, not a per-row query.
    artwork_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Category
        fields = ("id", "name", "description", "position", "artwork_count")


class WallpaperByCategorySerializer(serializers.ModelSerializer):
    category_id = serializers.IntegerField(read_only=True)
    title = serializers.SerializerMethodField()
    file_path = serializers.SerializerMethodField()
    created_at = serializers.SerializerMethodField()

    class Meta:
        model = Wallpaper
        fields = (
            "id",
            "category_id",
            "title",
            "media_type",
            "is_premium",
            "original_filename",
            "file_path",
            "created_at",
        )

    def get_title(self, obj) -> str | None:
        return title_from_filename(obj.original_filename or "").strip() or None

    def get_file_path(self, obj) -> str | None:
        if not obj.file:
            return None
        url = obj.file.url
        if settings.USE_LOCAL_MEDIA:
            request = self.context.get("request")
            if request is not None:
                return request.build_absolute_uri(url)
        return url

    def get_created_at(self, obj) -> str:
        value = obj.created_at
        if timezone.is_naive(value):
            value = timezone.make_aware(value, dt_timezone.utc)
        value = timezone.localtime(value, dt_timezone.utc)
        return value.isoformat(timespec="microseconds")
