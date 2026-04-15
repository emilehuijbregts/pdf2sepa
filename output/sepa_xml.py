# Genereert SEPA XML (pain.001.001.09) uit de betalingsopdrachten met behulp van lxml.

from __future__ import annotations

import logging
import os
import re
import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

from lxml import etree

from logic.payment_decisions import canonicalize_payments, decision_is_exportable
from logic.payment_amounts import amount_to_decimal, format_eur_xml, sum_decimals

NS = "urn:iso:std:iso:20022:tech:xsd:pain.001.001.09"

logger = logging.getLogger(__name__)

BatchStatus = Literal["valid", "warning", "blocked"]


@dataclass
class BatchValidationResult:
    status: BatchStatus
    flags: list[str] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)


def _trace_parsed_amount_status(p: dict) -> str:
    dt = p.get("decision_trace")
    if not isinstance(dt, dict):
        return ""
    snap = dt.get("reconciliation_snapshot")
    if not isinstance(snap, dict):
        return ""
    par = snap.get("parsed_amount_result")
    if not isinstance(par, dict):
        return ""
    return str(par.get("status") or "").strip().lower()


def _amount_result_status_top(p: dict) -> str:
    ar = p.get("amount_result")
    if not isinstance(ar, dict):
        return ""
    return str(ar.get("status") or ar.get("amount_status") or "").strip().lower()


def _payment_row_hash(payment: dict) -> str:
    return hashlib.sha256(
        repr(
            (
                payment.get("invoice_number"),
                payment.get("supplier_name"),
                payment.get("iban"),
                payment.get("amount"),
                payment.get("execution_date"),
                payment.get("decision"),
            )
        ).encode("utf-8")
    ).hexdigest()


def exportable_payments_from_decisions(payments: list[dict]) -> list[dict]:
    """Strict filter: only included decisions are exportable."""
    filtered = [p for p in payments if decision_is_exportable(p.get("decision"))]
    return canonicalize_payments(filtered)


def validate_export_batch(payments: list[dict]) -> BatchValidationResult:
    """Lichte safety-check op een exportbatch: geen nieuwe bedraglogica, alleen detectie."""
    ambiguous_count = 0
    failed_count = 0
    invalid_amount_count = 0
    for p in payments:
        top = str(p.get("status") or "").strip().lower()
        ar_st = _amount_result_status_top(p)
        trace_st = _trace_parsed_amount_status(p)
        # Top-level amount_result wint: UI kan bedrag kiezen en confirmed zetten terwijl
        # decision_trace nog een oude snapshot bevatte.
        if ar_st in ("confirmed", "tentative"):
            continue
        if ar_st == "ambiguous" or top == "ambiguous" or trace_st == "ambiguous":
            ambiguous_count += 1
        if ar_st == "failed" or top == "failed" or trace_st == "failed":
            failed_count += 1

    for p in payments:
        try:
            amt = amount_to_decimal(p.get("amount"))
        except (TypeError, ValueError):
            invalid_amount_count += 1
            continue
        if amt <= Decimal("0"):
            invalid_amount_count += 1

    flags: list[str] = []
    if ambiguous_count:
        flags.append("has_ambiguous_payment")
    if failed_count:
        flags.append("has_failed_payment")
    if invalid_amount_count:
        flags.append("has_invalid_amount")

    dup_keys: list[tuple[str, Decimal]] = []
    for p in payments:
        ib = _strip_iban(str(p.get("iban") or ""))
        if not ib:
            continue
        try:
            amt = amount_to_decimal(p.get("amount"))
        except (TypeError, ValueError):
            continue
        dup_keys.append((ib, amt))

    counts: dict[tuple[str, Decimal], int] = defaultdict(int)
    for k in dup_keys:
        counts[k] += 1
    duplicate_risk = any(c >= 2 for c in counts.values())
    if duplicate_risk:
        flags.append("duplicate_risk_detected")

    summary = {
        "payment_count": len(payments),
        "ambiguous_count": ambiguous_count,
        "failed_count": failed_count,
        "invalid_amount_count": invalid_amount_count,
    }

    if ambiguous_count or failed_count or invalid_amount_count:
        return BatchValidationResult(status="blocked", flags=flags, summary=summary)
    if duplicate_risk:
        return BatchValidationResult(status="warning", flags=flags, summary=summary)
    return BatchValidationResult(status="valid", flags=flags, summary=summary)


