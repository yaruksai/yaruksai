# pipeline-engine/app/pdf_engine.py
"""
YARUKSAİ — PDF Audit Certificate Engine
════════════════════════════════════════
Generates "YARUKSAİ Etik Uyum Sertifikası" — professional,
sealed PDF certificates for every pipeline verdict.

VERICORE Inside branding + SHA-256 cryptographic seal.
"""

import os
import io
import time
import hashlib
import json
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm, cm
from reportlab.lib.colors import HexColor, white, black
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    Image, HRFlowable, PageBreak
)
from reportlab.graphics.shapes import Drawing, Circle, String, Line, Rect
from reportlab.graphics.charts.piecharts import Pie

# ─── Colors (Navy Blue & Silver Corporate) ─────────────────────
NAVY_DEEP = HexColor("#0a1628")
NAVY_MID = HexColor("#111d33")
NAVY_LIGHT = HexColor("#1a2a44")
SILVER = HexColor("#c0c8d8")
ROYAL_BLUE = HexColor("#3b82f6")
GOLD = HexColor("#d4a853")
SUCCESS_GREEN = HexColor("#22c55e")
WARNING_AMBER = HexColor("#f59e0b")
DANGER_RED = HexColor("#ef4444")
TEXT_WHITE = HexColor("#e8ecf4")
TEXT_DIM = HexColor("#8892a8")

# ─── Default weights for 7 principles ─────────────────────────
DEFAULT_PRINCIPLES = {
    "adalet":  {"label": "Adalet (العدل)", "w": 0.18},
    "tevhid":  {"label": "Tevhid (التوحيد)", "w": 0.18},
    "emanet":  {"label": "Emanet (الأمانة)", "w": 0.14},
    "mizan":   {"label": "Mizan (الميزان)", "w": 0.14},
    "sidk":    {"label": "Sıdk (الصدق)", "w": 0.12},
    "ihsan":   {"label": "İhsan (الإحسان)", "w": 0.12},
    "itikat":  {"label": "İtikat (الاعتقاد)", "w": 0.12},
}


def _score_color(score: float) -> HexColor:
    """Return color based on score threshold."""
    if score >= 0.7:
        return SUCCESS_GREEN
    elif score >= 0.4:
        return WARNING_AMBER
    return DANGER_RED


def _verdict_text(verdict: str) -> str:
    """Human-readable verdict."""
    mapping = {
        "APPROVE": "✅ ONAYLANDI (APPROVED)",
        "APPROVE_WITH_CONDITIONS": "⚠️ KOŞULLU ONAY (CONDITIONAL)",
        "REJECT": "❌ REDDEDİLDİ (REJECTED)",
    }
    return mapping.get(verdict, verdict or "—")


def _build_header(elements: list, run_id: str, ts: float):
    """Build certificate header with branding."""
    styles = getSampleStyleSheet()

    # Title style
    title_style = ParagraphStyle(
        "CertTitle", parent=styles["Heading1"],
        fontName="Helvetica-Bold", fontSize=22,
        textColor=NAVY_DEEP, alignment=TA_CENTER,
        spaceAfter=2*mm,
    )
    # Subtitle
    sub_style = ParagraphStyle(
        "CertSub", parent=styles["Normal"],
        fontName="Helvetica", fontSize=10,
        textColor=TEXT_DIM, alignment=TA_CENTER,
        spaceAfter=4*mm,
    )
    # VERICORE badge
    vericore_style = ParagraphStyle(
        "Vericore", parent=styles["Normal"],
        fontName="Helvetica-Bold", fontSize=9,
        textColor=ROYAL_BLUE, alignment=TA_CENTER,
        spaceAfter=6*mm,
    )

    elements.append(Spacer(1, 10*mm))
    elements.append(Paragraph("🛡️ YARUKSAİ", title_style))
    elements.append(Paragraph("ETİK UYUM SERTİFİKASI", ParagraphStyle(
        "ST2", parent=styles["Heading2"], fontName="Helvetica-Bold",
        fontSize=14, textColor=NAVY_MID, alignment=TA_CENTER, spaceAfter=3*mm,
    )))
    elements.append(Paragraph("━" * 60, ParagraphStyle(
        "Sep", parent=styles["Normal"], fontName="Helvetica",
        fontSize=8, textColor=SILVER, alignment=TA_CENTER, spaceAfter=3*mm
    )))
    elements.append(Paragraph("✓ VERICORE Inside — AI Ethics Verification Engine", vericore_style))

    # Meta info
    date_str = datetime.fromtimestamp(ts).strftime("%d/%m/%Y %H:%M UTC")
    meta_style = ParagraphStyle(
        "Meta", parent=styles["Normal"], fontName="Helvetica",
        fontSize=8, textColor=TEXT_DIM, alignment=TA_CENTER,
    )
    elements.append(Paragraph(f"Rapor ID: {run_id}  |  Tarih: {date_str}", meta_style))
    elements.append(Spacer(1, 6*mm))


