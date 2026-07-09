"""Access seam re-exports (#784 / #796)."""

import housing_list_search.access as access


def test_access_exports_http_and_browser():
    assert callable(access.polite_get)
    assert callable(access.polite_post)
    assert callable(access.browser_page)
    assert callable(access.safe_goto)
    assert callable(access.validate_http_url)
    assert access.URLPolicyError is not None
