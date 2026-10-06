//! Cost Circuit Breaker — защита от зацикливания агента и взрыва токенов.
//!
//! Три уровня бюджета: per-task, per-session, per-horizon.
//! Soft-порог (по умолчанию 70%) -> эскалация; hard (100%) -> остановка.
//!
//! API 1:1 с Python-версией. Потокобезопасен через AtomicU64.

use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Instant;
use std::sync::Mutex;

/// Состояние бюджета.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CostState {
    Ok,
    Soft,
    Hard,
}

impl CostState {
    pub fn as_str(&self) -> &'static str {
        match self {
            CostState::Ok => "ok",
            CostState::Soft => "soft",
            CostState::Hard => "hard",
        }
    }
}

/// Бюджет с лимитом и soft-порогом.
#[derive(Debug, Clone, Copy)]
pub struct Budget {
    pub limit: u64,
    pub soft_ratio: f64,
}

impl Budget {
    pub fn new(limit: u64, soft_ratio: f64) -> Self {
        Self { limit, soft_ratio }
    }

    pub fn state(&self, used: u64) -> CostState {
        if used >= self.limit {
            CostState::Hard
        } else if (used as f64) >= (self.limit as f64) * self.soft_ratio {
            CostState::Soft
        } else {
            CostState::Ok
        }
    }
}

/// Cost Circuit Breaker — потокобезопасный.
pub struct CostBreaker {
    per_task: Budget,
    per_session: Budget,
    per_horizon: Budget,
    horizon_seconds: f64,
    task_used: AtomicU64,
    session_used: AtomicU64,
    horizon_used: AtomicU64,
    horizon_start: Mutex<Instant>,
}

impl CostBreaker {
    pub fn new(
        per_task: Budget,
        per_session: Budget,
        per_horizon: Budget,
        horizon_seconds: f64,
    ) -> Self {
        Self {
            per_task,
            per_session,
            per_horizon,
            horizon_seconds,
            task_used: AtomicU64::new(0),
            session_used: AtomicU64::new(0),
            horizon_used: AtomicU64::new(0),
            horizon_start: Mutex::new(Instant::now()),
        }
    }

    fn maybe_reset_horizon(&self) {
        if self.horizon_seconds <= 0.0 {
            return;
        }
        let mut start = self.horizon_start.lock().unwrap();
        if start.elapsed().as_secs_f64() >= self.horizon_seconds {
            self.horizon_used.store(0, Ordering::Relaxed);
            *start = Instant::now();
        }
    }

    pub fn charge(&self, tokens: u64) -> CostState {
        self.maybe_reset_horizon();

        let task = self.task_used.fetch_add(tokens, Ordering::Relaxed) + tokens;
        let session = self.session_used.fetch_add(tokens, Ordering::Relaxed) + tokens;
        let horizon = self.horizon_used.fetch_add(tokens, Ordering::Relaxed) + tokens;

        let states = [
            self.per_task.state(task),
            self.per_session.state(session),
            self.per_horizon.state(horizon),
        ];

        if states.iter().any(|s| *s == CostState::Hard) {
            CostState::Hard
        } else if states.iter().any(|s| *s == CostState::Soft) {
            CostState::Soft
        } else {
            CostState::Ok
        }
    }

    /// Списывает токены батчем, возвращает итоговое состояние.
    ///
    /// Эквивалент N вызовов charge(t) с тем же результатом в конце,
    /// но с одним переходом Python<->Rust вместо N.
    pub fn charge_batch(&self, tokens_list: &[u64]) -> CostState {
        let mut last = CostState::Ok;
        for &tokens in tokens_list {
            last = self.charge(tokens);
        }
        last
    }

    pub fn reset_task(&self) {
        self.task_used.store(0, Ordering::Relaxed);
    }

    pub fn reset_session(&self) {
        self.task_used.store(0, Ordering::Relaxed);
        self.session_used.store(0, Ordering::Relaxed);
    }

