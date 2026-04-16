"""Pontis metadata extractor

Usage:
    from extractor import extract
    extract("./my_data")
"""
from extractor.full_extract import extract
from extractor.modules._utils import Config

__all__ = ['extract', 'Config']
