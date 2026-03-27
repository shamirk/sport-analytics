"""Unit tests for app.services.cache.TTLCache."""
import time
from unittest.mock import patch

import pytest

from app.services.cache import TTLCache


@pytest.fixture
def cache():
    c = TTLCache()
    yield c
    c.clear()


class TestTTLCacheGet:
    def test_miss_returns_none(self, cache):
        assert cache.get("missing") is None

    def test_hit_returns_value(self, cache):
        cache.set("k", "v")
        assert cache.get("k") == "v"

    def test_expired_returns_none(self, cache):
        cache.set("k", "val", ttl=0.001)
        time.sleep(0.01)
        assert cache.get("k") is None

    def test_expired_key_is_removed(self, cache):
        cache.set("k", "val", ttl=0.001)
        time.sleep(0.01)
        cache.get("k")  # triggers removal
        assert "k" not in cache._store

    def test_not_expired_key_remains(self, cache):
        cache.set("k", "val", ttl=60)
        assert cache.get("k") == "val"

    def test_get_with_mocked_time_not_expired(self, cache):
        now = time.monotonic()
        with patch("app.services.cache.time") as mock_time:
            mock_time.monotonic.return_value = now
            cache.set("k", "val", ttl=10)
            mock_time.monotonic.return_value = now + 5
            assert cache.get("k") == "val"

    def test_get_with_mocked_time_expired(self, cache):
        now = time.monotonic()
        with patch("app.services.cache.time") as mock_time:
            mock_time.monotonic.return_value = now
            cache.set("k", "val", ttl=10)
            mock_time.monotonic.return_value = now + 11
            assert cache.get("k") is None


class TestTTLCacheSet:
    def test_set_stores_value(self, cache):
        cache.set("x", 42)
        assert cache.get("x") == 42

    def test_set_overwrites_existing(self, cache):
        cache.set("x", "old")
        cache.set("x", "new")
        assert cache.get("x") == "new"

    def test_set_stores_dict(self, cache):
        data = {"a": 1, "b": [1, 2, 3]}
        cache.set("data", data)
        assert cache.get("data") == data

    def test_set_stores_none_value(self, cache):
        cache.set("null", None)
        # None is a valid value — get returns the stored None
        # But our implementation returns None for both miss and stored-None.
        # Test that the key exists.
        assert "null" in cache._store


class TestTTLCacheDelete:
    def test_delete_removes_key(self, cache):
        cache.set("k", "v")
        cache.delete("k")
        assert cache.get("k") is None

    def test_delete_nonexistent_key_no_error(self, cache):
        cache.delete("never_existed")  # should not raise

    def test_delete_only_removes_target_key(self, cache):
        cache.set("a", 1)
        cache.set("b", 2)
        cache.delete("a")
        assert cache.get("b") == 2


class TestTTLCacheClear:
    def test_clear_removes_all_keys(self, cache):
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)
        cache.clear()
        assert cache.get("a") is None
        assert cache.get("b") is None
        assert cache.get("c") is None

    def test_clear_on_empty_no_error(self, cache):
        cache.clear()  # should not raise

    def test_can_set_after_clear(self, cache):
        cache.set("k", "v")
        cache.clear()
        cache.set("k", "new")
        assert cache.get("k") == "new"


class TestTTLCacheIsolation:
    def test_separate_instances_dont_share(self):
        c1 = TTLCache()
        c2 = TTLCache()
        c1.set("shared_key", "from_c1")
        assert c2.get("shared_key") is None
