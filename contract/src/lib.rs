//! SafeCell policy contract.
//!
//! Two jobs, both of them things an off-chain log cannot do for itself:
//!
//! 1. **Spend policy.** An agent holds a function-call key that can call exactly
//!    one method (`anchor`) on this contract. It cannot transfer funds, cannot
//!    add keys, cannot upgrade the contract. The contract itself enforces a
//!    per-call ceiling and a rolling daily ceiling, and the owner can revoke the
//!    agent at any time.
//! 2. **Receipt anchoring.** Every approved execution writes its hash-chained
//!    receipt head on-chain, and the contract refuses any anchor whose
//!    `prev_hash` is not the current head. A broken link is rejected by the
//!    chain, not merely noticed by a reader afterwards, so the audit trail is
//!    third-party verifiable and append-only.
//!
//! Nothing here holds user funds. The contract stores hashes and counters.

use near_sdk::store::IterableMap;
use near_sdk::json_types::U128;
use near_sdk::{env, near, near_bindgen, AccountId, BorshStorageKey, NearToken, PanicOnDefault};

/// Sentinel used as the `prev_hash` of the first anchor in a chain.
const GENESIS: &str = "genesis";
/// Milliseconds in a UTC day.
const DAY_MS: u64 = 86_400_000;

#[derive(BorshStorageKey)]
#[near(serializers = [borsh])]
enum StorageKey {
    Anchors,
}

#[near(serializers = [borsh, json])]
#[derive(Clone)]
pub struct Anchor {
    pub seq: u64,
    pub execution_id: String,
    pub receipt_hash: String,
    pub prev_hash: String,
    pub action: String,
    pub cost: U128,
    pub agent: AccountId,
    pub block_height: u64,
    pub timestamp_ms: u64,
}

#[near(serializers = [borsh, json])]
#[derive(Clone)]
pub struct Policy {
    /// Maximum `cost` (yoctoNEAR) a single execution may declare.
    pub per_call_limit: U128,
    /// Maximum total `cost` per UTC day.
    pub daily_limit: U128,
    /// Actions the agent is permitted to anchor, e.g. `exec:python3`.
    pub allowlist: Vec<String>,
}

#[near(contract_state)]
#[derive(PanicOnDefault)]
pub struct Contract {
    pub owner: AccountId,
    pub agent: AccountId,
    pub policy: Policy,
    pub head: String,
    pub chain_len: u64,
    pub day: u64,
    pub spent_today: U128,
    pub revoked: bool,
    /// insertion order, so `verify_chain` can re-walk the whole history
    pub order: Vec<String>,
    pub anchors: IterableMap<String, Anchor>,
}

#[near_bindgen]
impl Contract {
    #[init]
    pub fn new(
        owner: AccountId,
        agent: AccountId,
        per_call_limit: U128,
        daily_limit: U128,
        allowlist: Vec<String>,
    ) -> Self {
        assert!(per_call_limit.0 > 0, "per_call_limit must be positive");
        assert!(
            daily_limit.0 >= per_call_limit.0,
            "daily_limit must be >= per_call_limit"
        );
        assert!(!allowlist.is_empty(), "allowlist must not be empty");
        Self {
            owner,
            agent,
            policy: Policy {
                per_call_limit,
                daily_limit,
                allowlist,
            },
            head: GENESIS.to_string(),
            chain_len: 0,
            day: 0,
            spent_today: U128(0),
            revoked: false,
            order: Vec::new(),
            anchors: IterableMap::new(StorageKey::Anchors),
        }
    }

    /// Anchor one execution receipt. Callable only by the agent account.
    ///
    /// Rejects: wrong caller, revoked agent, unknown action, replay, a broken
    /// `prev_hash` link, a per-call overrun, or a daily-budget overrun.
    pub fn anchor(
        &mut self,
        execution_id: String,
        receipt_hash: String,
        prev_hash: String,
        action: String,
        cost: U128,
    ) -> u64 {
        let predecessor = env::predecessor_account_id();
        if predecessor != self.agent {
            env::panic_str("ERR_NOT_AGENT");
        }
        if self.revoked {
            env::panic_str("ERR_AGENT_REVOKED");
        }
        if !self.policy.allowlist.iter().any(|a| a == &action) {
            env::panic_str("ERR_ACTION_NOT_ALLOWED");
        }
        if self.anchors.contains_key(&execution_id) {
            env::panic_str("ERR_ALREADY_ANCHORED");
        }
        // The chain link is enforced here, not checked later.
        if prev_hash != self.head {
            env::panic_str("ERR_CHAIN_BREAK");
        }
        if cost.0 > self.policy.per_call_limit.0 {
            env::panic_str("ERR_PER_CALL_LIMIT");
        }

        let block_ms = env::block_timestamp_ms();
        let today = block_ms / DAY_MS;
        if today != self.day {
            self.day = today;
            self.spent_today = U128(0);
        }
        let spent = self.spent_today.0 + cost.0;
        if spent > self.policy.daily_limit.0 {
            env::panic_str("ERR_DAILY_LIMIT");
        }
        self.spent_today = U128(spent);

        self.chain_len += 1;
        let anchor = Anchor {
            seq: self.chain_len,
            execution_id: execution_id.clone(),
            receipt_hash: receipt_hash.clone(),
            prev_hash: prev_hash.clone(),
            action: action.clone(),
            cost,
            agent: predecessor,
            block_height: env::block_height(),
            timestamp_ms: block_ms,
        };
        self.anchors.insert(execution_id.clone(), anchor.clone());
        self.order.push(execution_id.clone());
        self.head = receipt_hash.clone();

        env::log_str(&format!(
            "{{\"standard\":\"safecell\",\"version\":\"1.0.0\",\"event\":\"anchor\",\
             \"execution_id\":\"{}\",\"seq\":{},\"receipt_hash\":\"{}\",\"prev_hash\":\"{}\",\
             \"action\":\"{}\",\"cost\":\"{}\"}}",
            execution_id, anchor.seq, receipt_hash, prev_hash, action, cost.0
        ));

        anchor.seq
    }

