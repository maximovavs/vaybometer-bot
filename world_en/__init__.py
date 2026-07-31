"""World image-generation package hooks."""

from . import imagegen as _imagegen
from .image_content_guard import install_imagegen_guard as _install_imagegen_guard

_install_imagegen_guard(_imagegen)

del _imagegen
del _install_imagegen_guard
