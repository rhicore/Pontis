"""Pontis metadata extractor

Usage:
    from extractor import extract
    extract("./my_data")
"""
from extractor.registry import extract
from extractor.utils import Config

__all__ = ['extract', 'Config']
