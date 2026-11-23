//! Speed limits: one token bucket per direction, shared by every transfer, changed live
//! (Hearth 0.3.x's algorithm: about 50 ms of burst, virtual time).

use std::sync::Mutex;
use std::time::{Duration, Instant};

/// A bytes-per-second limit (0: unlimited).
#[derive(Debug)]
pub struct Bucket {
    inner: Mutex<(u64, Instant)>,
}

impl Bucket {
    /// A bucket at `rate` bytes a second.
    pub fn new(rate: u64) -> Self {
        Bucket {
            inner: Mutex::new((rate, Instant::now())),
        }
    }

    /// The limit.
    pub fn rate(&self) -> u64 {
        self.inner.lock().map(|g| g.0).unwrap_or(0)
    }

    /// Changes the limit; transfers under way follow it.
    pub fn set_rate(&self, rate: u64) {
        if let Ok(mut g) = self.inner.lock() {
            // Keep the schedule when going from one limit to another; start afresh otherwise.
            if !(g.0 > 0 && rate > 0) {
                g.1 = Instant::now();
            }
            g.0 = rate;
        }
    }

    /// Books `n` bytes; how long to wait before sending or keeping them.
    pub fn reserve(&self, n: u64) -> Duration {
        let Ok(mut g) = self.inner.lock() else {
            return Duration::ZERO;
        };
        let rate = g.0;
        if n == 0 || rate == 0 {
            return Duration::ZERO;
        }
        let now = Instant::now();
        let burst = Duration::from_secs_f64((rate as f64 * 0.05).max(1024.0) / rate as f64);
        let earliest = now.checked_sub(burst).unwrap_or(now);
        let start = g.1.max(earliest);
        g.1 = start + Duration::from_secs_f64(n as f64 / rate as f64);
        start.saturating_duration_since(now)
    }

    /// Waits until `n` bytes may pass (in steps, so a raised limit takes effect soon).
    pub async fn take(&self, n: u64) {
        let deadline = Instant::now() + self.reserve(n);
        loop {
            let left = deadline.saturating_duration_since(Instant::now());
            if left.is_zero() {
                return;
            }
            tokio::time::sleep(left.min(Duration::from_millis(250))).await;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test(flavor = "current_thread")]
    async fn a_limit_paces_bytes_within_ten_percent() {
        // 100 KB at 50 KB/s in 8 KiB steps takes about 2 s (0.3.x's test).
        let b = Bucket::new(50_000);
        let start = Instant::now();
        let mut sent = 0u64;
        while sent < 100_000 {
            b.take(8192).await;
            sent += 8192;
        }
        let t = start.elapsed().as_secs_f64();
        assert!((1.8..=2.3).contains(&t), "{t}");
        // Unlimited never waits.
        let free = Bucket::new(0);
        assert_eq!(free.reserve(1 << 30), Duration::ZERO);
    }
}
