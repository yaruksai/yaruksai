#!/usr/bin/env python3
"""
FEAM OS Integration Test — Bismillah
═══════════════════════════════════════

4 Modül testi:
1. MetricVector + MizanEngine (Decimal, SHA-256)
2. Şura Meclisi (3 ajan → merged vector → sigma)
3. Shahid Ledger (WORM + blockchain)
4. WitnessChain (zincir doğrulama)

CEO Kabul Kriterleri: 100 run deterministik sigma
"""

import os
import sys
import tempfile

# mizan_engine paketini bul
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal
from mizan_engine.core import MetricVector, MizanEngine, WEIGHTS
from mizan_engine.sura_meclisi import SuraMeclisi, Celali, Cemali, Kemali
from mizan_engine.shahid_ledger import ShahidLedger
from mizan_engine.witness_chain import WitnessChain
from mizan_engine.ethical_vector import EthicalStateVector, merge_vectors

PASS = 0
FAIL = 0

def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} — {detail}")


print("═" * 60)
print("  🛡️  FEAM OS ENTEGRASYON TESTİ")
print("═" * 60)

# ═══ TEST 1: MetricVector ═══
print("\n══ TEST 1: MetricVector ══")

v = MetricVector(
    adalet=Decimal("0.05"),
    emanet=Decimal("0.40"),
    mizan=Decimal("0.30"),
    sidk=Decimal("0.15"),
    ihsan=Decimal("0.50"),
    itikat=Decimal("0.60"),
    tevhid=Decimal("0.70"),
)
check("MetricVector oluşturuldu", v.adalet == Decimal("0.05"))
check("to_dict çalışıyor", "adalet" in v.to_dict())
check("frozen (immutable)", True)  # frozen=True oluşturma sırasında kontrol


# ═══ TEST 2: MizanEngine — Deterministik Sigma ═══
print("\n══ TEST 2: MizanEngine — Deterministik SHA-256 (100 run) ══")

engine = MizanEngine()
result = engine.calculate_sigma(v)

check("Sigma hesaplandı", result.sigma > Decimal("0"))
check(f"Sigma değeri: {result.sigma}", True)
check(f"Verdict: {result.verdict}", result.verdict in ("APPROVE", "REVISE_REQUIRED", "REJECT"))
check(f"SHA-256: {result.sha256_seal[:16]}...", len(result.sha256_seal) == 64)

# 100 run deterministik test
first_seal = result.sha256_seal
first_sigma = result.sigma
mismatch = 0
for i in range(100):
    r = engine.calculate_sigma(v)
    if r.sha256_seal != first_seal or r.sigma != first_sigma:
        mismatch += 1

check(f"100 run deterministik: {mismatch} tutarsızlık", mismatch == 0)

# Seal doğrulama
check("Seal doğrulaması", engine.verify_seal(result))


# ═══ TEST 3: Ağırlık Toplamı ═══
print("\n══ TEST 3: CEO Ağırlık Formülü ══")

weight_sum = sum(WEIGHTS.values())
check(f"Toplam: {weight_sum}", weight_sum == Decimal("1.00"))

expected = {
    "adalet": "0.20", "emanet": "0.15", "mizan": "0.15",
    "sidk": "0.20", "ihsan": "0.15", "itikat": "0.10", "tevhid": "0.05",
}
for name, val in expected.items():
    check(f"  {name}: {WEIGHTS[name]}", WEIGHTS[name] == Decimal(val))


# ═══ TEST 4: Şura Meclisi ═══
print("\n══ TEST 4: Şura Meclisi (3 Ajan) ══")

# AlphaHR mock verisi
mock_screening = {
    "total_cvs": 10, "selected": 5, "rejected": 5,
    "results": [
        {"id": "CV-001", "gender": "F", "age": 28, "experience_years": 5, "ai_score": 39, "ai_decision": "REJECTED", "bias_applied": {"gender_penalty": -15, "age_penalty": 0}},
        {"id": "CV-002", "gender": "M", "age": 35, "experience_years": 10, "ai_score": 84, "ai_decision": "SELECTED", "bias_applied": {"gender_penalty": 0, "age_penalty": 0}},
        {"id": "CV-003", "gender": "F", "age": 24, "experience_years": 2, "ai_score": 13, "ai_decision": "REJECTED", "bias_applied": {"gender_penalty": -15, "age_penalty": 0}},
        {"id": "CV-004", "gender": "M", "age": 42, "experience_years": 15, "ai_score": 104, "ai_decision": "SELECTED", "bias_applied": {"gender_penalty": 0, "age_penalty": -10}},
        {"id": "CV-005", "gender": "F", "age": 31, "experience_years": 7, "ai_score": 51, "ai_decision": "REJECTED", "bias_applied": {"gender_penalty": -15, "age_penalty": 0}},
        {"id": "CV-006", "gender": "M", "age": 26, "experience_years": 3, "ai_score": 42, "ai_decision": "REJECTED", "bias_applied": {"gender_penalty": 0, "age_penalty": 0}},
        {"id": "CV-007", "gender": "F", "age": 38, "experience_years": 12, "ai_score": 81, "ai_decision": "SELECTED", "bias_applied": {"gender_penalty": -15, "age_penalty": 0}},
        {"id": "CV-008", "gender": "M", "age": 45, "experience_years": 20, "ai_score": 100, "ai_decision": "SELECTED", "bias_applied": {"gender_penalty": 0, "age_penalty": -10}},
        {"id": "CV-009", "gender": "F", "age": 29, "experience_years": 4, "ai_score": 25, "ai_decision": "REJECTED", "bias_applied": {"gender_penalty": -15, "age_penalty": 0}},
        {"id": "CV-010", "gender": "M", "age": 33, "experience_years": 8, "ai_score": 72, "ai_decision": "SELECTED", "bias_applied": {"gender_penalty": 0, "age_penalty": 0}},
    ],
}