def format_batch_export_blocked_message(result: BatchValidationResult) -> str:
    parts: list[str] = []
    ac = int(result.summary.get("ambiguous_count", 0))
    fc = int(result.summary.get("failed_count", 0))
    ia = int(result.summary.get("invalid_amount_count", 0))
    if ac:
        parts.append(f"{ac} betaling(en) met ambigu of onzeker bedrag")
    if fc:
        parts.append(f"{fc} betaling(en) met mislukte bedragdetectie")
    if ia:
        parts.append(f"{ia} betaling(en) met ongeldig of niet-positief bedrag")
    if not parts:
        parts.append("onbekende blokkade")
    return "Export geblokkeerd: " + "; ".join(parts) + "."


def _strip_iban(iban: str) -> str:
    s = re.sub(r"\s+", "", str(iban or ""))
    return s.upper()


def _require_execution_date(p: dict, index: int) -> str:
    raw = p.get("execution_date")
    if raw is None or not str(raw).strip():
        raise ValueError(f"payment[{index}] mist execution_date (verplicht voor SEPA-export)")
    s = str(raw).strip()
    if len(s) != 10 or s[4] != "-" or s[7] != "-":
        raise ValueError(f"payment[{index}] execution_date ongeldig (verwacht YYYY-MM-DD): {raw!r}")
    return s


