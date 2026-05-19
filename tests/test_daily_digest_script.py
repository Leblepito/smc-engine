"""scripts/daily_digest.sh smoke testleri (İş 3 2026-05-19).

Bash script doğrudan execute edilmez (Windows CI'da bash hazır olmayabilir).
Bunun yerine script content sanity check + integration via subprocess
(POSIX shell varsa). Tüm test'ler skip-on-Windows guard'ı ile.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "daily_digest.sh"


def test_daily_digest_script_exists():
    assert SCRIPT.exists(), f"missing: {SCRIPT}"


def test_daily_digest_script_has_shebang_and_set_e():
    content = SCRIPT.read_text(encoding="utf-8")
    assert content.startswith("#!/usr/bin/env bash"), "Shebang eksik"
    assert "set -e" in content, "set -e (fail-fast) eksik"


def test_daily_digest_script_references_both_analyze_scripts():
    content = SCRIPT.read_text(encoding="utf-8")
    assert "scripts/analyze_signals.py" in content
    assert "scripts/analyze_trades.py" in content


def test_daily_digest_script_defaults_to_yesterday():
    """DATE param verilmezse '1 day ago' default'u kullanır."""
    content = SCRIPT.read_text(encoding="utf-8")
    assert "1 day ago" in content


def test_daily_digest_script_creates_symlink_to_latest():
    """digest-DATE.txt → latest.txt symlink (cat logs/digest/latest.txt)."""
    content = SCRIPT.read_text(encoding="utf-8")
    assert 'latest.txt' in content
    assert 'ln -sf' in content


def test_daily_digest_script_outputs_under_logs_digest():
    """Output dizini logs/digest/ olmalı."""
    content = SCRIPT.read_text(encoding="utf-8")
    assert 'logs/digest' in content
    assert 'mkdir -p' in content


def _bash_actually_works() -> bool:
    """Windows'ta `bash` WSL'i çağırıp 'no distro' hatası dönebilir.
    Gerçekten POSIX-uyumlu bash bulup çalıştırabildiğimizden emin ol."""
    if shutil.which("bash") is None:
        return False
    try:
        result = subprocess.run(
            ["bash", "-c", "echo hi"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0 and "hi" in result.stdout
    except Exception:
        return False


@pytest.mark.skipif(
    not _bash_actually_works(),
    reason="bash not actually executable (Windows without WSL or git-bash)",
)
def test_daily_digest_script_runs_dry_with_explicit_date(tmp_path, monkeypatch):
    """Script çalışabilir olmalı (bash present). Eksik venv/log dosyaları
    için fail-soft branch'leri devreye girer; exit 0 beklenir."""
    # Çalışma dizini repo köküne sabit
    repo_root = SCRIPT.parent.parent
    # logs/ var olmalı; sub-proje #2'den kalanlar veya boş — sorun değil.
    # Sadece scriptin exit 0 ürettiğini ve output yazdığını doğrula.
    env = os.environ.copy()
    result = subprocess.run(
        ["bash", str(SCRIPT), "2026-01-01"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    # Exit 0 (script set -e ama tüm alt komutlar fail-soft || echo)
    assert result.returncode == 0, (
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    # Output dosyası oluşmuş olmalı
    out_path = repo_root / "logs" / "digest" / "digest-2026-01-01.txt"
    assert out_path.exists(), f"digest output yok: {out_path}"
    content = out_path.read_text(encoding="utf-8")
    assert "SMC Engine — Daily Digest" in content
    assert "2026-01-01" in content
    # M5 code review: symlink fiilen oluşturuldu mu — I1 sessiz fail'ini
    # erken yakalar. Windows'ta git-bash symlink yerine hardlink/copy
    # üretebilir — is_symlink() veya exists() yeterli (her ikisi de OK).
    symlink = repo_root / "logs" / "digest" / "latest.txt"
    assert symlink.exists() or symlink.is_symlink(), (
        f"latest.txt symlink yok: {symlink}"
    )
    # Cleanup
    out_path.unlink(missing_ok=True)
    if symlink.exists() or symlink.is_symlink():
        symlink.unlink()