def _build_summary_table(elements: list, goal: str, sigma: float, verdict: str, compliance_score: float):
    """Build the summary section with scores."""
    styles = getSampleStyleSheet()

    section_title = ParagraphStyle(
        "SectionTitle", parent=styles["Heading2"],
        fontName="Helvetica-Bold", fontSize=13,
        textColor=NAVY_DEEP, spaceAfter=4*mm,
    )
    elements.append(Paragraph("📋 DENETIM ÖZETİ", section_title))

    # Summary table
    data = [
        ["Hedef (Goal)", Paragraph(goal[:300], ParagraphStyle("GoalText", fontSize=9, fontName="Helvetica"))],
        ["Mizan σ Skoru", f"{sigma:.4f}"],
        ["Karar (Verdict)", _verdict_text(verdict)],
        ["EU AI Act Uyum", f"{compliance_score:.1%}" if compliance_score else "—"],
    ]

    score_color = _score_color(sigma)

    table = Table(data, colWidths=[45*mm, 120*mm])
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (0, -1), NAVY_MID),
        ("TEXTCOLOR", (1, 0), (1, -1), black),
        ("BACKGROUND", (0, 0), (-1, -1), HexColor("#f8f9fc")),
        ("GRID", (0, 0), (-1, -1), 0.5, SILVER),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    elements.append(table)
    elements.append(Spacer(1, 8*mm))


def _build_principles_table(elements: list, weights: Dict[str, Any]):
    """Build the 7 principles weight table."""
    styles = getSampleStyleSheet()

    section_title = ParagraphStyle(
        "SectionTitle2", parent=styles["Heading2"],
        fontName="Helvetica-Bold", fontSize=13,
        textColor=NAVY_DEEP, spaceAfter=4*mm,
    )
    elements.append(Paragraph("⚖️ 7AI ŞÛRÂ PRENSİPLERİ", section_title))

    header = ["Prensip", "Ağırlık", "Katkı"]
    data = [header]

    for key, info in weights.items():
        label = info.get("label", key)
        w = info.get("w", 0)
        bar = "█" * int(w * 50) + "░" * (50 - int(w * 50))
        data.append([
            label,
            f"{w:.2%}",
            bar[:25],
        ])

    table = Table(data, colWidths=[55*mm, 25*mm, 85*mm])
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, 0), NAVY_MID),
        ("TEXTCOLOR", (0, 0), (-1, 0), white),
        ("TEXTCOLOR", (0, 1), (-1, -1), black),
        ("BACKGROUND", (0, 1), (-1, -1), HexColor("#f8f9fc")),
        ("GRID", (0, 0), (-1, -1), 0.5, SILVER),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("ALIGN", (1, 0), (1, -1), "CENTER"),
    ]))
    elements.append(table)
    elements.append(Spacer(1, 8*mm))


def _build_compliance_section(elements: list, compliance_data: Optional[Dict]):
    """Build EU AI Act compliance mapping section."""
    styles = getSampleStyleSheet()

    if not compliance_data:
        return

    section_title = ParagraphStyle(
        "SectionTitle3", parent=styles["Heading2"],
        fontName="Helvetica-Bold", fontSize=13,
        textColor=NAVY_DEEP, spaceAfter=4*mm,
    )
    elements.append(Paragraph("🇪🇺 EU AI ACT UYUM HARİTASI", section_title))

    # Summary
    summary = compliance_data.get("compliance_summary", {})
    risk = summary.get("risk_classification", "—")
    status = summary.get("status", "—")
    score = summary.get("overall_score", 0)

    info_style = ParagraphStyle("Info", fontSize=9, fontName="Helvetica", textColor=black)
    elements.append(Paragraph(f"<b>Risk Sınıfı:</b> {risk}  |  <b>Durum:</b> {status}  |  <b>Skor:</b> {score:.1%}", info_style))
    elements.append(Spacer(1, 4*mm))

    # Articles table
    articles = compliance_data.get("article_assessments", [])
    if articles:
        header = ["Madde", "Başlık", "Durum", "Skor"]
        data = [header]
        for art in articles[:15]:  # Max 15
            data.append([
                str(art.get("article", "")),
                str(art.get("title", ""))[:40],
                art.get("status", "—"),
                f"{art.get('score', 0):.0%}",
            ])

        table = Table(data, colWidths=[20*mm, 70*mm, 35*mm, 20*mm])
        table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("BACKGROUND", (0, 0), (-1, 0), NAVY_MID),
            ("TEXTCOLOR", (0, 0), (-1, 0), white),
            ("GRID", (0, 0), (-1, -1), 0.5, SILVER),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ]))
        elements.append(table)

    elements.append(Spacer(1, 8*mm))


