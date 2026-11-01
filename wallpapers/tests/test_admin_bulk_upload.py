"""The admin's batch add: one submit, one row per file.

The add form and the change form are deliberately different shapes, so most of
what is asserted here is that the change form did *not* move.
"""

import os
import re
import shutil
import tempfile

from django.contrib.admin.models import ADDITION, LogEntry
from django.contrib.auth.models import Permission, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from wallpapers.forms import MAX_FILES_PER_UPLOAD
from wallpapers.models import Category, Wallpaper

from .test_contract import GIF, MP4, PNG

_TEMP_MEDIA = tempfile.mkdtemp()

UPLOAD_PATH = re.compile(
    r"^wallpaper/anime/assets/\d{4}/\d{2}/[0-9a-f]{32}\.png$"
)


def photo(width=1200, height=1800):
    """A PNG big enough that a third of its bytes is a real constraint."""
    import random
    from io import BytesIO

    from PIL import Image

    # Noise, so the source PNG cannot be compressed away to nothing - it makes
    # the "a third of the bytes" budget an actual constraint.
    random.seed(1)
    image = Image.frombytes(
        "RGB", (width, height), random.randbytes(width * height * 3)
    )
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def png(name="shot.png"):
    return SimpleUploadedFile(name, PNG, content_type="image/png")


