"""Local song analysis library and job state.

The first implementation deliberately keeps the library and its protocol small.
Heavy audio models belong in a separate worker and publish results through the
same store.
"""

from .store import SongStore

__all__ = ["SongStore"]
