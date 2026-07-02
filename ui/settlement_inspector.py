"""Settlement inspector text (read-only engine display)."""

from __future__ import annotations

from typing import Any

from ui.settlement_view import SettlementGroupVM, settlement_group_vm_from_engine


def settlement_inspector_lines(group: dict[str, Any]) -> list[str]:
    vm = settlement_group_vm_from_engine(group)
    lines = [
        "",
        "settlement:",
        f"  group_id={vm.group_id}",
        f"  status={vm.settlement_status}",
        f"  exportable={vm.exportable}",
        f"  final_amount_due={vm.final_amount_due}",
        f"  description={vm.description}",
    ]
    if vm.invoices:
        lines.append("  facturen:")
        for inv in vm.invoices:
            lines.append(f"    {inv.invoice_number}  {inv.gross_amount}")
    if vm.credits:
        lines.append("  credits:")
        for cr in vm.credits:
            lines.append(f"    {cr.invoice_number}  {cr.amount_applied or cr.gross_amount}")
    if vm.invoices_total or vm.credits_total:
        lines.append(f"  subtotaal facturen={vm.invoices_total}")
        lines.append(f"  subtotaal credits={vm.credits_total}")
    trace = group.get("decision_trace") if isinstance(group.get("decision_trace"), dict) else {}
    history = trace.get("override_history") if isinstance(trace, dict) else None
    if isinstance(history, list) and history:
        lines.append("")
        lines.append("override_history:")
        for entry in history:
            if isinstance(entry, dict):
                ev = str(entry.get("event") or "")
                credit = str(entry.get("credit_document_id") or entry.get("credit") or "")
                invs = entry.get("invoices") or []
                at = str(entry.get("at") or "")
                detail = f"  {ev}"
                if credit:
                    detail += f" credit={credit}"
                if invs:
                    detail += f" invoices={invs}"
                if at:
                    detail += f" at={at}"
                lines.append(detail)
    return lines


def inspector_text_with_settlement(
    base_lines: list[str],
    group: dict[str, Any] | None,
) -> str:
    lines = list(base_lines)
    if group:
        lines.extend(settlement_inspector_lines(group))
    return "\n".join(lines)
