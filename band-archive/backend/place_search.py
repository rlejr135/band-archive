"""Bounded, per-process controls for the public place-search proxy."""

import hashlib
import html
import math
import re
import threading
import time
from collections import OrderedDict, deque


class PlaceSearchGuard:
    """Bounded cache and request budgets for one Flask app process.

    Limits and cache entries are intentionally in-memory. They reset at process
    restart and are independent in each worker; they are not a distributed
    quota or a substitute for a provider-side account limit.
    """

    def __init__(self, *, request_limit=10, upstream_limit=60, window_seconds=60,
                 cache_ttl_seconds=30, cache_max_entries=512, max_subjects=4096,
                 max_concurrent_upstream=2, clock=time.monotonic):
        self.request_limit = max(1, int(request_limit))
        self.upstream_limit = max(1, int(upstream_limit))
        self.window_seconds = max(1, int(window_seconds))
        self.cache_ttl_seconds = max(1, int(cache_ttl_seconds))
        self.cache_max_entries = max(1, int(cache_max_entries))
        self.max_subjects = max(1, int(max_subjects))
        self._clock = clock
        self._lock = threading.RLock()
        self._requests_by_subject = {}
        self._upstream_calls = deque()
        self._cache = OrderedDict()
        self._upstream_slots = threading.BoundedSemaphore(max(1, int(max_concurrent_upstream)))

    @classmethod
    def from_config(cls, config):
        return cls(
            request_limit=config.get('PLACE_SEARCH_REQUEST_LIMIT', 10),
            upstream_limit=config.get('PLACE_SEARCH_UPSTREAM_LIMIT', 60),
            window_seconds=config.get('PLACE_SEARCH_WINDOW_SECONDS', 60),
            cache_ttl_seconds=config.get('PLACE_SEARCH_CACHE_TTL_SECONDS', 30),
            cache_max_entries=config.get('PLACE_SEARCH_CACHE_MAX_ENTRIES', 512),
            max_subjects=config.get('PLACE_SEARCH_MAX_SUBJECTS', 4096),
            max_concurrent_upstream=config.get('PLACE_SEARCH_MAX_CONCURRENT_UPSTREAM', 2),
        )

    @staticmethod
    def normalize_query(value):
        if not isinstance(value, str):
            raise ValueError('invalid query')
        if any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value):
            raise ValueError('invalid query')
        query = ' '.join(value.split())
        if len(query) > 100:
            raise ValueError('invalid query')
        return query

    @staticmethod
    def _subject_key(remote_addr):
        raw = remote_addr if isinstance(remote_addr, str) and remote_addr else 'unknown'
        return hashlib.sha256(raw.encode('utf-8', errors='replace')).hexdigest()

    def _prune_subjects(self, now):
        expired_before = now - self.window_seconds
        expired_keys = []
        for key, events in self._requests_by_subject.items():
            while events and events[0] <= expired_before:
                events.popleft()
            if not events:
                expired_keys.append(key)
        for key in expired_keys:
            del self._requests_by_subject[key]

    def allow_request(self, remote_addr):
        """Count each valid non-empty request against its socket peer address."""
        now = self._clock()
        key = self._subject_key(remote_addr)
        with self._lock:
            self._prune_subjects(now)
            events = self._requests_by_subject.get(key)
            if events is None:
                if len(self._requests_by_subject) >= self.max_subjects:
                    return False, self.window_seconds
                events = deque()
                self._requests_by_subject[key] = events
            if len(events) >= self.request_limit:
                retry_after = max(1, math.ceil(self.window_seconds - (now - events[0])))
                return False, retry_after
            events.append(now)
            return True, 0

    def get_cached(self, query):
        key = query.casefold()
        now = self._clock()
        with self._lock:
            item = self._cache.get(key)
            if item is None:
                return False, None
            expires_at, value = item
            if expires_at <= now:
                del self._cache[key]
                return False, None
            self._cache.move_to_end(key)
            return True, [dict(row) for row in value]

    def cache_results(self, query, results):
        key = query.casefold()
        now = self._clock()
        with self._lock:
            self._cache[key] = (now + self.cache_ttl_seconds, [dict(row) for row in results])
            self._cache.move_to_end(key)
            while len(self._cache) > self.cache_max_entries:
                self._cache.popitem(last=False)

    def reserve_upstream(self):
        """Reserve one bounded concurrent slot and app-wide provider call."""
        if not self._upstream_slots.acquire(blocking=False):
            return False, 1
        now = self._clock()
        expired_before = now - self.window_seconds
        with self._lock:
            while self._upstream_calls and self._upstream_calls[0] <= expired_before:
                self._upstream_calls.popleft()
            if len(self._upstream_calls) >= self.upstream_limit:
                retry_after = max(1, math.ceil(self.window_seconds - (now - self._upstream_calls[0])))
                self._upstream_slots.release()
                return False, retry_after
            self._upstream_calls.append(now)
        return True, 0

    def release_upstream(self):
        self._upstream_slots.release()


def sanitize_place_results(payload):
    """Return at most five short, JSON-safe provider fields."""
    if not isinstance(payload, dict) or not isinstance(payload.get('items'), list):
        raise ValueError('invalid provider response')

    def text_field(value, limit):
        if not isinstance(value, str):
            return ''
        value = ''.join(char for char in value if not (ord(char) < 32 or 127 <= ord(char) <= 159))
        return value[:limit]

    results = []
    for item in payload['items'][:5]:
        if not isinstance(item, dict):
            continue
        raw_title = text_field(item.get('title', ''), 512)
        # Naver wraps matching text in <b>; remove any markup before returning
        # a string, then decode entities as ordinary text.
        title = re.sub(r'<[^>]{0,128}>', '', raw_title)
        title = html.unescape(title)
        title = text_field(title, 200)
        raw_mapx = item.get('mapx', '')
        raw_mapy = item.get('mapy', '')
        mapx = raw_mapx if isinstance(raw_mapx, str) and len(raw_mapx) <= 12 else ''
        mapy = raw_mapy if isinstance(raw_mapy, str) and len(raw_mapy) <= 12 else ''
        results.append({
            'title': title,
            'address': text_field(item.get('address', ''), 300),
            'roadAddress': text_field(item.get('roadAddress', ''), 300),
            'mapx': mapx if re.fullmatch(r'[0-9]{1,12}', mapx) else '',
            'mapy': mapy if re.fullmatch(r'[0-9]{1,12}', mapy) else '',
        })
    return results