def generate_xml(
    payments: list[dict],
    debtor: dict,
    output_dir: str = "exports",
    *,
    run_id: str | None = None,
) -> str:
    """Genereert een pain.001.001.09 XML-bestand voor ING Mijn Zakelijk.

    Elke payment moet ``execution_date`` (ISO ``YYYY-MM-DD``) bevatten.
    Betalingen met dezelfde uitvoeringsdatum worden in één ``PmtInf`` gegroepeerd.

    Returns:
        Absoluut pad van het geschreven XML-bestand.
    """
    exportable = exportable_payments_from_decisions(payments)
    if not exportable:
        logger.error("SEPA XML generatie: 0 transacties (payments leeg) — dit mag niet")
        raise ValueError("payments mag niet leeg zijn voor SEPA batch export")

    batch_result = validate_export_batch(exportable)
    if batch_result.status == "blocked":
        raise ValueError(format_batch_export_blocked_message(batch_result))
    if batch_result.status == "warning":
        logger.warning(
            "SEPA export batch status=warning flags=%s summary=%s",
            batch_result.flags,
            batch_result.summary,
        )

    dec_by_payment: list[Decimal] = []
    for i, p in enumerate(exportable):
        _require_execution_date(p, i)
        dec_by_payment.append(amount_to_decimal(p.get("amount")))

    groups: dict[str, list[tuple[int, dict]]] = defaultdict(list)
    for i, p in enumerate(exportable):
        ex = _require_execution_date(p, i)
        groups[ex].append((i, p))

    sorted_dates = sorted(groups.keys())
    non_empty_dates = [d for d in sorted_dates if groups[d]]
    if not non_empty_dates:
        raise ValueError("geen geldige batch om te exporteren")

    if run_id:
        digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:14]
        timestamp = f"RID{digest}"
        ymd = digest[:8]
        hms = digest[8:14]
        msg_id = f"MSG-{timestamp}"
        cre_dt_tm = "2000-01-01T00:00:00"
    else:
        now = datetime.now()
        timestamp = now.strftime("%Y%m%d%H%M%S")
        ymd = now.strftime("%Y%m%d")
        hms = now.strftime("%H%M%S")
        msg_id = f"MSG-{timestamp}"
        cre_dt_tm = now.strftime("%Y-%m-%dT%H:%M:%S")

    n_tx_total = len(dec_by_payment)
    ctrl_sum_total = sum_decimals(dec_by_payment)
    ctrl_str_total = format_eur_xml(ctrl_sum_total)

    for batch_idx, ex_dt in enumerate(non_empty_dates):
        batch_pairs = groups[ex_dt]
        batch_decs = [dec_by_payment[i] for i, _ in batch_pairs]
        n_b = len(batch_decs)
        sum_b = sum_decimals(batch_decs)
        logger.info(
            "SEPA batch %d: %s → %d tx → EUR %s",
            batch_idx + 1,
            ex_dt,
            n_b,
            format_eur_xml(sum_b),
        )

    logger.info(
        "SEPA XML: %d batch(es), %d transacties totaal, CtrlSum %s",
        len(non_empty_dates),
        n_tx_total,
        ctrl_str_total,
    )

    root = etree.Element("Document", nsmap={None: NS})
    cci = etree.SubElement(root, "CstmrCdtTrfInitn")

    grp = etree.SubElement(cci, "GrpHdr")
    etree.SubElement(grp, "MsgId").text = msg_id
    etree.SubElement(grp, "CreDtTm").text = cre_dt_tm
    etree.SubElement(grp, "NbOfTxs").text = str(n_tx_total)
    etree.SubElement(grp, "CtrlSum").text = ctrl_str_total
    initg = etree.SubElement(grp, "InitgPty")
    etree.SubElement(initg, "Nm").text = str(debtor["name"])

    for batch_idx, ex_dt in enumerate(non_empty_dates):
        batch_pairs = groups[ex_dt]
        if not batch_pairs:
            continue
        batch_decs = [dec_by_payment[i] for i, _ in batch_pairs]
        n_b = len(batch_decs)
        sum_b = sum_decimals(batch_decs)
        ctrl_b = format_eur_xml(sum_b)

        pmt_inf_id = f"PMT-{timestamp}-{batch_idx:03d}"
        pmt_inf = etree.SubElement(cci, "PmtInf")
        etree.SubElement(pmt_inf, "PmtInfId").text = pmt_inf_id
        etree.SubElement(pmt_inf, "PmtMtd").text = "TRF"
        etree.SubElement(pmt_inf, "BtchBookg").text = "true"
        etree.SubElement(pmt_inf, "NbOfTxs").text = str(n_b)
        etree.SubElement(pmt_inf, "CtrlSum").text = ctrl_b

        pti = etree.SubElement(pmt_inf, "PmtTpInf")
        sl = etree.SubElement(pti, "SvcLvl")
        etree.SubElement(sl, "Cd").text = "SEPA"

        reqd_dt = etree.SubElement(pmt_inf, "ReqdExctnDt")
        etree.SubElement(reqd_dt, "Dt").text = ex_dt

        dbtr = etree.SubElement(pmt_inf, "Dbtr")
        etree.SubElement(dbtr, "Nm").text = str(debtor["name"])

        dbtr_acct = etree.SubElement(pmt_inf, "DbtrAcct")
        dbtr_id = etree.SubElement(dbtr_acct, "Id")
        etree.SubElement(dbtr_id, "IBAN").text = _strip_iban(str(debtor["iban"]))

        dbtr_agt = etree.SubElement(pmt_inf, "DbtrAgt")
        fi = etree.SubElement(dbtr_agt, "FinInstnId")
        etree.SubElement(fi, "BICFI").text = str(debtor["bic"]).strip().upper()

        etree.SubElement(pmt_inf, "ChrgBr").text = "SLEV"

        for row_i, p in batch_pairs:
            amt_dec = dec_by_payment[row_i]
            amt_txt = format_eur_xml(amt_dec)
            invoice_number = str(p.get("invoice_number") or "").strip()
            e2e = (invoice_number or "NOTPROVIDED")[:35]
            instr_id = e2e

            desc = str(p.get("description") or "")
            ustrd = desc.strip()[:140]

            tx = etree.SubElement(pmt_inf, "CdtTrfTxInf")
            pmt_id = etree.SubElement(tx, "PmtId")
            etree.SubElement(pmt_id, "InstrId").text = instr_id
            etree.SubElement(pmt_id, "EndToEndId").text = e2e

            amt_el = etree.SubElement(tx, "Amt")
            etree.SubElement(amt_el, "InstdAmt", Ccy="EUR").text = amt_txt

            bic = (
                str(p.get("bic") or p.get("cdtr_bic") or p.get("creditor_bic") or "").strip()
            )
            if bic:
                cdtr_agt = etree.SubElement(tx, "CdtrAgt")
                cdtr_fi = etree.SubElement(cdtr_agt, "FinInstnId")
                etree.SubElement(cdtr_fi, "BICFI").text = bic.upper()

            cdtr = etree.SubElement(tx, "Cdtr")
            supplier_name = str(p.get("supplier_name") or "").strip() or "UNKNOWN"
            etree.SubElement(cdtr, "Nm").text = supplier_name

            cdtr_acct = etree.SubElement(tx, "CdtrAcct")
            cdtr_id = etree.SubElement(cdtr_acct, "Id")
            etree.SubElement(cdtr_id, "IBAN").text = _strip_iban(str(p.get("iban") or ""))

            rmt = etree.SubElement(tx, "RmtInf")
            etree.SubElement(rmt, "Ustrd").text = ustrd

    os.makedirs(output_dir, exist_ok=True)
    filename = f"SEPA_{ymd}_{hms}.xml"
    path = os.path.join(output_dir, filename)
    abspath = os.path.abspath(path)

    tree = etree.ElementTree(root)
    tree.write(
        abspath,
        encoding="UTF-8",
        xml_declaration=True,
        pretty_print=True,
    )
    logger.info(
        "SEPA XML committed rows=%d run_id=%s digest=%s",
        len(exportable),
        run_id or "",
        hashlib.sha256("".join(_payment_row_hash(p) for p in exportable).encode("utf-8")).hexdigest(),
    )
    return abspath
