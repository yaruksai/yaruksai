# pipeline-engine/app/boot_lock.py
"""
YARUKSAİ — Boot Integrity Lock (Hard-Lock Mechanism)
═════════════════════════════════════════════════════
Sistem açılırken TÜM kaynak dosyaların SHA-256 hash'ini
doğrular. Sapma eşiğini aşarsa sistemi KİLİTLER.

"Güvenini kaybeden sistem, çalışmamalıdır."
"""

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ─── Config ───────────────────────────────────────────────────

# CEO TALİMATI: TEK BİT FARKI → SİSTEM AÇILMASIN
# Yüzde eşiği YOK. Hash eşleşir ya da eşleşmez.
BOOT_LOCK_THRESHOLD = 0.0  # SIFIR TOLERANS

# Hangi uzantılar kontrol edilecek
WATCHED_EXTENSIONS = {".py", ".ts", ".json"}

# Hangi dizinler atlanacak
SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".next", "dist",
    "build", ".venv", "venv", "artifacts", "data",
}

# Genesis manifest dosya adı
GENESIS_FILENAME = "genesis_manifest.json"


class BootIntegrityError(Exception):
    """Boot bütünlük hatası — sistem kilitlenmeli."""
    pass


# ─── Hash Computation ─────────────────────────────────────────

def _sha256_file(filepath: Path) -> str:
    """Dosyanın SHA-256 hash'ini hesapla."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_source_hashes(root_dir: Path) -> Dict[str, str]:
    """
    Belirtilen kök dizinden itibaren tüm izlenen dosyaların
    SHA-256 hash'lerini hesapla.

    Returns: {"relative/path/to/file.py": "sha256hex", ...}
    """
    hashes = {}
    root = root_dir.resolve()

    for dirpath, dirnames, filenames in os.walk(root):
        # Skip belirli dizinleri
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]

        current = Path(dirpath)
        for fname in sorted(filenames):
            fpath = current / fname
            if fpath.suffix not in WATCHED_EXTENSIONS:
                continue

            rel_path = str(fpath.relative_to(root))
            try:
                hashes[rel_path] = _sha256_file(fpath)
            except (OSError, IOError):
                continue

    return hashes


# ─── Genesis Manifest ─────────────────────────────────────────

def save_genesis_manifest(root_dir: Path, data_dir: Optional[Path] = None) -> Path:
    """
    İlk çalıştırmada genesis_manifest.json oluştur.
    Bu dosya sistemin "doğum belgesi"dir.
    """
    if data_dir is None:
        data_dir = Path(os.getenv("ADMIN_DB", "/app/data/admin.db")).parent

    data_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = data_dir / GENESIS_FILENAME

    hashes = compute_source_hashes(root_dir)

    manifest = {
        "version": "1.0.0",
        "created_at": time.time(),
        "created_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "root_dir": str(root_dir),
        "total_files": len(hashes),
        "threshold": BOOT_LOCK_THRESHOLD,
        "hashes": hashes,
        "meta_hash": hashlib.sha256(
            json.dumps(hashes, sort_keys=True).encode()
        ).hexdigest(),
    }

    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"[BOOT_LOCK] 🔐 Genesis manifest oluşturuldu: {len(hashes)} dosya mühürlendi")
    print(f"[BOOT_LOCK] 📄 Dosya: {manifest_path}")
    return manifest_path


def load_genesis_manifest(data_dir: Optional[Path] = None) -> Optional[Dict]:
    """Genesis manifest'i yükle. Yoksa None döndür."""
    if data_dir is None:
        data_dir = Path(os.getenv("ADMIN_DB", "/app/data/admin.db")).parent

    manifest_path = data_dir / GENESIS_FILENAME
    if not manifest_path.exists():
        return None

    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError):
        return None


# ─── Boot Verification ────────────────────────────────────────

def verify_boot_integrity(
    root_dir: Optional[Path] = None,
    data_dir: Optional[Path] = None,
    auto_genesis: bool = True,
) -> Dict:
    """
    Boot sırasında kaynak dosya bütünlüğünü doğrula.

    Returns: Doğrulama raporu
    Raises: BootIntegrityError if drift > threshold
    """
    if root_dir is None:
        # Pipeline-engine kök dizini
        root_dir = Path(__file__).resolve().parent.parent

    if data_dir is None:
        data_dir = Path(os.getenv("ADMIN_DB", "/app/data/admin.db")).parent

    # Genesis manifest yükle
    genesis = load_genesis_manifest(data_dir)

    if genesis is None:
        if auto_genesis:
            print("[BOOT_LOCK] ⚡ Genesis manifest bulunamadı — ilk çalıştırma, oluşturuluyor...")
            save_genesis_manifest(root_dir, data_dir)
            return {
                "status": "GENESIS_CREATED",
                "locked": False,
                "message": "İlk boot — genesis manifest oluşturuldu",
                "drift_pct": 0.0,
            }
        else:
            raise BootIntegrityError("Genesis manifest bulunamadı ve auto_genesis=False")

    # Mevcut hash'leri hesapla
    current_hashes = compute_source_hashes(root_dir)
    genesis_hashes = genesis.get("hashes", {})

    # Karşılaştır
    changed_files: List[str] = []
    new_files: List[str] = []
    deleted_files: List[str] = []

    for fpath, ghash in genesis_hashes.items():
        if fpath not in current_hashes:
            deleted_files.append(fpath)
        elif current_hashes[fpath] != ghash:
            changed_files.append(fpath)

    for fpath in current_hashes:
        if fpath not in genesis_hashes:
            new_files.append(fpath)

    total_genesis = len(genesis_hashes)
    total_drift = len(changed_files) + len(deleted_files)
    drift_pct = (total_drift / max(total_genesis, 1))
    threshold = genesis.get("threshold", BOOT_LOCK_THRESHOLD)

    # SIFIR TOLERANS: tek dosya farkı bile → HARD_LOCK
    is_locked = total_drift > 0

    report = {
        "status": "HARD_LOCK" if is_locked else "PASS",
        "locked": is_locked,
        "drift_pct": round(drift_pct * 100, 2),
        "threshold_pct": 0.0,
        "genesis_files": total_genesis,
        "current_files": len(current_hashes),
        "changed_files": changed_files,
        "new_files": new_files,
        "deleted_files": deleted_files,
        "total_drift": total_drift,
        "verified_at": time.time(),
        "verified_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    if is_locked:
        errmsg = (
            f"[BOOT_LOCK] 🔒 HARD-LOCK: {total_drift} dosya genesis'ten farklı. "
            f"Değişen: {len(changed_files)}, Silinen: {len(deleted_files)}. "
            f"SIFIR TOLERANS — SİSTEM KİLİTLENDİ."
        )
        print(errmsg)
        for cf in changed_files:
            print(f"  ⚠️ DEĞİŞMİŞ: {cf}")
        for df in deleted_files:
            print(f"  ❌ SİLİNMİŞ: {df}")

        raise BootIntegrityError(errmsg)

    else:
        print(f"[BOOT_LOCK] ✅ Boot integrity: TÜM {total_genesis} dosya doğrulandı — SIFIR sapma")

    return report


def regenerate_genesis(root_dir: Optional[Path] = None, data_dir: Optional[Path] = None) -> Path:
    """
    Bilinçli genesis yenileme — deploy sonrası çağrılır.
    Yeni dosya hash'lerini kaydeder.
    """
    if root_dir is None:
        root_dir = Path(__file__).resolve().parent.parent
    return save_genesis_manifest(root_dir, data_dir)
