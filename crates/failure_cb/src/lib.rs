//! Failure Circuit Breaker — защита от каскадных сбоев внешних вызовов.

use std::sync::atomic::{AtomicU8, AtomicU32, Ordering};
use std::sync::Mutex;
use std::time::Instant;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CBState {
    Closed,
    Open,
    HalfOpen,
}

impl CBState {
    pub fn as_str(&self) -> &'static str {
        match self {
            CBState::Closed => "closed",
            CBState::Open => "open",
            CBState::HalfOpen => "half_open",
        }
    }

    fn from_u8(v: u8) -> Self {
        match v {
            0 => CBState::Closed,
            1 => CBState::Open,
            2 => CBState::HalfOpen,
            _ => CBState::Closed,
        }
    }

    fn as_u8(&self) -> u8 {
        match self {
            CBState::Closed => 0,
            CBState::Open => 1,
            CBState::HalfOpen => 2,
        }
    }
}

pub struct FailureBreaker {
    failure_threshold: u32,
    recovery_timeout: f64,
    half_open_probes: u32,
    state: AtomicU8,
    failures: AtomicU32,
    probes: AtomicU32,
    opened_at: Mutex<Instant>,
}

impl FailureBreaker {
    pub fn new(failure_threshold: u32, recovery_timeout: f64, half_open_probes: u32) -> Self {
        Self {
            failure_threshold,
            recovery_timeout,
            half_open_probes,
            state: AtomicU8::new(CBState::Closed.as_u8()),
            failures: AtomicU32::new(0),
            probes: AtomicU32::new(0),
            opened_at: Mutex::new(Instant::now()),
        }
    }

    fn transition_if_needed(&self) {
        let current = CBState::from_u8(self.state.load(Ordering::Acquire));
        if current != CBState::Open {
            return;
        }
        let opened = self.opened_at.lock().unwrap();
        if opened.elapsed().as_secs_f64() >= self.recovery_timeout {
            drop(opened);
            self.state.store(CBState::HalfOpen.as_u8(), Ordering::Release);
            self.probes.store(0, Ordering::Release);
        }
    }

    pub fn state(&self) -> CBState {
        self.transition_if_needed();
        CBState::from_u8(self.state.load(Ordering::Acquire))
    }

    pub fn allow(&self) -> bool {
        self.transition_if_needed();
        let current = CBState::from_u8(self.state.load(Ordering::Acquire));
        match current {
            CBState::Closed => true,
            CBState::Open => false,
            CBState::HalfOpen => {
                let probes = self.probes.fetch_add(1, Ordering::AcqRel);
                if probes < self.half_open_probes {
                    true
                } else {
                    self.probes.fetch_sub(1, Ordering::AcqRel);
                    false
                }
            }
        }
    }

    pub fn on_success(&self) {
        self.failures.store(0, Ordering::Release);
        self.probes.store(0, Ordering::Release);
        self.state.store(CBState::Closed.as_u8(), Ordering::Release);
    }

    pub fn on_failure(&self) {
        let current = CBState::from_u8(self.state.load(Ordering::Acquire));
        if current == CBState::HalfOpen {
            self.state.store(CBState::Open.as_u8(), Ordering::Release);
            *self.opened_at.lock().unwrap() = Instant::now();
            return;
        }
        let failures = self.failures.fetch_add(1, Ordering::AcqRel) + 1;
        if failures >= self.failure_threshold {
            self.state.store(CBState::Open.as_u8(), Ordering::Release);
            *self.opened_at.lock().unwrap() = Instant::now();
        }
    }

    pub fn failures(&self) -> u32 {
        self.failures.load(Ordering::Acquire)
    }

    pub fn probes_count(&self) -> u32 {
        self.probes.load(Ordering::Acquire)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::thread;
    use std::time::Duration;

    fn cb(threshold: u32, timeout: f64) -> FailureBreaker {
        FailureBreaker::new(threshold, timeout, 1)
    }

    #[test]
    fn closed_initially() {
        let cb = cb(3, 10.0);
        assert_eq!(cb.state(), CBState::Closed);
        assert!(cb.allow());
    }

    #[test]
    fn opens_after_threshold() {
        let cb = cb(3, 10.0);
        cb.on_failure();
        cb.on_failure();
        assert_eq!(cb.state(), CBState::Closed);
        cb.on_failure();
        assert_eq!(cb.state(), CBState::Open);
        assert!(!cb.allow());
    }

    #[test]
    fn half_open_after_timeout() {
        let cb = cb(2, 0.01);
        cb.on_failure();
        cb.on_failure();
        assert_eq!(cb.state(), CBState::Open);
        thread::sleep(Duration::from_millis(20));
        assert_eq!(cb.state(), CBState::HalfOpen);
        assert!(cb.allow());
    }

    #[test]
    fn success_closes() {
        let cb = cb(2, 0.01);
        cb.on_failure();
        cb.on_failure();
        thread::sleep(Duration::from_millis(20));
        assert!(cb.allow());
        cb.on_success();
        assert_eq!(cb.state(), CBState::Closed);
    }

    #[test]
    fn failure_in_half_open_reopens() {
        let cb = cb(2, 0.01);
        cb.on_failure();
        cb.on_failure();
        thread::sleep(Duration::from_millis(20));
        assert_eq!(cb.state(), CBState::HalfOpen);
        assert!(cb.allow());
        cb.on_failure();
        assert_eq!(cb.state(), CBState::Open);
    }

    #[test]
    fn failures_reset_on_success() {
        let cb = cb(3, 10.0);
        cb.on_failure();
        cb.on_failure();
        cb.on_success();
        assert_eq!(cb.failures(), 0);
        cb.on_failure();
        assert_eq!(cb.state(), CBState::Closed);
    }

    #[test]
    fn half_open_probe_limit() {
        let cb = FailureBreaker::new(2, 0.01, 2);
        cb.on_failure();
        cb.on_failure();
        thread::sleep(Duration::from_millis(20));
        assert!(cb.allow());
        assert!(cb.allow());
        assert!(!cb.allow());
    }
}
