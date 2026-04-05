"""Pontis metadata extractor

Usage:
    from extractor import extract
    extract("./my_data")
"""
from extractor.extract import extract
from extractor.utils import Config, NodeRef, VFSStorage

__all__ = ['extract', 'Config', 'NodeRef', 'VFSStorage']
