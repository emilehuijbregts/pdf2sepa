"""QThread worker for non-blocking invoice batch load."""

from __future__ import annotations

import logging
from dataclasses import replace

from PySide6.QtCore import QObject, Qt, Signal, Slot

from logic import batch_load_pipeline

logger = logging.getLogger(__name__)

def _exception_to_error_code(exc: BaseException) -> str:
    msg = str(exc)
    if "warm_invoices required" in msg:
        return "error.batch.warm_invoices_required"
    if "run_preprocess must complete" in msg:
        return "error.batch.resolve_phase_incomplete"
    return "error.batch.generic"


class InvoiceBatchLoadWorker(QObject):
    started = Signal()
    progress = Signal(int, int)
    current_file = Signal(str)
    current_stage = Signal(str)
    preprocess_finished = Signal(object)
    resolve_requested = Signal(object, object)
    finished = Signal(object)
    error = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._params = None
        self._preprocess_params = None
        self._cancel_requested = False
        self.resolve_requested.connect(
            self.run_resolve_iban_and_engine,
            Qt.ConnectionType.QueuedConnection,
        )

    def set_preprocess_params(self, params) -> None:
        self._preprocess_params = params

    @Slot()
    def start_preprocess(self) -> None:
        params = self._preprocess_params
        if params is None:
            self.error.emit("error.batch.params_missing")
            return
        self.run_preprocess(params)

    def request_cancel(self) -> None:
        self._cancel_requested = True

    def _emit_progress(self, done: int, total: int, filename: str, stage: str) -> None:
        self.progress.emit(done, total)
        self.current_file.emit(filename)
        self.current_stage.emit(stage)

    def _cancel_check(self) -> bool:
        return self._cancel_requested

    @Slot(object)
    def run_preprocess(self, params) -> None:
        try:
            self._cancel_requested = False
            self._params = params
            self.started.emit()
            active_params = replace(params, cancel_check=self._cancel_check)
            result = batch_load_pipeline.run_preprocess(active_params, self._emit_progress)
            self.preprocess_finished.emit(result)
        except Exception as exc:
            logger.exception("Batch preprocess failed")
            self.error.emit(_exception_to_error_code(exc))

    @Slot(object, object)
    def run_resolve_iban_and_engine(self, checkpoint, raw_answers) -> None:
        try:
            if self._params is None:
                raise RuntimeError("run_preprocess must complete before resolve phase")
            result = batch_load_pipeline.run_resolve_iban_and_engine(
                self._params,
                checkpoint,
                raw_answers,
                self._emit_progress,
            )
            self.finished.emit(result)
        except Exception as exc:
            logger.exception("Batch resolve/engine failed")
            self.error.emit(_exception_to_error_code(exc))
