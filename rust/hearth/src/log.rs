//! `hearth.log` in the profile, where the Python app writes what it prints: the start banner,
//! and what went wrong when there is no window to say it in.

use std::io::Write;
use std::path::Path;

/// Appends `text` as a line to the profile's `hearth.log` (never fails the caller).
pub fn line(root: &Path, text: &str) {
    let _ = std::fs::create_dir_all(root);
    if let Ok(mut f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(root.join("hearth.log"))
    {
        let _ = writeln!(f, "{text}");
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn lines_are_appended_to_what_is_there() {
        let d = tempfile::tempdir().unwrap_or_else(|e| unreachable!("{e}"));
        std::fs::write(d.path().join("hearth.log"), "from Python\n")
            .unwrap_or_else(|e| unreachable!("{e}"));
        super::line(d.path(), "Hearth  ·  Den");
        assert_eq!(
            std::fs::read_to_string(d.path().join("hearth.log")).unwrap_or_default(),
            "from Python\nHearth  ·  Den\n"
        );
    }
}