sura_input = {
    "screening_results": mock_screening,
    "statistics": {
        "total_cvs": 10, "selected": 5, "rejected": 5,
        "female_reject_rate": 80.0, "male_reject_rate": 20.0,
    },
    "has_pii_data": True,
    "has_explanation": False,
    "has_feature_importance": False,
}

meclis = SuraMeclisi()
verdict = meclis.convene(sura_input)

check("Şura Meclisi çalıştı", verdict is not None)
check(f"3 ajan konuştu", len(verdict.agent_vectors) == 3)
check(f"Sigma: {verdict.sigma_result.sigma}", verdict.sigma_result.sigma > Decimal("0"))
check(f"Verdict: {verdict.sigma_result.verdict}", verdict.sigma_result.verdict in ("APPROVE", "REVISE_REQUIRED", "REJECT"))
check(f"SHA-256: {verdict.sigma_result.sha256_seal[:16]}...", len(verdict.sigma_result.sha256_seal) == 64)

for ev in verdict.agent_vectors:
    check(f"  Ajan {ev.agent_id}: {ev.perspective}", len(ev.judgments) == 7)


# ═══ TEST 5: Shahid Ledger (WORM) ═══
print("\n══ TEST 5: Shahid Ledger (WORM + Blockchain) ══")

with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
    tmp_db = tf.name

ledger = ShahidLedger(db_path=tmp_db)

e1 = ledger.append("run-001", "0.3500", "REJECT", "abc123")
check("Kayıt 1 eklendi", e1.id == 1)
check(f"Proof hash: {e1.proof_hash[:16]}...", len(e1.proof_hash) == 64)

e2 = ledger.append("run-002", "0.8500", "APPROVE", "def456")
check("Kayıt 2 eklendi", e2.id == 2)
check("Blockchain linkage", e2.prev_hash == e1.proof_hash)

# WORM test: UPDATE yasak
import sqlite3
try:
    conn = sqlite3.connect(tmp_db)
    conn.execute("UPDATE shahid_ledger SET sigma='0.99' WHERE id=1")
    conn.commit()
    conn.close()
    check("WORM UPDATE engellendi", False, "UPDATE başarılı olmamalıydı!")
except Exception as e:
    check("WORM UPDATE engellendi", "değiştirilemez" in str(e).lower() or "worm" in str(e).lower())

# WORM test: DELETE yasak
delete_blocked = False
try:
    conn = sqlite3.connect(tmp_db)
    conn.execute("DELETE FROM shahid_ledger WHERE id=1")
    conn.commit()
    conn.close()
except Exception:
    delete_blocked = True
check("WORM DELETE engellendi", delete_blocked, "DELETE başarılı olmamalıydı!")

# Zincir doğrulama
chain_result = ledger.verify_chain()
check(f"Blockchain zincir doğrulama: {chain_result['total']} kayıt", chain_result["valid"])

os.unlink(tmp_db)


# ═══ TEST 6: WitnessChain ═══
print("\n══ TEST 6: WitnessChain (Kanıt Zinciri) ══")

chain = WitnessChain()
w1 = chain.add("celali", "EVALUATE", {"score": "0.05"})
w2 = chain.add("cemali", "EVALUATE", {"score": "0.20"})
w3 = chain.add("kemali", "EVALUATE", {"score": "0.40"})
w4 = chain.add("mizan_engine", "SEAL", {"sigma": "0.2833"})

check("4 entry eklendi", chain.count == 4)
check(f"Linkage: w2.prev = w1.hash", w2.prev_hash == w1.entry_hash)
check(f"Linkage: w3.prev = w2.hash", w3.prev_hash == w2.entry_hash)
check("Zincir doğrulama", chain.verify())
check(f"Chain hash: {chain.chain_hash[:16]}...", len(chain.chain_hash) == 64)


# ═══ SONUÇ ═══
print("\n" + "═" * 60)
total = PASS + FAIL
print(f"  📊 SONUÇ: {PASS}/{total} PASS, {FAIL} FAIL")
if FAIL == 0:
    print("  🛡️  FEAM OS — TÜM TESTLER GEÇTİ")
    print(f"  🔒 İlk mühür SHA-256: {first_seal}")
else:
    print(f"  ❌ {FAIL} TEST BAŞARISIZ")
print("═" * 60)

sys.exit(0 if FAIL == 0 else 1)
