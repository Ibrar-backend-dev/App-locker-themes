"""Paging for the category feed envelope.

DRF's PageNumberPagination is the wrong shape here on three counts: it emits
count/next/previous rather than our ``meta``, it silently clamps an oversized
page_size where the contract wants a 400, and it 404s past the last page where
the contract wants an empty list.
"""

from rest_framework.exceptions import ValidationError

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


def _positive_int(raw, name, default):
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValidationError(f"{name} must be an integer.")
    if value < 1:
        raise ValidationError(f"{name} must be 1 or greater.")
    return value


def paginate(queryset, request):
    """Return ``(items, meta)`` for the given queryset and request."""
    params = request.query_params
    page = _positive_int(params.get("page"), "page", 1)
    page_size = _positive_int(params.get("page_size"), "page_size", DEFAULT_PAGE_SIZE)
    if page_size > MAX_PAGE_SIZE:
        raise ValidationError(f"page_size may not exceed {MAX_PAGE_SIZE}.")

    total = queryset.count()
    start = (page - 1) * page_size
    # Past the last page this slices to [], which is what the contract asks for.
    items = list(queryset[start : start + page_size])

    meta = {
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_next": start + len(items) < total,
    }
    return items, meta
