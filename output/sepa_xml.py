# Genereert SEPA XML (pain.001.001.09) uit de betalingsopdrachten met behulp van lxml.

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from lxml import etree

NS = "urn:iso:std:iso:20022:tech:xsd:pain.001.001.09"

logger = logging.getLogger(__name__)


def _strip_iban(iban: str) -> str:
    s = re.sub(r"\s+", "", str(iban or ""))
    return s.upper()


def _money_decimal(amount: float) -> Decimal:
    d = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return d


def _money_str(d: Decimal) -> str:
    return f"{d:.2f}"


def generate_xml(
    payments: list[dict],
    debtor: dict,
    execution_date: str,
    output_dir: str = "exports",
) -> str:
    """Genereert een pain.001.001.09 XML-bestand voor ING Mijn Zakelijk.

    Returns:
        Absoluut pad van het geschreven XML-bestand.
    """
    if not payments:
        logger.error("SEPA XML generatie: 0 transacties (payments leeg) — dit mag niet")
        raise ValueError("payments mag niet leeg zijn voor SEPA batch export")

    now = datetime.now()
    timestamp = now.strftime("%Y%m%d%H%M%S")
    ymd = now.strftime("%Y%m%d")
    hms = now.strftime("%H%M%S")
    msg_id = f"MSG-{timestamp}"
    pmt_inf_id = f"PMT-{timestamp}"
    cre_dt_tm = now.strftime("%Y-%m-%dT%H:%M:%S")

    dec_rows: list[Decimal] = [_money_decimal(float(p["amount"])) for p in payments]
    n_tx = len(dec_rows)
    ctrl_sum = sum(dec_rows, start=Decimal("0")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    ctrl_str = _money_str(ctrl_sum)
    logger.info("SEPA XML: %d transacties in PmtInf", n_tx)

    root = etree.Element("Document", nsmap={None: NS})
    cci = etree.SubElement(root, "CstmrCdtTrfInitn")

    grp = etree.SubElement(cci, "GrpHdr")
    etree.SubElement(grp, "MsgId").text = msg_id
    etree.SubElement(grp, "CreDtTm").text = cre_dt_tm
    etree.SubElement(grp, "NbOfTxs").text = str(n_tx)
    etree.SubElement(grp, "CtrlSum").text = ctrl_str
    initg = etree.SubElement(grp, "InitgPty")
    etree.SubElement(initg, "Nm").text = str(debtor["name"])

    pmt_inf = etree.SubElement(cci, "PmtInf")
    etree.SubElement(pmt_inf, "PmtInfId").text = pmt_inf_id
    etree.SubElement(pmt_inf, "PmtMtd").text = "TRF"
    etree.SubElement(pmt_inf, "BtchBookg").text = "true"
    etree.SubElement(pmt_inf, "NbOfTxs").text = str(n_tx)
    etree.SubElement(pmt_inf, "CtrlSum").text = ctrl_str

    pti = etree.SubElement(pmt_inf, "PmtTpInf")
    sl = etree.SubElement(pti, "SvcLvl")
    etree.SubElement(sl, "Cd").text = "SEPA"

    reqd_dt = etree.SubElement(pmt_inf, "ReqdExctnDt")
    etree.SubElement(reqd_dt, "Dt").text = execution_date

    dbtr = etree.SubElement(pmt_inf, "Dbtr")
    etree.SubElement(dbtr, "Nm").text = str(debtor["name"])

    dbtr_acct = etree.SubElement(pmt_inf, "DbtrAcct")
    dbtr_id = etree.SubElement(dbtr_acct, "Id")
    etree.SubElement(dbtr_id, "IBAN").text = _strip_iban(str(debtor["iban"]))

    dbtr_agt = etree.SubElement(pmt_inf, "DbtrAgt")
    fi = etree.SubElement(dbtr_agt, "FinInstnId")
    etree.SubElement(fi, "BICFI").text = str(debtor["bic"]).strip().upper()

    etree.SubElement(pmt_inf, "ChrgBr").text = "SLEV"

    for i, p in enumerate(payments):
        amt_dec = dec_rows[i]
        amt_txt = _money_str(amt_dec)
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
    return abspath