def _build_council_table(elements: list, votes: list):
    """Build the 7-agent council deliberation table."""
    styles = getSampleStyleSheet()

    section_title = ParagraphStyle(
        "CouncilTitle", parent=styles["Heading2"],
        fontName="Helvetica-Bold", fontSize=13,
        textColor=NAVY_DEEP, spaceAfter=4*mm,
    )
    elements.append(Paragraph("🛡️ ŞÛRA KONSEYİ — 7 AJAN ANALİZİ", section_title))

    reason_style = ParagraphStyle(
        "ReasonText", fontName="Helvetica", fontSize=7,
        textColor=HexColor("#333333"), leading=9,
    )
    verdict_style_cell = ParagraphStyle(
        "VerdictCell", fontName="Helvetica-Bold", fontSize=8,
        textColor=black, alignment=TA_CENTER,
    )

    header = ["Ajan", "σ", "Karar", "Gerekçe"]
    data = [header]

    for vote in votes:
        agent = vote.get("agent", "—")
        score = vote.get("score", 0)
        v = vote.get("verdict", "—")
        reasoning = vote.get("reasoning", "—")
        # Truncate reasoning to fit PDF
        if len(reasoning) > 200:
            reasoning = reasoning[:197] + "..."

        data.append([
            agent,
            f"{score:.2f}",
            v,
            Paragraph(reasoning, reason_style),
        ])

    col_widths = [22*mm, 14*mm, 18*mm, 111*mm]
    table = Table(data, colWidths=col_widths)

    # Color verdict cells
    table_style_cmds = [
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (2, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("FONTSIZE", (0, 1), (2, -1), 8),
        ("BACKGROUND", (0, 0), (-1, 0), NAVY_MID),
        ("TEXTCOLOR", (0, 0), (-1, 0), white),
        ("BACKGROUND", (0, 1), (-1, -1), HexColor("#f8f9fc")),
        ("GRID", (0, 0), (-1, -1), 0.5, SILVER),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("ALIGN", (1, 0), (1, -1), "CENTER"),
        ("ALIGN", (2, 0), (2, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]

    # Color-code verdict and score cells per row
    for i, vote in enumerate(votes):
        row = i + 1
        score = vote.get("score", 0)
        v = vote.get("verdict", "")
        sc = _score_color(score)
        table_style_cmds.append(("TEXTCOLOR", (1, row), (1, row), sc))

        if v == "REJECT":
            table_style_cmds.append(("TEXTCOLOR", (2, row), (2, row), DANGER_RED))
        elif v == "CAUTION" or v == "DEFER":
            table_style_cmds.append(("TEXTCOLOR", (2, row), (2, row), WARNING_AMBER))
        else:
            table_style_cmds.append(("TEXTCOLOR", (2, row), (2, row), SUCCESS_GREEN))

    table.setStyle(TableStyle(table_style_cmds))
    elements.append(table)
    elements.append(Spacer(1, 6*mm))


def _build_deliberation_section(elements: list, deliberation: dict, synthesis: str):
    """Build the council deliberation pairs and mizan synthesis."""
    styles = getSampleStyleSheet()

    section_title = ParagraphStyle(
        "DelibTitle", parent=styles["Heading2"],
        fontName="Helvetica-Bold", fontSize=12,
        textColor=NAVY_DEEP, spaceAfter=4*mm,
    )
    elements.append(Paragraph("📋 KONSEY İSTİŞARE ÖZETİ", section_title))

    pair_label = ParagraphStyle(
        "PairLabel", fontName="Helvetica-Bold", fontSize=8,
        textColor=ROYAL_BLUE, spaceAfter=1*mm,
    )
    pair_text = ParagraphStyle(
        "PairText", fontName="Helvetica", fontSize=7.5,
        textColor=HexColor("#333333"), leading=10, spaceAfter=3*mm,
    )

    pair_names = {
        "tevhid_adalet_sirri": "Tevhid + Adalet (Bütünlük–Adalet Dengesi)",
        "merhamet_emanet_sirri": "Merhamet + Emanet (İnsaniyet–Güven Dengesi)",
        "ihsan_sidk_sirri": "İhsan + Sıdk (Mükemmellik–Doğruluk Dengesi)",
    }

    for key, label in pair_names.items():
        val = deliberation.get(key, "")
        if val:
            if len(val) > 250:
                val = val[:247] + "..."
            elements.append(Paragraph(label, pair_label))
            elements.append(Paragraph(val, pair_text))

    # Mizan Synthesis
    if synthesis:
        elements.append(Spacer(1, 2*mm))
        synth_label = ParagraphStyle(
            "SynthLabel", fontName="Helvetica-Bold", fontSize=9,
            textColor=GOLD, spaceAfter=2*mm,
        )
        synth_text = ParagraphStyle(
            "SynthText", fontName="Helvetica", fontSize=8,
            textColor=HexColor("#222222"), leading=11, spaceAfter=4*mm,
        )
        elements.append(Paragraph("⚖️ MİZAN SENTEZİ", synth_label))
        synth_short = synthesis[:500] + "..." if len(synthesis) > 500 else synthesis
        elements.append(Paragraph(synth_short, synth_text))

    elements.append(Spacer(1, 4*mm))


def _build_evidence_section(elements: list, result_seal: str, metrics: dict):
    """Build EVIDENCE_PACK reference section."""
    styles = getSampleStyleSheet()

    section_title = ParagraphStyle(
        "EvidenceTitle", parent=styles["Heading2"],
        fontName="Helvetica-Bold", fontSize=12,
        textColor=NAVY_DEEP, spaceAfter=4*mm,
    )
    elements.append(Paragraph("📦 EVIDENCE_PACK", section_title))

    data = [
        ["Alan", "Değer", "Yasal Dayanak"],
        ["schema_version", "EVIDENCE_PACK v1.0", "EU AI Act Art. 11"],
        ["mizan_trace", "7 ajan kararı + σ skoru + gerekçe zinciri", "EU AI Act Art. 13"],
        ["result_seal", result_seal[:32] + "..." if len(result_seal) > 32 else result_seal, "EU AI Act Art. 17"],
        ["fayda", str(metrics.get("fayda", "—")), "GDPR Art. 22"],
        ["seffaflik", str(metrics.get("seffaflik", "—")), "EU AI Act Art. 13"],
        ["sozlesme", str(metrics.get("sozlesme", "—")), "EU AI Act Art. 16"],
        ["israf", str(metrics.get("israf", "—")), "DSA Art. 34"],
    ]

    table = Table(data, colWidths=[30*mm, 85*mm, 40*mm])
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("BACKGROUND", (0, 0), (-1, 0), NAVY_MID),
        ("TEXTCOLOR", (0, 0), (-1, 0), white),
        ("BACKGROUND", (0, 1), (-1, -1), HexColor("#f8f9fc")),
        ("GRID", (0, 0), (-1, -1), 0.5, SILVER),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    elements.append(table)
    elements.append(Spacer(1, 6*mm))


def _build_seal_footer(elements: list, run_id: str, ts: float, all_data: Dict):
    """Build SHA-256 cryptographic seal footer."""
    styles = getSampleStyleSheet()

    elements.append(HRFlowable(width="100%", color=SILVER, thickness=1))
    elements.append(Spacer(1, 4*mm))

    # Create seal
    seal_input = json.dumps({
        "run_id": run_id,
        "timestamp": ts,
        "issuer": "yaruksai-vericore",
    }, sort_keys=True, ensure_ascii=False)
    seal_hash = hashlib.sha256(seal_input.encode()).hexdigest()

    seal_style = ParagraphStyle(
        "Seal", parent=styles["Normal"],
        fontName="Courier", fontSize=7,
        textColor=TEXT_DIM, alignment=TA_CENTER,
    )
    elements.append(Paragraph("🔐 KRİPTOGRAFİK MÜHÜR (SHA-256)", ParagraphStyle(
        "SealTitle", fontName="Helvetica-Bold", fontSize=9,
        textColor=NAVY_MID, alignment=TA_CENTER, spaceAfter=2*mm,
    )))
    elements.append(Paragraph(seal_hash, seal_style))
    elements.append(Spacer(1, 3*mm))

    # Disclaimer
    disclaimer = ParagraphStyle(
        "Disclaimer", fontName="Helvetica", fontSize=6,
        textColor=TEXT_DIM, alignment=TA_CENTER,
    )
    elements.append(Paragraph(
        "Bu sertifika YARUKSAİ VERICORE motoru tarafından otomatik olarak üretilmiştir. "
        "Kriptografik mühür, belge bütünlüğünü garanti eder. "
        "Doğrulama: verify.yaruksai.com",
        disclaimer
    ))
    elements.append(Spacer(1, 2*mm))
    elements.append(Paragraph(
        f"© {datetime.now().year} YARUKSAİ — LIGHT FOR AI · PROOF FOR HUMANS",
        ParagraphStyle("Footer", fontName="Helvetica-Bold", fontSize=7,
                       textColor=ROYAL_BLUE, alignment=TA_CENTER)
    ))


def generate_certificate(
    run_id: str,
    goal: str = "",
    sigma: float = 0.0,
    verdict: str = "",
    compliance_score: float = 0.0,
    compliance_data: Optional[Dict] = None,
    weights: Optional[Dict] = None,
    ts: Optional[float] = None,
) -> bytes:
    """
    Generate a YARUKSAİ Etik Uyum Sertifikası PDF.
    Returns PDF as bytes.
    """
    if weights is None:
        weights = DEFAULT_PRINCIPLES
    if ts is None:
        ts = time.time()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=15*mm, bottomMargin=15*mm,
        leftMargin=20*mm, rightMargin=20*mm,
        title=f"YARUKSAİ Sertifika — {run_id}",
        author="YARUKSAİ VERICORE",
        subject="Etik Uyum Sertifikası",
    )

    elements = []

    _build_header(elements, run_id, ts)
    _build_summary_table(elements, goal, sigma, verdict, compliance_score)
    _build_principles_table(elements, weights)
    _build_compliance_section(elements, compliance_data)
    _build_seal_footer(elements, run_id, ts, {
        "goal": goal, "sigma": sigma, "verdict": verdict
    })

    doc.build(elements)
    return buf.getvalue()


def generate_council_certificate(evaluate_result: Dict) -> bytes:
    """
    Generate a full Şûra Konseyi PDF certificate from evaluate() output.
    Input: raw JSON from POST /api/evaluate
    Output: VERICORE-stamped PDF with 7-agent table, σ score,
            deliberation summary, EVIDENCE_PACK, EU AI Act reference.
    """
    ts = time.time()
    run_id = f"CERT-{hashlib.sha256(json.dumps(evaluate_result, sort_keys=True).encode()).hexdigest()[:12].upper()}"

    sigma = evaluate_result.get("sigma_score", 0.0)
    verdict = evaluate_result.get("verdict", "—")
    votes = evaluate_result.get("votes", [])
    deliberation = evaluate_result.get("council_deliberation", {})
    synthesis = evaluate_result.get("mizan_synthesis", "")
    metrics = evaluate_result.get("metrics_parsed", {})
    result_seal = evaluate_result.get("result_seal", "—")
    timestamp_iso = evaluate_result.get("timestamp_iso", "")

    # Determine narrative/goal from context or first vote
    goal = evaluate_result.get("_goal", "Şûra Konseyi Etik Değerlendirmesi")

    # Verdict mapping for summary
    verdict_display = {
        "PERMIT": "✅ ONAYLANDI (PERMIT)",
        "DEFER": "⚠️ İNSAN ONAYI GEREKLİ (DEFER)",
        "REJECT": "❌ REDDEDİLDİ (REJECT)",
        "CAUTION": "⚠️ DİKKAT (CAUTION)",
    }.get(verdict, verdict)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=15*mm, bottomMargin=15*mm,
        leftMargin=15*mm, rightMargin=15*mm,
        title=f"YARUKSAİ Şûra Sertifikası — {run_id}",
        author="YARUKSAİ VERICORE",
        subject="Şûra Konseyi Etik Değerlendirme Sertifikası",
    )

    elements = []
    styles = getSampleStyleSheet()

    # ── Header ──
    _build_header(elements, run_id, ts)

    # ── Summary ──
    _build_summary_table(elements, goal, sigma, verdict, 0.0)

    # ── Council Votes Table (7 agents) ──
    if votes:
        _build_council_table(elements, votes)

    # ── Deliberation Pairs ──
    if deliberation:
        _build_deliberation_section(elements, deliberation, synthesis)

    # ── EVIDENCE_PACK ──
    if metrics and result_seal:
        _build_evidence_section(elements, result_seal, metrics)

    # ── Seal Footer ──
    _build_seal_footer(elements, run_id, ts, evaluate_result)

    doc.build(elements)
    return buf.getvalue()


def generate_victim_report(report_data: Dict[str, Any]) -> bytes:
    """
    Generate a Digital Victim Report PDF — VERICORE sealed.
    Documents AI-caused harm with EU AI Act legal references.

    Input: {
        report_id, victim_name, victim_contact, ai_system_name,
        ai_system_provider, decision_date, harm_type, harm_description,
        evidence_summary, audit_result (from /v1/audit), recommended_actions
    }
    """
    ts = time.time()
    report_id = report_data.get("report_id", f"DVR-{hashlib.sha256(str(ts).encode()).hexdigest()[:10].upper()}")

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=15*mm, bottomMargin=15*mm,
        leftMargin=18*mm, rightMargin=18*mm,
        title=f"YARUKSAI Digital Victim Report — {report_id}",
        author="YARUKSAI VERICORE",
        subject="AI Harm Documentation & EU AI Act Violation Report",
    )

    elements = []
    styles = getSampleStyleSheet()

    # ── Header ──
    title_style = ParagraphStyle(
        "DVRTitle", parent=styles["Heading1"],
        fontName="Helvetica-Bold", fontSize=20,
        textColor=DANGER_RED, alignment=TA_CENTER,
        spaceAfter=2*mm,
    )
    sub_style = ParagraphStyle(
        "DVRSub", parent=styles["Normal"],
        fontName="Helvetica-Bold", fontSize=12,
        textColor=NAVY_DEEP, alignment=TA_CENTER,
        spaceAfter=3*mm,
    )
    vericore_style = ParagraphStyle(
        "DVRVericore", parent=styles["Normal"],
        fontName="Helvetica-Bold", fontSize=9,
        textColor=ROYAL_BLUE, alignment=TA_CENTER,
        spaceAfter=6*mm,
    )

    elements.append(Spacer(1, 8*mm))
    elements.append(Paragraph("⚠️ DIGITAL VICTIM REPORT", title_style))
    elements.append(Paragraph("AI Decision Harm Documentation", sub_style))
    elements.append(Paragraph("━" * 60, ParagraphStyle(
        "Sep", fontName="Helvetica", fontSize=8,
        textColor=SILVER, alignment=TA_CENTER, spaceAfter=3*mm
    )))
    elements.append(Paragraph("✓ VERICORE Sealed — Cryptographic Evidence Chain", vericore_style))

    # Meta
    date_str = datetime.fromtimestamp(ts).strftime("%d/%m/%Y %H:%M UTC")
    meta_style = ParagraphStyle("Meta", fontSize=8, textColor=TEXT_DIM, alignment=TA_CENTER)
    elements.append(Paragraph(f"Report ID: {report_id}  |  Generated: {date_str}", meta_style))
    elements.append(Spacer(1, 6*mm))

    # ── Section style ──
    section_style = ParagraphStyle(
        "SectionHead", fontName="Helvetica-Bold", fontSize=12,
        textColor=NAVY_DEEP, spaceAfter=3*mm,
    )
    body_style = ParagraphStyle(
        "BodyText", fontName="Helvetica", fontSize=9,
        textColor=black, leading=12, spaceAfter=2*mm,
    )

    # ── 1. INCIDENT OVERVIEW ──
    elements.append(Paragraph("📋 1. INCIDENT OVERVIEW", section_style))
    incident_data = [
        ["Field", "Details"],
        ["Victim Name", report_data.get("victim_name", "Anonymous")],
        ["Contact", report_data.get("victim_contact", "Withheld")],
        ["AI System", report_data.get("ai_system_name", "Unknown AI System")],
        ["Provider", report_data.get("ai_system_provider", "Unknown Provider")],
        ["Decision Date", report_data.get("decision_date", "Not specified")],
        ["Harm Type", report_data.get("harm_type", "Unspecified")],
        ["Report Status", "FILED — Pending Review"],
    ]
    table = Table(incident_data, colWidths=[40*mm, 125*mm])
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (1, 1), (1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (-1, 0), NAVY_MID),
        ("TEXTCOLOR", (0, 0), (-1, 0), white),
        ("TEXTCOLOR", (0, 1), (-1, -1), black),
        ("BACKGROUND", (0, 1), (-1, -1), HexColor("#f8f9fc")),
        ("GRID", (0, 0), (-1, -1), 0.5, SILVER),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    elements.append(table)
    elements.append(Spacer(1, 6*mm))

    # ── 2. HARM DESCRIPTION ──
    elements.append(Paragraph("📝 2. HARM DESCRIPTION", section_style))
    harm_desc = report_data.get("harm_description", "No description provided.")
    elements.append(Paragraph(harm_desc[:2000], body_style))
    elements.append(Spacer(1, 4*mm))

    # ── 3. EVIDENCE SUMMARY ──
    elements.append(Paragraph("🔍 3. EVIDENCE SUMMARY", section_style))
    evidence = report_data.get("evidence_summary", "No evidence provided.")
    elements.append(Paragraph(evidence[:2000], body_style))
    elements.append(Spacer(1, 4*mm))

    # ── 4. YARUKSAI AUDIT RESULT ──
    audit = report_data.get("audit_result", {})
    if audit:
        elements.append(Paragraph("⚖️ 4. YARUKSAI INTEGRITY AUDIT", section_style))
        audit_data = [
            ["Metric", "Value"],
            ["INTEGRITY_INDEX", str(audit.get("INTEGRITY_INDEX", audit.get("mizan_score", "N/A")))],
            ["Verdict", str(audit.get("verdict", "N/A"))],
            ["Red Veto Triggered", str(audit.get("red_veto_triggered", "N/A"))],
            ["Triggered Rules", str(len(audit.get("triggered_rules", audit.get("issues", []))))],
            ["SHA-256 Seal", str(audit.get("ledger_seal", audit.get("sha256_seal", "")))[:40] + "..."],
            ["Engine", str(audit.get("engine_version", audit.get("engine", "YARUKSAI v1.0")))],
        ]
        at = Table(audit_data, colWidths=[45*mm, 120*mm])
        at.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
            ("FONTNAME", (1, 1), (1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("BACKGROUND", (0, 0), (-1, 0), NAVY_MID),
            ("TEXTCOLOR", (0, 0), (-1, 0), white),
            ("GRID", (0, 0), (-1, -1), 0.5, SILVER),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ]))
        elements.append(at)
        elements.append(Spacer(1, 4*mm))

        # Triggered rules detail
        rules = audit.get("triggered_rules", audit.get("issues", []))
        if rules:
            elements.append(Paragraph("Triggered Violation Rules:", ParagraphStyle(
                "RulesHead", fontName="Helvetica-Bold", fontSize=9,
                textColor=DANGER_RED, spaceAfter=2*mm,
            )))
            for i, rule in enumerate(rules[:10]):
                if isinstance(rule, dict):
                    rule_text = f"#{i+1} [{rule.get('severity', rule.get('category',''))}] {rule.get('name', rule.get('problem', str(rule)))}"
                else:
                    rule_text = f"#{i+1} {rule}"
                elements.append(Paragraph(rule_text[:200], ParagraphStyle(
                    f"Rule{i}", fontName="Helvetica", fontSize=8,
                    textColor=HexColor("#333333"), leftIndent=10*mm, spaceAfter=1*mm,
                )))
            elements.append(Spacer(1, 4*mm))

    # ── 5. EU AI ACT VIOLATIONS ──
    elements.append(Paragraph("🇪🇺 5. EU AI ACT VIOLATION MAPPING", section_style))

    harm_type = report_data.get("harm_type", "").lower()
    violations = []

    # Map harm type to likely violations
    violation_db = {
        "discrimination": [
            {"article": "Art. 5(1)(a)", "title": "Prohibited AI Practices", "desc": "AI systems deploying subliminal or manipulative techniques causing harm."},
            {"article": "Art. 10", "title": "Data & Data Governance", "desc": "Training data must be representative and free from bias."},
            {"article": "Art. 14", "title": "Human Oversight", "desc": "High-risk AI must allow effective human oversight."},
        ],
        "privacy": [
            {"article": "Art. 10(5)", "title": "Data Governance", "desc": "Special categories of personal data processing restrictions."},
            {"article": "Art. 13", "title": "Transparency", "desc": "Users must be informed about AI processing of their data."},
            {"article": "GDPR Art. 22", "title": "Automated Decision-Making", "desc": "Right not to be subject to solely automated decisions with legal effects."},
        ],
        "employment": [
            {"article": "Art. 6(2)", "title": "High-Risk Classification", "desc": "AI in employment/worker management is classified as high-risk."},
            {"article": "Art. 13", "title": "Transparency", "desc": "Deployers must inform natural persons subject to high-risk AI."},
            {"article": "Art. 14", "title": "Human Oversight", "desc": "Employment AI decisions require human review capability."},
            {"article": "Art. 26", "title": "Deployer Obligations", "desc": "Deployers must monitor AI and report incidents."},
        ],
        "financial": [
            {"article": "Art. 6(2)", "title": "High-Risk AI", "desc": "Creditworthiness assessment AI is high-risk."},
            {"article": "Art. 13", "title": "Transparency", "desc": "Financial AI must provide clear explanations."},
            {"article": "Art. 14", "title": "Human Oversight", "desc": "Automated financial decisions require human review."},
        ],
    }

    # Find matching violations
    for key, rules in violation_db.items():
        if key in harm_type:
            violations.extend(rules)

    # Default violations (always applicable)
    default_violations = [
        {"article": "Art. 13", "title": "Transparency & Information", "desc": "AI decision-making process must be explainable."},
        {"article": "Art. 14", "title": "Human Oversight", "desc": "Effective human oversight must be ensured."},
        {"article": "Art. 72", "title": "Right to Complaint", "desc": "Affected persons have the right to lodge complaints."},
    ]
    if not violations:
        violations = default_violations

    viol_header = ["Article", "Title", "Violation"]
    viol_data = [viol_header]
    for v in violations:
        viol_data.append([
            v["article"],
            v["title"],
            Paragraph(v["desc"][:120], ParagraphStyle("ViolDesc", fontSize=7, fontName="Helvetica", leading=9)),
        ])

    vt = Table(viol_data, colWidths=[25*mm, 35*mm, 105*mm])
    vt.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, 0), DANGER_RED),
        ("TEXTCOLOR", (0, 0), (-1, 0), white),
        ("BACKGROUND", (0, 1), (-1, -1), HexColor("#fff5f5")),
        ("GRID", (0, 0), (-1, -1), 0.5, SILVER),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    elements.append(vt)
    elements.append(Spacer(1, 6*mm))

    # ── 6. RECOMMENDED ACTIONS ──
    elements.append(Paragraph("🎯 6. RECOMMENDED ACTIONS", section_style))
    actions = report_data.get("recommended_actions", [
        "File a formal complaint with the AI system provider.",
        "Request full explanation of the AI decision under Art. 13.",
        "Invoke right to human review under Art. 14.",
        "Lodge complaint with national supervisory authority (Art. 72).",
        "Preserve all evidence for potential legal proceedings.",
    ])
    for i, action in enumerate(actions[:8]):
        elements.append(Paragraph(
            f"<b>{i+1}.</b> {action}",
            ParagraphStyle(f"Act{i}", fontSize=9, fontName="Helvetica", leftIndent=5*mm, spaceAfter=2*mm),
        ))
    elements.append(Spacer(1, 6*mm))

    # ── SEAL FOOTER ──
    elements.append(HRFlowable(width="100%", color=SILVER, thickness=1))
    elements.append(Spacer(1, 4*mm))

    seal_input = json.dumps({
        "report_id": report_id,
        "timestamp": ts,
        "victim": report_data.get("victim_name", "anonymous"),
        "ai_system": report_data.get("ai_system_name", "unknown"),
        "issuer": "yaruksai-vericore",
    }, sort_keys=True, ensure_ascii=False)
    seal_hash = hashlib.sha256(seal_input.encode()).hexdigest()

    elements.append(Paragraph("🔐 CRYPTOGRAPHIC SEAL (SHA-256)", ParagraphStyle(
        "SealTitle", fontName="Helvetica-Bold", fontSize=9,
        textColor=NAVY_MID, alignment=TA_CENTER, spaceAfter=2*mm,
    )))
    elements.append(Paragraph(seal_hash, ParagraphStyle(
        "Seal", fontName="Courier", fontSize=7,
        textColor=TEXT_DIM, alignment=TA_CENTER,
    )))
    elements.append(Spacer(1, 3*mm))
    elements.append(Paragraph(
        "This report was generated by YARUKSAI VERICORE engine. "
        "The cryptographic seal guarantees document integrity. "
        "Verification: verify.yaruksai.com",
        ParagraphStyle("Disc", fontName="Helvetica", fontSize=6,
                       textColor=TEXT_DIM, alignment=TA_CENTER),
    ))
    elements.append(Spacer(1, 2*mm))
    elements.append(Paragraph(
        f"© {datetime.now().year} YARUKSAI — LIGHT FOR AI · PROOF FOR HUMANS",
        ParagraphStyle("Footer", fontName="Helvetica-Bold", fontSize=7,
                       textColor=ROYAL_BLUE, alignment=TA_CENTER),
    ))

    doc.build(elements)
    return buf.getvalue()

