# pipeline-engine/src/memory/memory_store.py
"""
YARUKSAİ Kolektif Hafıza — Local Vector Memory Store
─────────────────────────────────────────────────────
Ajanların geçmiş kararlarından öğrenmesini sağlar.
Tüm veri sunucu içinde kalır — sıfır dış bağımlılık.

Mimari:
  - SQLite (yerleşik, sıfır kurulum)
  - TF-IDF benzeri basit benzerlik (numpy/scipy gerektirmez)
  - Her pipeline run sonunda otomatik kayıt
  - Sorgu: "Bu hedefe benzer geçmiş kararlar nelerdi?"

Prensip: Veri Egemenliği — hiçbir veri dışarıya çıkmaz.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional


# ── DB Path ──────────────────────────────────────────────────────
_DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "memory.db"


def _get_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """SQLite bağlantısı aç, tablo yoksa oluştur."""
    p = db_path or _DEFAULT_DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            goal TEXT NOT NULL,
            sigma REAL,
            verdict TEXT,
            compliance_score REAL,
            risk_level TEXT,
            final_decision TEXT,
            summary TEXT,
            tokens TEXT,
            created_at REAL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_memories_goal ON memories(goal)
    """)
    conn.commit()
    return conn


# ── Tokenizer (basit, bağımsız) ─────────────────────────────────

def _tokenize(text: str) -> List[str]:
    """Metin → kelime listesi (lowercase, noktalama temizle)."""
    text = text.lower()
    text = re.sub(r'[^\w\s]', ' ', text)
    words = text.split()
    # Stopwords (TR + EN minimal)
    stops = {'ve', 'bir', 'bu', 'da', 'de', 'ile', 'için', 'the', 'a', 'an', 'is', 'of', 'to', 'and', 'in', 'for'}
    return [w for w in words if len(w) > 2 and w not in stops]


def _tf_vector(tokens: List[str]) -> Dict[str, float]:
    """Token listesinden TF vektörü."""
    counts = Counter(tokens)
    total = len(tokens) if tokens else 1
    return {word: count / total for word, count in counts.items()}


def _cosine_similarity(vec_a: Dict[str, float], vec_b: Dict[str, float]) -> float:
    """İki TF vektörü arasındaki kosinüs benzerliği."""
    common = set(vec_a.keys()) & set(vec_b.keys())
    if not common:
        return 0.0
    dot = sum(vec_a[w] * vec_b[w] for w in common)
    mag_a = math.sqrt(sum(v ** 2 for v in vec_a.values()))
    mag_b = math.sqrt(sum(v ** 2 for v in vec_b.values()))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


# ── Public API ───────────────────────────────────────────────────

def store_memory(
    run_id: str,
    goal: str,
    sigma: float = 0.0,
    verdict: str = "",
    compliance_score: float = 0.0,
    risk_level: str = "",
    final_decision: str = "",
    summary: str = "",
    db_path: Optional[Path] = None,
) -> str:
    """
    Pipeline sonucunu hafızaya kaydet.
    Returns: memory_id (SHA-256)
    """
    tokens = _tokenize(f"{goal} {summary}")
    tokens_str = json.dumps(tokens, ensure_ascii=False)
    memory_id = hashlib.sha256(f"{run_id}:{goal}".encode()).hexdigest()[:16]

    conn = _get_db(db_path)
    try:
        conn.execute(
            """INSERT OR REPLACE INTO memories 
               (id, run_id, goal, sigma, verdict, compliance_score, risk_level, 
                final_decision, summary, tokens, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (memory_id, run_id, goal, sigma, verdict, compliance_score,
             risk_level, final_decision, summary, tokens_str, time.time()),
        )
        conn.commit()
        print(f"[HAFIZA] 💾 Kaydedildi: {memory_id} → '{goal[:50]}...'")
        return memory_id
    finally:
        conn.close()


def recall_similar(
    query: str,
    top_k: int = 3,
    min_similarity: float = 0.1,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """
    Verilen sorguya en benzer geçmiş kararları getir.
    TF-IDF kosinüs benzerliği ile sıralama yapar.
    """
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []

    query_vec = _tf_vector(query_tokens)

    conn = _get_db(db_path)
    try:
        rows = conn.execute(
            "SELECT id, run_id, goal, sigma, verdict, compliance_score, "
            "risk_level, final_decision, summary, tokens, created_at FROM memories"
        ).fetchall()
    finally:
        conn.close()

    results = []
    for row in rows:
        try:
            stored_tokens = json.loads(row[9])
        except Exception:
            stored_tokens = _tokenize(row[2])

        stored_vec = _tf_vector(stored_tokens)
        sim = _cosine_similarity(query_vec, stored_vec)

        if sim >= min_similarity:
            results.append({
                "memory_id": row[0],
                "run_id": row[1],
                "goal": row[2],
                "sigma": row[3],
                "verdict": row[4],
                "compliance_score": row[5],
                "risk_level": row[6],
                "final_decision": row[7],
                "summary": row[8],
                "similarity": round(sim, 4),
                "created_at": row[10],
            })

    results.sort(key=lambda x: x["similarity"], reverse=True)
    return results[:top_k]


def get_memory_stats(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Hafıza istatistikleri."""
    conn = _get_db(db_path)
    try:
        total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        avg_sigma = conn.execute("SELECT AVG(sigma) FROM memories").fetchone()[0] or 0
        verdicts = conn.execute(
            "SELECT verdict, COUNT(*) FROM memories GROUP BY verdict"
        ).fetchall()
        return {
            "total_memories": total,
            "avg_sigma": round(avg_sigma, 4),
            "verdict_distribution": {v: c for v, c in verdicts},
        }
    finally:
        conn.close()


def get_db_path() -> str:
    """Hafıza DB dosya yolunu döndür."""
    return str(_DEFAULT_DB_PATH)


def format_memories_for_prompt(memories: List[Dict], max_chars: int = 500) -> str:
    """
    Geçmiş kararları ajan prompt'una eklenecek formatta döndür.
    Ajanlar bu bilgiyle daha iyi karar verir.
    """
    if not memories:
        return ""

    lines = ["[GEÇMİŞ KARARLAR — Kolektif Hafıza]"]
    total = 0
    for m in memories:
        line = (
            f"• Hedef: {m['goal'][:80]} | σ={m['sigma']:.2f} | "
            f"Karar: {m['verdict']} | Uyumluluk: {m.get('compliance_score', 'N/A')}%"
        )
        if total + len(line) > max_chars:
            break
        lines.append(line)
        total += len(line)

    return "\n".join(lines)
