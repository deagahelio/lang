from .backend_base import BaseBackend
from .backend_c import CBackend

BACKEND_MAP = {
    "base": BaseBackend,
    "c": CBackend
}