    // ----- owner controls (owner account only, never the agent) -----

    pub fn set_policy(
        &mut self,
        per_call_limit: U128,
        daily_limit: U128,
        allowlist: Vec<String>,
    ) {
        if env::predecessor_account_id() != self.owner {
            env::panic_str("ERR_NOT_OWNER");
        }
        assert!(per_call_limit.0 > 0, "per_call_limit must be positive");
        assert!(
            daily_limit.0 >= per_call_limit.0,
            "daily_limit must be >= per_call_limit"
        );
        assert!(!allowlist.is_empty(), "allowlist must not be empty");
        self.policy = Policy {
            per_call_limit,
            daily_limit,
            allowlist,
        };
    }

    pub fn set_agent(&mut self, agent: AccountId) {
        if env::predecessor_account_id() != self.owner {
            env::panic_str("ERR_NOT_OWNER");
        }
        self.agent = agent;
    }

    /// Kill switch. The agent's key still exists; the contract stops honouring it.
    pub fn revoke(&mut self) {
        if env::predecessor_account_id() != self.owner {
            env::panic_str("ERR_NOT_OWNER");
        }
        self.revoked = true;
    }

    pub fn unrevoke(&mut self) {
        if env::predecessor_account_id() != self.owner {
            env::panic_str("ERR_NOT_OWNER");
        }
        self.revoked = false;
    }

    // ----- views -----

    pub fn get_anchor(&self, execution_id: String) -> Option<Anchor> {
        self.anchors.get(&execution_id).cloned()
    }

    pub fn head(&self) -> String {
        self.head.clone()
    }

    pub fn chain_len(&self) -> u64 {
        self.chain_len
    }

    pub fn remaining_today(&self) -> U128 {
        let today = env::block_timestamp_ms() / DAY_MS;
        if today != self.day {
            return self.policy.daily_limit;
        }
        U128(self.policy.daily_limit.0.saturating_sub(self.spent_today.0))
    }

    pub fn get_policy(&self) -> Policy {
        self.policy.clone()
    }

    pub fn get_agent(&self) -> AccountId {
        self.agent.clone()
    }

    pub fn is_revoked(&self) -> bool {
        self.revoked
    }

    /// Re-walk every stored anchor and check the links. Anyone can call this;
    /// it reads only on-chain state, so it is a real audit rather than a claim.
    pub fn verify_chain(&self) -> bool {
        let mut prev = GENESIS.to_string();
        for id in self.order.iter() {
            let a = match self.anchors.get(id) {
                Some(a) => a,
                None => return false,
            };
            if a.prev_hash != prev {
                return false;
            }
            prev = a.receipt_hash.clone();
        }
        prev == self.head
    }

