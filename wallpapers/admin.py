"""The whole content-management surface: categories and uploads live here."""

from django.contrib import admin, messages
from django.contrib.admin.models import ADDITION, LogEntry
from django.db.models import Count, Q
from django.utils.html import format_html
from django.utils.translation import ngettext

from .forms import WallpaperBulkAddForm
from .models import Category, MediaType, Wallpaper, title_from_filename


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "position", "is_active", "artwork_count")
    list_editable = ("position", "is_active")
    list_display_links = ("name",)
    search_fields = ("name", "description")
    prepopulated_fields = {"slug": ("name",)}
    ordering = ("position", "name")
    fields = ("name", "slug", "description", "thumbnail", "position", "is_active")

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(
                artwork_count=Count("wallpapers", filter=Q(wallpapers__is_published=True))
            )
        )

    @admin.display(description="Published items", ordering="artwork_count")
    def artwork_count(self, obj):
        return obj.artwork_count


@admin.register(Wallpaper)
class WallpaperAdmin(admin.ModelAdmin):
    list_display = (
        "preview",
        "display_title",
        "category",
        "media_type",
        "is_premium",
        "is_published",
        "priority",
        "created_at",
    )
    list_display_links = ("preview", "display_title")
    list_editable = ("is_premium", "is_published", "priority")
    list_filter = ("category", "media_type", "is_premium", "is_published")
    search_fields = ("title", "original_filename")
    list_select_related = ("category",)
    date_hierarchy = "created_at"
    readonly_fields = (
        "preview_large",
        "thumbnail_preview",
        "media_type",
        "original_filename",
        "file_path",
        "thumbnail_path",
        "created_at",
    )
    actions = ("publish", "unpublish", "mark_premium", "mark_free")
    # The change form: one item, every field.
    fieldsets = (
        (None, {"fields": ("category", "title", "file", "preview_large")}),
        (
            "Thumbnail",
            {
                "fields": ("thumbnail", "thumbnail_preview"),
                "description": (
                    "Videos only, and optional. Leave empty if no poster frame is available."
                ),
            },
        ),
        ("Placement", {"fields": ("priority", "is_premium", "is_published")}),
        (
            "Derived",
            {
                "fields": (
                    "media_type",
                    "original_filename",
                    "file_path",
                    "thumbnail_path",
                    "created_at",
                ),
                "description": "Filled in automatically from the upload.",
            },
        ),
    )

    # The add form: a batch of files into one category, nothing else to fill in.
    add_fieldsets = (
        (
            None,
            {
                "fields": ("category", "files"),
                "description": (
                    "Each file becomes its own wallpaper - published, free and at "
                    "priority 0. Change any of them afterwards from the list."
                ),
            },
        ),
    )

    # --- add form vs change form -----------------------------------------
    # Every override below branches on `obj is None` so the change form keeps
    # behaving exactly as it did before bulk upload existed.

    def get_fieldsets(self, request, obj=None):
        if obj is None:
            return self.add_fieldsets
        return super().get_fieldsets(request, obj)

    def get_readonly_fields(self, request, obj=None):
        # The preview and the derived fields have nothing to show before the
        # upload exists, and listing them here would push them into `exclude`.
        if obj is None:
            return ()
        return super().get_readonly_fields(request, obj)

    def get_form(self, request, obj=None, change=False, **kwargs):
        if obj is None:
            kwargs["form"] = WallpaperBulkAddForm
        return super().get_form(request, obj, change=change, **kwargs)

    # --- one submit, many rows -------------------------------------------

    def save_model(self, request, obj, form, change):
        if change:
            return super().save_model(request, obj, form, change)

        created = []
        for index, upload in enumerate(form.cleaned_data["files"]):
            # The first file lands on `obj` itself: the admin hands that same
            # instance to log_addition() and response_add(), both of which need
            # a real pk. The rest are peers created alongside it.
            row = obj if index == 0 else Wallpaper(category=obj.category)
            row.title = title_from_filename(upload.name)
            # Assigning the upload (rather than a name) leaves _committed False,
            # which is what lets save() keep the client's filename and derive
            # media_type. Category is already set, so the upload path gets its
            # slug. Per-row save() is required - bulk_create would skip both.
            row.file = upload
            row.save()
            created.append(row)

        # changeform_view already wraps this POST in transaction.atomic(), so a
        # failure part-way rolls the rows back on its own. ModelAdmin instances
        # are shared between threads, so the batch goes on the request.
        request._bulk_created = created

    def log_addition(self, request, obj, message):
        created = getattr(request, "_bulk_created", None)
        if not created or len(created) == 1:
            return super().log_addition(request, obj, message)
        # One INSERT for the batch, and every row gets a working History page.
        return LogEntry.objects.log_actions(
            user_id=request.user.pk,
            queryset=created,
            action_flag=ADDITION,
            change_message=message,
        )

    def response_add(self, request, obj, post_url_continue=None):
        created = getattr(request, "_bulk_created", None)
        if created and len(created) > 1:
            request._bulk_message = ngettext(
                "%(count)d wallpaper added to %(category)s.",
                "%(count)d wallpapers added to %(category)s.",
                len(created),
            ) % {"count": len(created), "category": obj.category.name}
        # Everything else - _continue, _addanother, preserved filters, the
        # changelist redirect - stays stock.
        return super().response_add(request, obj, post_url_continue)

    def message_user(
        self, request, message, level=messages.INFO, extra_tags="", fail_silently=False
    ):
        # Swap only the string response_add emits, so its hundred lines of
        # redirect branching stay untouched. A single-file add falls through to
        # Django's own message, which links to the new row.
        replacement = getattr(request, "_bulk_message", None)
        if replacement and level == messages.SUCCESS:
            del request._bulk_message
            message = replacement
        return super().message_user(request, message, level, extra_tags, fail_silently)

    @admin.display(description="Title", ordering="title")
    def display_title(self, obj):
        return obj.title or "(untitled)"

    @admin.display(description="Preview")
    def preview(self, obj):
        return self._render_preview(obj, width=60)

    @admin.display(description="Preview")
    def preview_large(self, obj):
        return self._render_preview(obj, width=320)

    @admin.display(description="Thumbnail")
    def thumbnail_preview(self, obj):
        if not obj.thumbnail:
            return "-"
        return format_html(
            '<img src="{}" width="320" style="border-radius:4px;object-fit:cover" />',
            obj.thumbnail.url,
        )

    @admin.display(description="file_path")
    def file_path(self, obj):
        return self._render_link(obj.file)

    @admin.display(description="thumbnail_path")
    def thumbnail_path(self, obj):
        return self._render_link(obj.thumbnail)

    @staticmethod
    def _render_link(filefield):
        if not filefield:
            return "-"
        return format_html('<a href="{0}" target="_blank">{0}</a>', filefield.url)

    @staticmethod
    def _render_preview(obj, width):
        if not obj.file:
            return "-"
        if obj.media_type == MediaType.VIDEO:
            if obj.thumbnail:
                # The poster frame is the whole point of the thumbnail, so the
                # list shows it rather than a scrubbing video element.
                return format_html(
                    '<img src="{}" width="{}" style="border-radius:4px;object-fit:cover" />',
                    obj.thumbnail.url,
                    width,
                )
            return format_html(
                '<video src="{}" width="{}" muted loop playsinline '
                'onmouseover="this.play()" onmouseout="this.pause()"></video>',
                obj.file.url,
                width,
            )
        return format_html(
            '<img src="{}" width="{}" style="border-radius:4px;object-fit:cover" />',
            obj.file.url,
            width,
        )

    @admin.action(description="Publish selected wallpapers")
    def publish(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_published=True)} published.")

    @admin.action(description="Unpublish selected wallpapers")
    def unpublish(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_published=False)} unpublished.")

    @admin.action(description="Mark selected as premium")
    def mark_premium(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_premium=True)} marked premium.")

    @admin.action(description="Mark selected as free")
    def mark_free(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_premium=False)} marked free.")


admin.site.site_header = "Wallpaper API admin"
admin.site.site_title = "Wallpaper API"
admin.site.index_title = "Content"
