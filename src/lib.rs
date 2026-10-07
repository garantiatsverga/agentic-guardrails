//! PyO3-фасад: реэкспорт всех Rust-модулей в один Python-модуль `_core`.

use pyo3::prelude::*;

use cost_cb::{Budget as RustBudget, CostBreaker as RustCostBreaker};
use failure_cb::{FailureBreaker as RustFailureBreaker};
use audit::{AuditChain as RustAuditChain, AuditEntry as RustAuditEntry};
use serde_json::Value;

#[pyclass]
#[derive(Clone)]
struct Budget {
    inner: RustBudget,
}

#[pymethods]
impl Budget {
    #[new]
    #[pyo3(signature = (limit, soft_ratio = 0.7))]
    fn new(limit: u64, soft_ratio: f64) -> Self {
        Self { inner: RustBudget::new(limit, soft_ratio) }
    }

    #[getter]
    fn limit(&self) -> u64 {
        self.inner.limit
    }

    #[getter]
    fn soft_ratio(&self) -> f64 {
        self.inner.soft_ratio
    }
}

#[pyclass]
struct CostBreaker {
    inner: RustCostBreaker,
}

#[pymethods]
impl CostBreaker {
    #[new]
    #[pyo3(signature = (per_task, per_session, per_horizon, horizon_seconds = 0.0))]
    fn new(
        per_task: Budget,
        per_session: Budget,
        per_horizon: Budget,
        horizon_seconds: f64,
    ) -> Self {
        Self {
            inner: RustCostBreaker::new(
                per_task.inner,
                per_session.inner,
                per_horizon.inner,
                horizon_seconds,
            ),
        }
    }

    /// Возвращает "ok" | "soft" | "hard".
    fn charge(&self, tokens: u64) -> &'static str {
        self.inner.charge(tokens).as_str()
    }

    /// Списывает батч токенов, возвращает итоговое состояние.
    fn charge_batch(&self, tokens_list: Vec<u64>) -> &'static str {
        self.inner.charge_batch(&tokens_list).as_str()
    }

    fn reset_task(&self) {
        self.inner.reset_task();
    }

    fn reset_session(&self) {
        self.inner.reset_session();
    }

    #[getter]
    fn task_used(&self) -> u64 {
        self.inner.task_used()
    }

    #[getter]
    fn session_used(&self) -> u64 {
        self.inner.session_used()
    }

    #[getter]
    fn horizon_used(&self) -> u64 {
        self.inner.horizon_used()
    }
}

#[pyclass]
struct FailureBreaker {
    inner: RustFailureBreaker,
}

#[pymethods]
impl FailureBreaker {
    #[new]
    #[pyo3(signature = (failure_threshold = 5, recovery_timeout = 30.0, half_open_probes = 1))]
    fn new(
        failure_threshold: u32,
        recovery_timeout: f64,
        half_open_probes: u32,
    ) -> Self {
        Self {
            inner: RustFailureBreaker::new(
                failure_threshold,
                recovery_timeout,
                half_open_probes,
            ),
        }
    }

    /// Возвращает "closed" | "open" | "half_open".
    fn state(&self) -> &'static str {
        self.inner.state().as_str()
    }

    fn allow(&self) -> bool {
        self.inner.allow()
    }

    fn on_success(&self) {
        self.inner.on_success();
    }

    fn on_failure(&self) {
        self.inner.on_failure();
    }

    #[getter]
    fn failures(&self) -> u32 {
        self.inner.failures()
    }

    #[getter]
    fn probes_count(&self) -> u32 {
        self.inner.probes_count()
    }
}

#[pyclass]
#[derive(Clone)]
struct AuditEntry {
    inner: RustAuditEntry,
}

#[pymethods]
impl AuditEntry {
    #[getter]
    fn seq(&self) -> u64 {
        self.inner.seq
    }

    #[getter]
    fn prev_hash(&self) -> String {
        self.inner.prev_hash.clone()
    }

    /// Unix millis. Python-обёртка конвертирует в ISO.
    #[getter]
    fn timestamp(&self) -> u64 {
        self.inner.timestamp
    }

    #[getter]
    fn event(&self) -> String {
        self.inner.event.clone()
    }

    /// payload как НОВЫЙ Python-объект (через JSON-строку).
    /// Мутация результата не влияет на Rust-структуру.
    fn payload<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let json_str = serde_json::to_string(&self.inner.payload)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        let json_mod = py.import_bound("json")?;
        json_mod.call_method1("loads", (json_str,))
    }

    #[getter]
    fn entry_hash(&self) -> String {
        self.inner.entry_hash.clone()
    }
}

#[pyclass]
struct AuditChain {
    inner: RustAuditChain,
}

#[pymethods]
impl AuditChain {
    #[new]
    fn new() -> Self {
        Self { inner: RustAuditChain::new() }
    }

    fn append(
        &mut self,
        event: String,
        payload: Bound<'_, PyAny>,
    ) -> PyResult<AuditEntry> {
        let value: Value = pythonize::depythonize(&payload)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        let entry = self.inner.append(event, value);
        Ok(AuditEntry { inner: entry })
    }

    /// Добавляет пачку записей за один вызов.
    ///
    /// events: список кортежей (event, payload).
    /// Возвращает список AuditEntry в порядке входа.
    fn append_batch(
        &mut self,
        events: Vec<(String, Bound<'_, PyAny>)>,
    ) -> PyResult<Vec<AuditEntry>> {
        // Конвертируем все payload'ы заранее — если один невалиден,
        // падаем ДО изменения состояния цепочки.
        let mut converted: Vec<(String, Value)> = Vec::with_capacity(events.len());
        for (event, payload) in events {
            let value: Value = pythonize::depythonize(&payload)
                .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
            converted.push((event, value));
        }
        Ok(self.inner
            .append_batch(converted)
            .into_iter()
            .map(|e| AuditEntry { inner: e })
            .collect())
    }

    fn verify(&self) -> bool {
        self.inner.verify()
    }

    fn __len__(&self) -> usize {
        self.inner.len()
    }

    fn entries(&self) -> Vec<AuditEntry> {
        self.inner
            .entries()
            .iter()
            .map(|e| AuditEntry { inner: e.clone() })
            .collect()
    }
}

#[pymodule]
fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Budget>()?;
    m.add_class::<CostBreaker>()?;
    m.add_class::<FailureBreaker>()?;
    m.add_class::<AuditEntry>()?;
    m.add_class::<AuditChain>()?;
    Ok(())
}
