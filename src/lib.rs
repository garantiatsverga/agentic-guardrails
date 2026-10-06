//! PyO3-фасад: реэкспорт всех Rust-модулей в один Python-модуль `_core`.

use pyo3::prelude::*;

use cost_cb::{Budget as RustBudget, CostBreaker as RustCostBreaker};

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

#[pymodule]
fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Budget>()?;
    m.add_class::<CostBreaker>()?;
    Ok(())
}
