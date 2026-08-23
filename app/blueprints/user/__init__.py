# -*- coding: utf-8 -*-
"""User blueprint package."""
from flask import Blueprint

user_bp = Blueprint(
    'user',
    __name__,
    url_prefix='/user',
)

from . import forms, profile, reservations  # noqa: E402,F401

__all__ = ['user_bp']
