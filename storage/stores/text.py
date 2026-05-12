"""Text source helpers shared by storage modules and extractors."""

from __future__ import annotations

import os

TEXT_EXTENSIONS = {
    ".txt", ".log", ".text", ".readme",
    ".md", ".markdown", ".rst", ".adoc", ".asciidoc",
    ".py", ".pyw", ".pyi",
    ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
    ".java", ".c", ".h", ".cpp", ".hpp", ".cc", ".hh", ".cxx", ".hxx",
    ".go", ".rs", ".rb", ".erb", ".php", ".phtml", ".swift", ".kt", ".kts",
    ".scala", ".sc", ".r", ".rmd", ".pl", ".pm", ".lua", ".elm",
    ".clj", ".cljs", ".ex", ".exs", ".erl", ".hrl", ".fs", ".fsx",
    ".hs", ".lhs", ".ml", ".mli", ".groovy", ".cs", ".csx", ".vb",
    ".vbs", ".dart", ".jl", ".nim", ".cr", ".d", ".f90", ".f95",
    ".f03", ".for", ".m", ".mm", ".ps1", ".psm1", ".psd1",
    ".sql", ".ddl", ".dml", ".json", ".jsonl", ".yaml", ".yml",
    ".xml", ".xsd", ".xslt", ".svg", ".toml", ".ini", ".cfg",
    ".conf", ".config", ".properties", ".csv", ".tsv", ".html", ".htm",
    ".xhtml", ".css", ".scss", ".sass", ".less", ".graphql", ".gql",
    ".proto", ".thrift", ".sh", ".bash", ".zsh", ".fish", ".csh",
    ".tcsh", ".ksh", ".bat", ".cmd", ".nt", ".vba", ".awk", ".sed",
    ".gitignore", ".gitattributes", ".gitmodules", ".dockerignore",
    ".editorconfig", ".npmignore", ".mk", ".mak", ".cmake", ".makefile",
    ".dockerfile", ".tex", ".latex", ".bib", ".po", ".pot", ".mo",
    ".strings", ".resx", ".resw", ".env", ".envrc", ".env.local",
    ".env.development", ".env.production", ".htaccess", ".htpasswd",
    ".srt", ".vtt", ".sub", ".ics", ".ical", ".diff", ".patch",
}

TEXT_FILENAMES = {
    "makefile", "rakefile", "gemfile", "vagrantfile", "dockerfile",
    "jenkinsfile", "brewfile", ".gitignore", ".gitattributes",
    ".gitmodules", ".dockerignore", ".editorconfig", ".npmignore",
    ".env", ".envrc", ".env.local", "readme", "license", "copying",
    "authors", "contributors", "changelog", "changes", "news", "history",
    "install", "setup", "configure", "manifest", "manifest.in",
    "requirements", "requirements-dev", "requirements-test", "pipfile",
    "poetry.lock", "yarn.lock", "package-lock.json", "cmakelists.txt",
    "cmakecache.txt",
}


def is_text_file(filename: str) -> bool:
    name_lower = filename.lower()
    base_name = os.path.splitext(name_lower)[0]
    ext = os.path.splitext(name_lower)[1]
    return ext in TEXT_EXTENSIONS or name_lower in TEXT_FILENAMES or base_name in TEXT_FILENAMES


__all__ = ["TEXT_EXTENSIONS", "TEXT_FILENAMES", "is_text_file"]
