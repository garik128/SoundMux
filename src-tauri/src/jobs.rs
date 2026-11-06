use std::{collections::HashMap, sync::{atomic::{AtomicBool, Ordering}, Arc, Mutex}, time::{Duration, Instant}};

#[derive(Default)]
pub struct Registry {
    pub running: HashMap<String, Arc<AtomicBool>>,
    cancelled: HashMap<String, Instant>,
}

impl Registry {
    fn prune(&mut self) {
        self.cancelled.retain(|_, time| time.elapsed() < Duration::from_secs(300));
    }

    pub fn register(&mut self, id: String) -> Result<Arc<AtomicBool>, String> {
        self.prune();
        if self.running.contains_key(&id) { return Err("Задача уже запущена".into()); }
        let flag = Arc::new(AtomicBool::new(self.cancelled.remove(&id).is_some()));
        self.running.insert(id, flag.clone());
        Ok(flag)
    }

    pub fn cancel(&mut self, id: String) {
        self.prune();
        if let Some(flag) = self.running.get(&id) { flag.store(true, Ordering::SeqCst); }
        else { self.cancelled.insert(id, Instant::now()); }
    }
}

#[derive(Default)]
pub struct Jobs(pub Mutex<Registry>);

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn cancellation_before_registration_stops_the_worker() {
        let mut jobs = Registry::default();
        jobs.cancel("early".into());
        assert!(jobs.register("early".into()).unwrap().load(Ordering::SeqCst));
        assert!(!jobs.register("other".into()).unwrap().load(Ordering::SeqCst));
        jobs.cancel("other".into());
        assert!(jobs.running["other"].load(Ordering::SeqCst));
        assert!(jobs.register("other".into()).is_err());
    }
}