@override_settings(
    MEDIA_ROOT=_TEMP_MEDIA,
    USE_LOCAL_MEDIA=True,
    # STORAGES is built once at settings import, so USE_LOCAL_MEDIA alone does
    # not undo a B2 .env - without this the suite uploads to the real bucket.
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
    },
)
class BulkUploadTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_superuser("root", "root@example.com", "pw")
        cls.anime = Category.objects.create(name="Anime", position=0)
        cls.add_url = reverse("admin:wallpapers_wallpaper_add")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(_TEMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.client.force_login(self.staff)
        # The database rolls back between tests but the media directory does
        # not, and "nothing was written" is only meaningful against an empty one.
        for entry in os.listdir(_TEMP_MEDIA):
            shutil.rmtree(os.path.join(_TEMP_MEDIA, entry), ignore_errors=True)

    def post(self, files, category=None, follow=False):
        data = {"files": files}
        data["category"] = self.anime.pk if category is None else category
        return self.client.post(self.add_url, data, follow=follow)

    def media_files(self):
        found = []
        for root, _dirs, names in os.walk(_TEMP_MEDIA):
            found.extend(os.path.join(root, name) for name in names)
        return found

    # --- form shape ---------------------------------------------------

    def test_add_form_takes_many_files_and_nothing_else(self):
        body = self.client.get(self.add_url).content.decode()
        self.assertIn('name="files"', body)
        self.assertIn("multiple", body)
        for absent in ('name="title"', 'name="priority"', 'name="is_premium"',
                       'name="is_published"', 'name="file"'):
            self.assertNotIn(absent, body)

    def test_change_form_is_untouched(self):
        wallpaper = Wallpaper.objects.create(category=self.anime, file=png())
        url = reverse("admin:wallpapers_wallpaper_change", args=[wallpaper.pk])
        body = self.client.get(url).content.decode()
        for present in ('name="title"', 'name="file"', 'name="priority"',
                        'name="is_premium"', 'name="is_published"'):
            self.assertIn(present, body)
        self.assertNotIn('name="files"', body)

    # --- happy path ---------------------------------------------------

    def test_three_files_become_three_wallpapers(self):
        response = self.post([png("a.png"), png("b.png"), png("c.png")])

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"], reverse("admin:wallpapers_wallpaper_changelist")
        )
        self.assertEqual(Wallpaper.objects.count(), 3)
        for wallpaper in Wallpaper.objects.all():
            self.assertEqual(wallpaper.category, self.anime)
            self.assertTrue(wallpaper.is_published)
            self.assertFalse(wallpaper.is_premium)
            self.assertEqual(wallpaper.priority, 0)

    def test_title_comes_from_the_filename_without_its_extension(self):
        self.post([SimpleUploadedFile("my shot.png", PNG, content_type="image/png")])

        wallpaper = Wallpaper.objects.get()
        self.assertEqual(wallpaper.title, "my shot")
        # Proves save() ran: bulk_create would leave this null.
        self.assertEqual(wallpaper.original_filename, "my shot.png")

    def test_media_type_is_derived_per_file(self):
        self.post([
            png("a.png"),
            SimpleUploadedFile("anim.gif", GIF, content_type="image/gif"),
            SimpleUploadedFile("clip.mp4", MP4, content_type="video/mp4"),
        ])

        by_title = {w.title: w.media_type for w in Wallpaper.objects.all()}
        self.assertEqual(by_title, {"a": "image", "anim": "gif", "clip": "video"})

    def test_upload_path_uses_the_chosen_category(self):
        # Fails as wallpaper/uncategorized/... if category is not set first.
        self.post([png("a.png")])
        self.assertRegex(Wallpaper.objects.get().file.name, UPLOAD_PATH)

    def test_long_filename_is_clipped_to_the_column_width(self):
        self.post([png("x" * 200 + ".png")])
        self.assertEqual(len(Wallpaper.objects.get().title), 150)

    def test_exactly_the_maximum_is_accepted(self):
        response = self.post([png(f"{i}.png") for i in range(MAX_FILES_PER_UPLOAD)])

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Wallpaper.objects.count(), MAX_FILES_PER_UPLOAD)

    # --- rejected batches write nothing -------------------------------

    def assertRejected(self, response, message):
        self.assertEqual(response.status_code, 200)  # re-rendered, not redirected
        self.assertContains(response, message)
        self.assertEqual(Wallpaper.objects.count(), 0)
        self.assertEqual(self.media_files(), [])

    def test_over_the_maximum_is_refused(self):
        response = self.post([png(f"{i}.png") for i in range(MAX_FILES_PER_UPLOAD + 1)])
        self.assertRejected(response, "at most 10 files")

    def test_one_bad_extension_rejects_the_whole_batch(self):
        response = self.post([
            png("good.png"),
            SimpleUploadedFile("clip.avi", b"AVI ", content_type="video/x-msvideo"),
            png("also-good.png"),
        ])
        self.assertRejected(response, "clip.avi")

    def test_every_bad_file_is_named_at_once(self):
        response = self.post([
            SimpleUploadedFile("one.avi", b"AVI ", content_type="video/x-msvideo"),
            SimpleUploadedFile("two.bmp", b"BM", content_type="image/bmp"),
        ])
        self.assertContains(response, "one.avi")
        self.assertContains(response, "two.bmp")
        self.assertEqual(Wallpaper.objects.count(), 0)

    def test_no_files_is_a_validation_error_not_a_crash(self):
        response = self.client.post(self.add_url, {"category": self.anime.pk})
        self.assertRejected(response, "required")

    def test_category_is_required(self):
        response = self.post([png("a.png")], category="")
        self.assertRejected(response, "required")

    # --- admin chrome -------------------------------------------------

    def test_batch_reports_how_many_were_added(self):
        response = self.post([png("a.png"), png("b.png"), png("c.png")], follow=True)

        messages = [str(m) for m in response.context["messages"]]
        self.assertEqual(messages, ["3 wallpapers added to Anime."])

    def test_single_file_keeps_djangos_own_message(self):
        response = self.post([png("a.png")], follow=True)

        message = str(list(response.context["messages"])[0])
        self.assertIn("added successfully", message)

    def test_every_row_gets_its_own_history_entry(self):
        self.post([png("a.png"), png("b.png"), png("c.png")])

        logged = LogEntry.objects.filter(action_flag=ADDITION)
        self.assertEqual(logged.count(), 3)
        self.assertEqual(
            {int(entry.object_id) for entry in logged},
            set(Wallpaper.objects.values_list("pk", flat=True)),
        )

    # --- permissions --------------------------------------------------

    def test_staff_without_add_permission_is_refused(self):
        viewer = User.objects.create_user("viewer", password="pw", is_staff=True)
        viewer.user_permissions.add(
            Permission.objects.get(codename="view_wallpaper")
        )
        self.client.force_login(viewer)

        self.assertEqual(self.client.get(self.add_url).status_code, 403)
        self.assertEqual(self.post([png("a.png")]).status_code, 403)
        self.assertEqual(Wallpaper.objects.count(), 0)

    def test_anonymous_is_sent_to_the_login_page(self):
        self.client.logout()
        response = self.client.get(self.add_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    # --- the change form still saves the old way ----------------------

    def test_editing_a_title_leaves_the_file_alone(self):
        wallpaper = Wallpaper.objects.create(
            category=self.anime,
            file=SimpleUploadedFile("keep.png", PNG, content_type="image/png"),
        )
        stored_name = wallpaper.file.name

        response = self.client.post(
            reverse("admin:wallpapers_wallpaper_change", args=[wallpaper.pk]),
            {
                "category": self.anime.pk,
                "title": "renamed",
                "priority": 0,
                "is_published": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        wallpaper.refresh_from_db()
        self.assertEqual(wallpaper.title, "renamed")
        self.assertEqual(wallpaper.file.name, stored_name)
        self.assertEqual(wallpaper.original_filename, "keep.png")


@override_settings(
    MEDIA_ROOT=_TEMP_MEDIA,
    USE_LOCAL_MEDIA=True,
    # STORAGES is built once at settings import, so USE_LOCAL_MEDIA alone does
    # not undo a B2 .env - without this the suite uploads to the real bucket.
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
    },
)
class VideoThumbnailTests(TestCase):
    """Images derive a thumbnail; a video's is uploaded by hand."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_superuser("thumbroot", "t@example.com", "pw")
        cls.anime = Category.objects.create(name="Anime", position=0)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(_TEMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.client.force_login(self.staff)

    def make(self, payload=MP4, name="clip.mp4", content_type="video/mp4"):
        return Wallpaper.objects.create(
            category=self.anime,
            file=SimpleUploadedFile(name, payload, content_type=content_type),
        )

    def change(self, wallpaper, **extra):
        data = {
            "category": self.anime.pk,
            "title": wallpaper.title or "",
            "priority": 0,
            "is_published": "on",
        }
        data.update(extra)
        return self.client.post(
            reverse("admin:wallpapers_wallpaper_change", args=[wallpaper.pk]), data
        )

    def test_an_image_derives_its_own_thumbnail(self):
        wallpaper = self.make(payload=photo(), name="shot.png", content_type="image/png")

        self.assertTrue(wallpaper.thumbnail)
        self.assertTrue(wallpaper.thumbnail.name.endswith(".webp"))

    def test_a_derived_thumbnail_is_at_most_a_third_of_the_upload(self):
        payload = photo()
        wallpaper = self.make(payload=payload, name="shot.png", content_type="image/png")

        self.assertLessEqual(wallpaper.thumbnail.size, len(payload) // 3)

    def test_a_video_derives_nothing(self):
        # No ffmpeg, so a video's poster frame stays a manual upload.
        wallpaper = self.make()
        self.assertFalse(wallpaper.thumbnail)

    def test_a_hand_picked_thumbnail_is_never_overwritten(self):
        chosen = SimpleUploadedFile("poster.png", PNG, content_type="image/png")
        wallpaper = Wallpaper.objects.create(
            category=self.anime,
            file=SimpleUploadedFile("shot.png", photo(), content_type="image/png"),
            thumbnail=chosen,
        )
        self.assertTrue(wallpaper.thumbnail.name.endswith(".png"))

    # --- the admin form -----------------------------------------------

    def test_change_form_offers_a_thumbnail_field(self):
        wallpaper = self.make()
        body = self.client.get(
            reverse("admin:wallpapers_wallpaper_change", args=[wallpaper.pk])
        ).content.decode()
        self.assertIn('name="thumbnail"', body)

    def test_a_video_accepts_a_thumbnail(self):
        wallpaper = self.make()
        response = self.change(
            wallpaper,
            thumbnail=SimpleUploadedFile("poster.png", PNG, content_type="image/png"),
        )

        self.assertEqual(response.status_code, 302)
        wallpaper.refresh_from_db()
        self.assertTrue(wallpaper.thumbnail)

    def test_an_image_also_accepts_a_chosen_thumbnail(self):
        wallpaper = self.make(payload=photo(), name="shot.png", content_type="image/png")
        response = self.change(
            wallpaper,
            thumbnail=SimpleUploadedFile("poster.png", PNG, content_type="image/png"),
        )

        self.assertEqual(response.status_code, 302)
        wallpaper.refresh_from_db()
        self.assertTrue(wallpaper.thumbnail.name.endswith(".png"))

    def test_a_video_thumbnail_must_be_a_still_image(self):
        wallpaper = self.make()
        response = self.change(
            wallpaper,
            thumbnail=SimpleUploadedFile("poster.mp4", MP4, content_type="video/mp4"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "not allowed")
        wallpaper.refresh_from_db()
        self.assertFalse(wallpaper.thumbnail)

    def test_the_thumbnail_stays_optional(self):
        wallpaper = self.make()
        response = self.change(wallpaper)

        self.assertEqual(response.status_code, 302)
        wallpaper.refresh_from_db()
        self.assertFalse(wallpaper.thumbnail)
