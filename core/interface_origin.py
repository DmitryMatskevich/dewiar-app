"""Host-owned command origin, never reconstructed from user/model arguments."""
from contextlib import contextmanager
from contextvars import ContextVar

_authenticated=ContextVar('dewiar_authenticated_interface',default=False)


@contextmanager
def authenticated_interface():
    token=_authenticated.set(True)
    try:yield
    finally:_authenticated.reset(token)


def require_interface():
    if _authenticated.get() is not True:raise ValueError('Откройте подключение через авторизованный интерфейс Dewiar')