    pub fn task_used(&self) -> u64 {
        self.task_used.load(Ordering::Relaxed)
    }

    pub fn session_used(&self) -> u64 {
        self.session_used.load(Ordering::Relaxed)
    }

    pub fn horizon_used(&self) -> u64 {
        self.horizon_used.load(Ordering::Relaxed)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn breaker(task: u64, session: u64, horizon: u64) -> CostBreaker {
        CostBreaker::new(
            Budget::new(task, 0.7),
            Budget::new(session, 0.7),
            Budget::new(horizon, 0.7),
            0.0,
        )
    }

    #[test]
    fn ok_under_soft_threshold() {
        let cb = breaker(1000, 10_000, 100_000);
        assert_eq!(cb.charge(100), CostState::Ok);
    }

    #[test]
    fn soft_at_70_percent() {
        let cb = breaker(1000, 10_000, 100_000);
        assert_eq!(cb.charge(700), CostState::Soft);
    }

    #[test]
    fn hard_at_100_percent() {
        let cb = breaker(1000, 10_000, 100_000);
        assert_eq!(cb.charge(1000), CostState::Hard);
    }

    #[test]
    fn hard_wins_over_soft() {
        let cb = breaker(1000, 10_000, 100_000);
        assert_eq!(cb.charge(1000), CostState::Hard);
    }

    #[test]
    fn reset_task_keeps_session() {
        let cb = breaker(1000, 10_000, 100_000);
        cb.charge(900);
        cb.reset_task();
        assert_eq!(cb.charge(100), CostState::Ok);
    }

    #[test]
    fn reset_session_clears_both() {
        let cb = breaker(1000, 10_000, 100_000);
        cb.charge(900);
        cb.reset_session();
        assert_eq!(cb.task_used(), 0);
        assert_eq!(cb.session_used(), 0);
    }

    #[test]
    fn batch_equals_sequential() {
        let cb1 = breaker(1000, 10_000, 100_000);
        let cb2 = breaker(1000, 10_000, 100_000);

        let tokens = vec![100u64, 200, 300, 400];
        for &t in &tokens {
            cb1.charge(t);
        }
        let _ = cb2.charge_batch(&tokens);

        assert_eq!(cb1.task_used(), cb2.task_used());
        assert_eq!(cb1.session_used(), cb2.session_used());
        assert_eq!(cb1.horizon_used(), cb2.horizon_used());
    }

    #[test]
    fn batch_empty_returns_ok() {
        let cb = breaker(1000, 10_000, 100_000);
        assert_eq!(cb.charge_batch(&[]), CostState::Ok);
    }

    #[test]
    fn batch_hard_state_persists() {
        let cb = breaker(1000, 10_000, 100_000);
        let state = cb.charge_batch(&[500, 500, 500]);
        assert_eq!(state, CostState::Hard);
    }

    #[test]
    fn horizon_resets_after_timeout() {
        let cb = CostBreaker::new(
            Budget::new(10_u64.pow(9), 0.7),
            Budget::new(10_u64.pow(9), 0.7),
            Budget::new(1000, 0.7),
            0.01,
        );
        cb.charge(900);
        std::thread::sleep(std::time::Duration::from_millis(20));
        assert_eq!(cb.charge(200), CostState::Ok);
    }

    #[test]
    fn atomic_charge_no_lost_updates() {
        use std::sync::Arc;
        use std::thread;

        let cb = Arc::new(breaker(1_000_000, 1_000_000, 1_000_000));
        let mut handles = vec![];

        for _ in 0..10 {
            let cb = Arc::clone(&cb);
            handles.push(thread::spawn(move || {
                for _ in 0..1000 {
                    cb.charge(1);
                }
            }));
        }

        for h in handles {
            h.join().unwrap();
        }

        assert_eq!(cb.task_used(), 10_000);
        assert_eq!(cb.session_used(), 10_000);
    }
}
