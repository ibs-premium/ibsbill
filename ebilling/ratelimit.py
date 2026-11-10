"""A small per-IP rate limiter for the public API endpoints (uses the shared cache)."""
import functools

from django.core.cache import cache
from django.http import JsonResponse


def rate_limit(name, limit, seconds=60):
    """Allow `limit` calls per `seconds` from one IP address; answer 429 beyond that."""
    def decorator(view):
        @functools.wraps(view)
        def wrapper(request, *args, **kwargs):
            from .views import client_ip  # imported here to avoid a circular import
            key = f'rl:{name}:{client_ip(request)}'
            cache.add(key, 0, seconds)
            try:
                count = cache.incr(key)
            except ValueError:  # the key expired between add() and incr()
                cache.set(key, 1, seconds)
                count = 1
            if count > limit:
                response = JsonResponse({'error': 'Too many requests. Slow down and try again shortly.'},
                                        status=429)
                response['Retry-After'] = str(seconds)
                return response
            return view(request, *args, **kwargs)
        return wrapper
    return decorator
