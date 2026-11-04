"""The privacy scan that runs on every push (samples are split so this file stays clean for the scan)."""

from __future__ import annotations

import importlib.util
import sys

import pytest

from jobradar import config

spec = importlib.util.spec_from_file_location("scan_public", config.ROOT / "dev" / "scan_public.py")
scan_public = importlib.util.module_from_spec(spec)
sys.modules["scan_public"] = scan_public
spec.loader.exec_module(scan_public)


def _kinds(root, line: str, terms: set[str] | None = None) -> list[str]:
    (root / "file.py").write_text(line, encoding="utf-8")
    return [kind for _, _, kind in scan_public.scan(root, terms or set())]


@pytest.mark.parametrize(
    "line, kind",
    [
        ("ana" + "@" + "gmail.com", "email"),
        ("+56 9 8" + "765 4321", "phone"),
        ("linkedin.com/in/" + "janedoe", "linkedin_profile"),
        ("sk-" + "a" * 20, "api_key"),
        ("GOCSPX" + "-abc", "api_key"),
        ("postgresql://" + "bob:secret" + "@db.internal/app", "database_url"),
        ("C:" + "\\Users\\bob", "windows_path"),
        ('"' + "1aB" * 11 + '"', "google_id"),
    ],
)
def test_private_data_is_found(tmp_path, line, kind):
    assert kind in _kinds(tmp_path, line)


@pytest.mark.parametrize(
    "line",
    [
        "ana" + "@acme.example",
        "you" + "@example.com",
        "+56 9 1234 5678",
        "linkedin.com/in/" + "example-ana",
        "postgresql://user:password" + "@host:5432/db",
        '"' + "idx_a_long_lowercase_index_name_here" + '"',
    ],
)
def test_fictional_data_passes(tmp_path, line):
    assert _kinds(tmp_path, line) == []


def test_the_denylist_ignores_case_accents_and_separators(tmp_path):
    denylist = tmp_path / "list.txt"
    denylist.write_text("# a comment\nÁcme Corp\n56987654321\n", encoding="utf-8")
    folder = tmp_path / "copy"
    folder.mkdir()
    terms = scan_public.load_denylist(denylist)
    assert _kinds(folder, "from ACME  corp, the best", terms) == ["denylist"]
    assert _kinds(folder, "acme corporate", terms) == []
    assert _kinds(folder, "a=+56 9 8" + "765 4321", terms) == ["phone", "denylist"]


def test_only_the_copyright_line_of_the_license_may_name_the_author(tmp_path):
    (tmp_path / "LICENSE").write_text("Copyright (c) 2026 Ana Pérez\nAna Pérez\n", encoding="utf-8")
    (tmp_path / "file.py").write_text("Copyright (c) 2026 Ana Pérez\n", encoding="utf-8")
    findings = scan_public.scan(tmp_path, {"ana perez"})
    assert sorted(findings) == [("LICENSE", 2, "denylist"), ("file.py", 1, "denylist")]


@pytest.mark.parametrize("name", ["cv.pdf", ".env", "token_gmail.json", "data/x.txt", "secrets/key.txt"])
def test_private_files_are_forbidden(tmp_path, name):
    (tmp_path / name).parent.mkdir(exist_ok=True)
    (tmp_path / name).write_text("x", encoding="utf-8")
    assert [kind for _, _, kind in scan_public.scan(tmp_path, set())] in (["forbidden_file"], ["forbidden_folder"])


def test_the_command_fails_without_printing_what_it_found(tmp_path, capsys):
    secret = "sk-" + "a" * 20
    (tmp_path / "file.py").write_text(f"key = '{secret}'", encoding="utf-8")
    assert scan_public.main(["--path", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert "file.py:1: api_key" in captured.out and secret not in captured.out + captured.err
    (tmp_path / "file.py").write_text("key = None", encoding="utf-8")
    assert scan_public.main(["--path", str(tmp_path)]) == 0
