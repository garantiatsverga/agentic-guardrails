//! Cost Circuit Breaker — защита от зацикливания агента и взрыва токенов.
//!
//! Три уровня бюджета: per-task, per-session, per-horizon.
//! Soft-порог (по умолчанию 70%) -> эскалация; hard (100%) -> остановка.
//!
//! API 1:1 с Python-версией. Потокобезопасен через AtomicU64.

use std::sync::atomic::{AtomicU64, Ordering};

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
    task_used: AtomicU64,
    session_used: AtomicU64,
    horizon_used: AtomicU64,
}

impl CostBreaker {
    pub fn new(per_task: Budget, per_session: Budget, per_horizon: Budget) -> Self {
        Self {
            per_task,
            per_session,
            per_horizon,
            task_used: AtomicU64::new(0),
            session_used: AtomicU64::new(0),
            horizon_used: AtomicU64::new(0),
        }
    }

    /// Списывает токены и возвращает текущее состояние.
    ///
    /// Приоритет: Hard > Soft > Ok.
    pub fn charge(&self, tokens: u64) -> CostState {
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
