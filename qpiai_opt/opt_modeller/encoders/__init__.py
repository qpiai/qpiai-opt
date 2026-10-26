"""Variable-domain encoders used by polynomial transformations."""

from .continuous import encode_continuous
from .dispatcher import dispatch_encoders, get_encoder_mapping
from .fraction import encode_fraction
from .integer import encode_integer_binary

__all__ = [
    "encode_integer_binary",
    "encode_continuous",
    "encode_fraction",
    "dispatch_encoders",
    "get_encoder_mapping",
]
