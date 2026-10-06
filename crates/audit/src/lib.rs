//! Append-only Audit Chain с hash-chain.
//!
//! Каждая запись содержит sha256 от {seq, prev_hash, timestamp, event, payload}.
//! Изменение любой записи ломает verify() — tamper-evident.
//!
//! timestamp — Unix millis (u64). Python-обёртка конвертирует в ISO при экспорте.
//! payload сериализуется в JSON через serde_json.

use serde_json::Value;
use sha2::{Digest, Sha256};
use std::time::{SystemTime, UNIX_EPOCH};

pub const GENESIS_HASH: &str = "0000000000000000000000000000000000000000000000000000000000000000";

/// Unix millis.
fn now_millis() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

/// Запись в audit-цепочке.
#[derive(Debug, Clone)]
pub struct AuditEntry {
    pub seq: u64,
    pub prev_hash: String,
    pub timestamp: u64,
    pub event: String,
    pub payload: Value,
    pub entry_hash: String,
}

impl AuditEntry {
    /// sha256 от канонизированного представления записи.
    fn compute_hash(&self) -> String {
        let canonical = serde_json::json!({
            "seq": self.seq,
            "prev_hash": self.prev_hash,
            "timestamp": self.timestamp,
            "event": self.event,
            "payload": self.payload,
        });
        let bytes = serde_json::to_vec(&canonical).expect("json serialization failed");
        let mut hasher = Sha256::new();
        hasher.update(&bytes);
        format!("{:x}", hasher.finalize())
    }
}

/// Append-only цепочка с hash-chain.
pub struct AuditChain {
    entries: Vec<AuditEntry>,
    last_hash: String,
}

impl AuditChain {
    pub fn new() -> Self {
        Self {
            entries: Vec::new(),
            last_hash: GENESIS_HASH.to_string(),
        }
    }

    pub fn append(&mut self, event: String, payload: Value) -> AuditEntry {
        let seq = self.entries.len() as u64 + 1;
        let mut entry = AuditEntry {
            seq,
            prev_hash: self.last_hash.clone(),
            timestamp: now_millis(),
            event,
            payload,
            entry_hash: String::new(),
        };
        entry.entry_hash = entry.compute_hash();
        self.last_hash = entry.entry_hash.clone();
        self.entries.push(entry.clone());
        entry
    }

    pub fn verify(&self) -> bool {
        let mut prev = GENESIS_HASH.to_string();
        for e in &self.entries {
            if e.prev_hash != prev {
                return false;
            }
            if e.compute_hash() != e.entry_hash {
                return false;
            }
            prev = e.entry_hash.clone();
        }
        true
    }

    pub fn len(&self) -> usize {
        self.entries.len()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    pub fn entries(&self) -> &[AuditEntry] {
        &self.entries
    }

    #[cfg(test)]
    fn tamper(&mut self, idx: usize) {
        if let Some(e) = self.entries.get_mut(idx) {
            e.payload = serde_json::json!({"tampered": true});
        }
    }
}

impl Default for AuditChain {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_chain_verifies() {
        let chain = AuditChain::new();
        assert!(chain.verify());
        assert_eq!(chain.len(), 0);
    }

    #[test]
    fn append_and_verify() {
        let mut chain = AuditChain::new();
        chain.append("e1".to_string(), serde_json::json!({"a": 1}));
        chain.append("e2".to_string(), serde_json::json!({"b": 2}));
        assert!(chain.verify());
        assert_eq!(chain.len(), 2);
        assert_eq!(chain.entries()[1].prev_hash, chain.entries()[0].entry_hash);
    }

    #[test]
    fn genesis_prev_hash() {
        let mut chain = AuditChain::new();
        let e = chain.append("first".to_string(), serde_json::json!({}));
        assert_eq!(e.prev_hash, GENESIS_HASH);
        assert_eq!(e.seq, 1);
    }

    #[test]
    fn tampering_detected() {
        let mut chain = AuditChain::new();
        chain.append("e1".to_string(), serde_json::json!({"a": 1}));
        chain.append("e2".to_string(), serde_json::json!({"b": 2}));
        assert!(chain.verify());
        chain.tamper(0);
        assert!(!chain.verify());
    }

    #[test]
    fn timestamp_is_unix_millis() {
        let mut chain = AuditChain::new();
        let before = now_millis();
        let e = chain.append("e".to_string(), serde_json::json!({}));
        let after = now_millis();
        assert!(e.timestamp >= before && e.timestamp <= after);
        // Разумный диапазон: > 2020-01-01 и < 2100-01-01
        assert!(e.timestamp > 1_577_836_800_000, "timestamp too small: {}", e.timestamp);
        assert!(e.timestamp < 4_102_444_800_000, "timestamp too large: {}", e.timestamp);
    }

    #[test]
    fn entry_hash_is_sha256_hex() {
        let mut chain = AuditChain::new();
        let e = chain.append("e".to_string(), serde_json::json!({}));
        assert_eq!(e.entry_hash.len(), 64);
        assert!(e.entry_hash.chars().all(|c| c.is_ascii_hexdigit()));
    }

    #[test]
    fn payload_order_does_not_matter() {
        let mut c1 = AuditChain::new();
        let mut c2 = AuditChain::new();
        let p1: Value = serde_json::from_str(r#"{"b": 2, "a": 1}"#).unwrap();
        let p2: Value = serde_json::from_str(r#"{"a": 1, "b": 2}"#).unwrap();
        let e1 = c1.append("e".to_string(), p1);
        let e2 = c2.append("e".to_string(), p2);
        assert_eq!(e1.payload, e2.payload);
    }
}