    /// sha256 of a receipt payload, so callers do not have to agree on a hash
    /// implementation with the off-chain runner.
    pub fn hash_receipt(payload: String) -> String {
        let digest = env::sha256(payload.as_bytes());
        digest.iter().map(|b| format!("{:02x}", b)).collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use near_sdk::test_utils::VMContextBuilder;
    use near_sdk::{testing_env, Gas};

    const OWNER: &str = "owner.testnet";
    const AGENT: &str = "agent.testnet";

    fn ctx(predecessor: &str) -> VMContextBuilder {
        let mut b = VMContextBuilder::new();
        b.predecessor_account_id(predecessor.parse().unwrap())
            .current_account_id("policy.testnet".parse().unwrap())
            .block_height(1)
            .block_timestamp(1_700_000_000_000_000_000)
            .attached_deposit(NearToken::from_yoctonear(0))
            .prepaid_gas(Gas::from_tgas(100));
        b
    }

    fn contract() -> Contract {
        testing_env!(ctx(OWNER).build());
        Contract::new(
            OWNER.parse().unwrap(),
            AGENT.parse().unwrap(),
            U128(10_000),
            U128(25_000),
            vec!["exec:python3".to_string(), "exec:node".to_string()],
        )
    }

    fn anchor(c: &mut Contract, id: &str, hash: &str) -> u64 {
        let prev = c.head();
        c.anchor(
            id.to_string(),
            hash.to_string(),
            prev,
            "exec:python3".to_string(),
            U128(1_000),
        )
    }

    #[test]
    fn anchors_chain_and_advance_head() {
        let mut c = contract();
        testing_env!(ctx(AGENT).build());
        assert_eq!(anchor(&mut c, "run1", "aaa"), 1);
        assert_eq!(anchor(&mut c, "run2", "bbb"), 2);
        assert_eq!(c.head(), "bbb");
        assert_eq!(c.chain_len(), 2);
        assert!(c.verify_chain());
        let a = c.get_anchor("run1".to_string()).unwrap();
        assert_eq!(a.prev_hash, "genesis");
        assert_eq!(a.agent, AGENT.parse::<AccountId>().unwrap());
    }

    #[test]
    fn rejects_broken_chain_link() {
        let mut c = contract();
        testing_env!(ctx(AGENT).build());
        anchor(&mut c, "run1", "aaa");
        let res = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            c.anchor(
                "run2".to_string(),
                "bbb".to_string(),
                "not-the-head".to_string(),
                "exec:python3".to_string(),
                U128(1_000),
            )
        }));
        assert!(res.is_err(), "a broken prev_hash must be rejected");
        assert_eq!(c.chain_len(), 1);
    }

    #[test]
    fn rejects_per_call_overrun() {
        let mut c = contract();
        testing_env!(ctx(AGENT).build());
        let prev = c.head();
        let res = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            c.anchor(
                "x".to_string(),
                "h".to_string(),
                prev,
                "exec:python3".to_string(),
                U128(10_001),
            )
        }));
        assert!(res.is_err());
        assert_eq!(c.chain_len(), 0);
    }

    #[test]
    fn rejects_daily_budget_overrun() {
        let mut c = contract();
        testing_env!(ctx(AGENT).build());
        for i in 0..25 {
            anchor(&mut c, &format!("run{}", i), &format!("h{}", i));
        }
        assert_eq!(c.chain_len(), 25);
        assert_eq!(c.remaining_today(), U128(0));
        let prev = c.head();
        let res = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            c.anchor(
                "one-too-many".to_string(),
                "h".to_string(),
                prev,
                "exec:python3".to_string(),
                U128(1_000),
            )
        }));
        assert!(res.is_err(), "daily ceiling must stop the 26th execution");
        assert_eq!(c.chain_len(), 25);
    }

    #[test]
    fn rejects_replay_of_same_execution() {
        let mut c = contract();
        testing_env!(ctx(AGENT).build());
        anchor(&mut c, "run1", "aaa");
        let prev = c.head();
        let res = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            c.anchor(
                "run1".to_string(),
                "aaa".to_string(),
                prev,
                "exec:python3".to_string(),
                U128(1_000),
            )
        }));
        assert!(res.is_err(), "same execution_id must not anchor twice");
    }

    #[test]
    fn rejects_call_from_anyone_but_agent() {
        let mut c = contract();
        testing_env!(ctx("stranger.testnet").build());
        let prev = c.head();
        let res = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            c.anchor(
                "run1".to_string(),
                "aaa".to_string(),
                prev,
                "exec:python3".to_string(),
                U128(1_000),
            )
        }));
        assert!(res.is_err());
    }

    #[test]
    fn rejects_action_outside_allowlist() {
        let mut c = contract();
        testing_env!(ctx(AGENT).build());
        let prev = c.head();
        let res = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            c.anchor(
                "run1".to_string(),
                "aaa".to_string(),
                prev,
                "exec:curl".to_string(),
                U128(1_000),
            )
        }));
        assert!(res.is_err());
    }

    #[test]
    fn revoke_is_a_kill_switch() {
        let mut c = contract();
        testing_env!(ctx(OWNER).build());
        c.revoke();
        testing_env!(ctx(AGENT).build());
        let prev = c.head();
        let res = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            c.anchor(
                "run1".to_string(),
                "aaa".to_string(),
                prev,
                "exec:python3".to_string(),
                U128(1_000),
            )
        }));
        assert!(res.is_err(), "revoked agent must not be able to anchor");
        assert!(c.is_revoked());
    }

    #[test]
    fn only_owner_can_change_policy() {
        let mut c = contract();
        testing_env!(ctx(AGENT).build());
        let res = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            c.set_policy(U128(1), U128(1), vec!["exec:python3".to_string()])
        }));
        assert!(res.is_err(), "agent must not be able to widen its own limits");
    }

    #[test]
    fn daily_budget_rolls_over_at_utc_midnight() {
        let mut c = contract();
        testing_env!(ctx(AGENT).build());
        for i in 0..25 {
            anchor(&mut c, &format!("run{}", i), &format!("h{}", i));
        }
        assert_eq!(c.remaining_today(), U128(0));
        // next UTC day
        testing_env!(ctx(AGENT)
            .block_timestamp(1_700_000_000_000_000_000 + 86_400_000 * 1_000_000)
            .build());
        assert_eq!(c.remaining_today(), U128(25_000));
        let prev = c.head();
        c.anchor(
            "next-day".to_string(),
            "h".to_string(),
            prev,
            "exec:python3".to_string(),
            U128(1_000),
        );
        assert_eq!(c.chain_len(), 26);
    }
}
