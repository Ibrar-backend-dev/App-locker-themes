"""The category API contract and removed-route behavior.

The client parses a fixed shape, so these tests care about key *order* and null
semantics, not just "the right data came back".
"""

import json
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from wallpapers.models import Category, Wallpaper

# Smallest valid 1x1 PNG; the other bodies only need a plausible header.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000002000100ffff03000006000557bfabd4000000"
    "0049454e44ae426082"
)
MP4 = b"\x00\x00\x00\x18ftypmp42"
GIF = b"GIF89a"

HOST = "127.0.0.1:8000"

CATEGORY_KEYS = ["id", "name", "description", "position", "artwork_count"]
ARTWORK_KEYS = [
    "id",
    "category_id",
    "title",
    "media_type",
    "is_premium",
    "original_filename",
    "file_path",
    "created_at",
]
META_KEYS = ["page", "page_size", "total", "has_next"]

_TEMP_MEDIA = tempfile.mkdtemp()


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
class CategoryFeedContractTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.anime = Category.objects.create(
            name="Anime", description="Anime wallpapers wonderful styles .", position=0
        )
        cls.nature = Category.objects.create(name="Nature", position=1)  # blank description
        cls.hidden = Category.objects.create(name="Hidden", position=2, is_active=False)

        # Named, premium, png.
        cls.w_png = Wallpaper.objects.create(
            category=cls.anime,
            title="wallpaper",
            is_premium=True,
            file=SimpleUploadedFile("my shot.png", PNG, content_type="image/png"),
        )
        # Untitled, top priority, video.
        cls.w_mp4 = Wallpaper.objects.create(
            category=cls.anime,
            priority=5,
            file=SimpleUploadedFile("clip.mp4", MP4, content_type="video/mp4"),
        )
        cls.w_gif = Wallpaper.objects.create(
            category=cls.anime,
            title="loop",
            file=SimpleUploadedFile("anim.gif", GIF, content_type="image/gif"),
        )
        # Unpublished: must not surface anywhere, including artwork_count.
        cls.w_draft = Wallpaper.objects.create(
            category=cls.nature,
            title="unpublished",
            is_published=False,
            file=SimpleUploadedFile("x.png", PNG, content_type="image/png"),
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(_TEMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def get(self, path):
        response = self.client.get(path, HTTP_HOST=HOST)
        return response, json.loads(response.content)

    # --- 1. categories ---------------------------------------------------

    def test_category_feed_shape_and_key_order(self):
        response, body = self.get("/api/v1/wallpapers/categories")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(body), ["data", "meta"])
        self.assertEqual(list(body["data"][0]), CATEGORY_KEYS)
        self.assertEqual(list(body["meta"]), META_KEYS)
        self.assertEqual(body["meta"], {"page": 1, "page_size": 20, "total": 2, "has_next": False})

    def test_categories_are_active_only_and_newest_first(self):
        _, body = self.get("/api/v1/wallpapers/categories")
        # Descending id: the most recently added category leads the feed.
        self.assertEqual([c["id"] for c in body["data"]], [self.nature.id, self.anime.id])

    def test_artwork_count_counts_published_items_only(self):
        _, body = self.get("/api/v1/wallpapers/categories")
        rows = {c["id"]: c for c in body["data"]}
        self.assertEqual(rows[self.anime.id]["artwork_count"], 3)
        # Nature holds only an unpublished item.
        self.assertEqual(rows[self.nature.id]["artwork_count"], 0)

    def test_blank_description_is_null(self):
        _, body = self.get("/api/v1/wallpapers/categories")
        rows = {c["id"]: c for c in body["data"]}
        self.assertIsNone(rows[self.nature.id]["description"])

    def test_categories_carry_no_thumbnail(self):
        _, body = self.get("/api/v1/wallpapers/categories")
        self.assertEqual(set(body["data"][0]), set(CATEGORY_KEYS))

    def test_clearing_a_title_restores_the_filename(self):
        self.w_png.title = ""
        self.w_png.save()
        self.assertEqual(self.w_png.title, "my shot")

    def test_original_filename_is_kept(self):
        self.assertEqual(self.w_png.original_filename, "my shot.png")

    def test_wallpaper_list_without_category_id_returns_404(self):
        response = self.client.get("/api/v1/wallpapers", HTTP_HOST=HOST)
        self.assertEqual(response.status_code, 404)

    def test_artwork_by_category_response_shape_and_values(self):
        response, body = self.get(f"/api/v1/wallpapers?category_id={self.anime.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(body), ["data", "meta"])
        self.assertEqual(list(body["data"][0]), ARTWORK_KEYS)
        self.assertEqual(list(body["meta"]), META_KEYS)
        self.assertEqual(body["meta"]["total"], 3)

        row = next(item for item in body["data"] if item["id"] == self.w_png.id)
        self.assertEqual(row["category_id"], self.anime.id)
        self.assertEqual(row["title"], "my shot")
        self.assertEqual(row["media_type"], "image")
        self.assertTrue(row["is_premium"])
        self.assertEqual(row["original_filename"], "my shot.png")
        self.assertRegex(
            row["file_path"],
            r"^http://127\.0\.0\.1:8000/media/wallpaper/anime/assets/"
            r"\d{4}/\d{2}/[0-9a-f]{32}\.png$",
        )
        self.assertRegex(
            row["created_at"],
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+00:00$",
        )

    def test_artwork_by_category_excludes_unpublished_and_inactive_categories(self):
        _, body = self.get(f"/api/v1/wallpapers?category_id={self.nature.id}")
        self.assertEqual(body["data"], [])
        self.assertEqual(body["meta"]["total"], 0)

        _, body = self.get(f"/api/v1/wallpapers?category_id={self.hidden.id}")
        self.assertEqual(body["data"], [])

    def test_artwork_by_category_validates_category_id(self):
        for value in ("abc", "0", "-1"):
            with self.subTest(category_id=value):
                response = self.client.get(
                    f"/api/v1/wallpapers?category_id={value}", HTTP_HOST=HOST
                )
                self.assertEqual(response.status_code, 400)

    # --- paging ----------------------------------------------------------

    def test_page_size_over_the_cap_is_a_400_not_a_clamp(self):
        response, _ = self.get("/api/v1/wallpapers/categories?page_size=101")
        self.assertEqual(response.status_code, 400)

    def test_page_size_at_the_cap_is_accepted(self):
        response, _ = self.get("/api/v1/wallpapers/categories?page_size=100")
        self.assertEqual(response.status_code, 200)

    def test_page_past_the_end_is_an_empty_list_not_a_404(self):
        response, body = self.get("/api/v1/wallpapers/categories?page=9999")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["data"], [])
        self.assertFalse(body["meta"]["has_next"])

    def test_non_positive_or_non_numeric_paging_is_a_400(self):
        for query in ("page=0", "page=-1", "page=abc", "page_size=0", "page_size=abc"):
            with self.subTest(query=query):
                response, _ = self.get(f"/api/v1/wallpapers/categories?{query}")
                self.assertEqual(response.status_code, 400)
