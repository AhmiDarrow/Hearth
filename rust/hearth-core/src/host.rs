//! This PC's name as the system knows it (what Python's `socket.gethostname()` returns), for
//! the default PC name.

/// The host name, or `None` when the system will not say.
pub fn name() -> Option<String> {
    platform()
        .map(|n| n.trim().to_owned())
        .filter(|n| !n.is_empty())
}

#[cfg(windows)]
fn platform() -> Option<String> {
    std::env::var("COMPUTERNAME").ok()
}

/// `gethostname(2)`: the environment's `HOSTNAME` is a shell variable, rarely exported.
#[cfg(unix)]
#[allow(unsafe_code)]
fn platform() -> Option<String> {
    let mut buf = [0u8; 256];
    // SAFETY: `buf` is writable for its whole length, which the call is told.
    let rc = unsafe { libc::gethostname(buf.as_mut_ptr().cast(), buf.len()) };
    if rc != 0 {
        return None;
    }
    let end = buf.iter().position(|b| *b == 0).unwrap_or(buf.len());
    Some(String::from_utf8_lossy(buf.get(..end).unwrap_or_default()).into_owned())
}

#[cfg(not(any(windows, unix)))]
fn platform() -> Option<String> {
    None
}

#[cfg(test)]
mod tests {
    #[test]
    fn this_pc_has_a_name() {
        let n = super::name();
        assert!(
            n.as_deref().is_some_and(|n| !n.is_empty() && n != "PC"),
            "{n:?}"
        );
    }
}
