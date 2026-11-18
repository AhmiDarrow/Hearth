//! Paths other PCs send: relative paths inside a share (never outside it), file names for
//! uploads and downloads, and how a shared folder is labelled. The rules are Hearth 0.3.x's,
//! so a Rust PC refuses exactly what a Python one refuses.

use std::path::{Path, PathBuf};

/// Why a path is refused (the messages other PCs show).
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum PathError {
    /// `..` or a link that leads out of the share.
    Escapes,
    /// An absolute path or drive letter.
    Absolute,
    /// A `\\host\share` path.
    Unc,
    /// A NUL character.
    IllegalCharacter,
    /// `name:stream`.
    AlternateStream,
    /// CON, PRN, AUX, NUL, COM1–9, LPT1–9.
    Reserved,
}

impl PathError {
    /// The text Hearth answers with.
    pub fn message(&self) -> &'static str {
        match self {
            PathError::Escapes => "Path escapes the shared folder",
            PathError::Absolute => "Absolute paths are not allowed",
            PathError::Unc => "UNC paths are not allowed",
            PathError::IllegalCharacter => "Path contains an illegal character",
            PathError::AlternateStream => "Alternate data streams are not allowed",
            PathError::Reserved => "That filename is reserved on Windows",
        }
    }
}

const RESERVED: [&str; 22] = [
    "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2",
    "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
];

fn reserved(segment: &str) -> bool {
    let stem = segment.split('.').next().unwrap_or("").to_uppercase();
    RESERVED.contains(&stem.as_str())
}

/// A relative path from another PC, made safe: forward slashes, no `.` or empty parts.
pub fn sanitize_relpath(raw: &str) -> Result<String, PathError> {
    let s = raw.trim();
    if matches!(s, "" | "." | "./" | ".\\") {
        return Ok(String::new());
    }
    if s.contains('\0') {
        return Err(PathError::IllegalCharacter);
    }
    let unified = s.replace('\\', "/");
    if unified.starts_with("//") {
        return Err(PathError::Unc);
    }
    let b = unified.as_bytes();
    if unified.starts_with('/') || (b.len() >= 2 && b.first().is_some_and(u8::is_ascii_alphabetic) && b.get(1) == Some(&b':')) {
        return Err(PathError::Absolute);
    }
    let mut parts = Vec::new();
    for seg in unified.split('/') {
        if seg.is_empty() || seg == "." {
            continue;
        }
        if seg == ".." {
            return Err(PathError::Escapes);
        }
        if seg.contains(':') {
            return Err(PathError::AlternateStream);
        }
        if reserved(seg) {
            return Err(PathError::Reserved);
        }
        parts.push(seg);
    }
    Ok(parts.join("/"))
}

/// Where `rel` points inside the share at `root`, following links, refused if that is outside
/// the share.
pub fn resolve_in_share(root: &Path, rel: &str) -> Result<PathBuf, PathError> {
    let rel = sanitize_relpath(rel)?;
    let root = std::fs::canonicalize(root).unwrap_or_else(|_| root.to_path_buf());
    let joined = if rel.is_empty() { root.clone() } else { root.join(&rel) };
    // A missing target is still checked lexically (it may be a new upload's folder).
    let target = std::fs::canonicalize(&joined).unwrap_or(joined);
    if target.starts_with(&root) { Ok(target) } else { Err(PathError::Escapes) }
}

/// A file name from another PC made safe to write here.
pub fn safe_filename(name: &str) -> String {
    let unified = name.replace('\\', "/");
    if unified.contains('\0') {
        return "upload.bin".into();
    }
    let mut n = unified.rsplit('/').next().unwrap_or("").trim().to_owned();
    if let Some(i) = n.find(':') {
        n = n[..i].trim().to_owned();
    }
    let n = n.trim_end_matches('.').to_owned();
    if n.is_empty() || n == "." || n == ".." || reserved(&n) {
        return "upload.bin".into();
    }
    n.chars().take(255).collect()
}

/// The label for a share: the given one, else the folder's name, else a drive (`"Windows
/// (C:)"`, `"C:"`) or network share (`"host:share"`).
pub fn share_label(path: &str, explicit: &str, volume: Option<&str>) -> String {
    if !explicit.trim().is_empty() {
        return explicit.trim().to_owned();
    }
    let raw = path.trim();
    let vol = volume.filter(|v| !v.trim().is_empty()).map(str::trim);
    let b = raw.as_bytes();
    let rest_is_slashes = |s: &str| s.chars().all(|c| c == '\\' || c == '/');
    if b.len() >= 2 && b.first().is_some_and(u8::is_ascii_alphabetic) && b.get(1) == Some(&b':') && rest_is_slashes(&raw[2..]) {
        let letter = raw[..2].to_uppercase();
        return match vol {
            Some(v) => format!("{v} ({letter})"),
            None => letter,
        };
    }
    let u = raw.replace('\\', "/");
    if let Some(rest) = u.strip_prefix("//") {
        let parts: Vec<&str> = rest.split('/').filter(|p| !p.is_empty()).collect();
        if parts.len() == 2 {
            let label = format!("{}:{}", parts[0], parts[1]);
            return match vol {
                Some(v) => format!("{v} ({label})"),
                None => label,
            };
        }
    }
    let name = Path::new(raw).file_name().map(|n| n.to_string_lossy().trim().to_owned()).unwrap_or_default();
    if !name.is_empty() && name != "." && name != ".." {
        return name;
    }
    if let Some(v) = vol {
        return v.to_owned();
    }
    let anchor = raw.trim_end_matches(['\\', '/']);
    if anchor.is_empty() { "/".into() } else { anchor.to_owned() }
}

/// A name that does not clash with what is in `dir`: `name (1).ext`, `name (2).ext`, …
pub fn unique_name(dir: &Path, name: &str) -> PathBuf {
    let first = dir.join(name);
    if !first.exists() {
        return first;
    }
    let p = Path::new(name);
    let stem = p.file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_else(|| name.to_owned());
    let ext = p.extension().map(|e| format!(".{}", e.to_string_lossy())).unwrap_or_default();
    (1..).map(|n| dir.join(format!("{stem} ({n}){ext}"))).find(|c| !c.exists()).unwrap_or(first)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn relative_paths_follow_0_3_rules() {
        assert_eq!(sanitize_relpath(" ./a\\b/./c/ ").as_deref(), Ok("a/b/c"));
        assert_eq!(sanitize_relpath(".").as_deref(), Ok(""));
        assert_eq!(sanitize_relpath("../x"), Err(PathError::Escapes));
        assert_eq!(sanitize_relpath("a/../../x"), Err(PathError::Escapes));
        assert_eq!(sanitize_relpath("/etc"), Err(PathError::Absolute));
        assert_eq!(sanitize_relpath("C:/Windows"), Err(PathError::Absolute));
        assert_eq!(sanitize_relpath("\\\\host\\share"), Err(PathError::Unc));
        assert_eq!(sanitize_relpath("a\0b"), Err(PathError::IllegalCharacter));
        assert_eq!(sanitize_relpath("file.txt:secret"), Err(PathError::AlternateStream));
        assert_eq!(sanitize_relpath("docs/aux.txt"), Err(PathError::Reserved));
        assert_eq!(sanitize_relpath("docs/console.txt").as_deref(), Ok("docs/console.txt"));
    }

    #[test]
    fn filenames_follow_0_3_rules() {
        assert_eq!(safe_filename("../../evil.txt"), "evil.txt");
        assert_eq!(safe_filename("C:\\dir\\file.txt"), "file.txt");
        assert_eq!(safe_filename("name.txt:stream"), "name.txt");
        assert_eq!(safe_filename("trailing..."), "trailing");
        assert_eq!(safe_filename("CON.txt"), "upload.bin");
        assert_eq!(safe_filename(".."), "upload.bin");
        assert_eq!(safe_filename("a\0b"), "upload.bin");
        assert_eq!(safe_filename(&"x".repeat(300)).len(), 255);
    }

    #[test]
    fn labels_follow_0_3_rules() {
        assert_eq!(share_label("C:\\", "", Some("Windows")), "Windows (C:)");
        assert_eq!(share_label("c:", "", None), "C:");
        assert_eq!(share_label("\\\\nas\\public", "", None), "nas:public");
        assert_eq!(share_label("//media/films/", "", Some("Media")), "Media (media:films)");
        assert_eq!(share_label("D:\\Photos\\2024", "", None), "2024");
        assert_eq!(share_label("D:\\Photos", " Family ", None), "Family");
    }

    #[test]
    fn shares_contain_their_paths() {
        let dir = tempfile::tempdir().unwrap_or_else(|e| unreachable!("{e}"));
        std::fs::create_dir_all(dir.path().join("in/sub")).unwrap_or_else(|e| unreachable!("{e}"));
        let root = dir.path().join("in");
        assert!(resolve_in_share(&root, "sub").is_ok());
        assert!(resolve_in_share(&root, "new/folder").is_ok());
        assert_eq!(resolve_in_share(&root, "../"), Err(PathError::Escapes));
        std::fs::write(root.join("a.txt"), b"x").unwrap_or_else(|e| unreachable!("{e}"));
        assert_eq!(unique_name(&root, "a.txt"), root.join("a (1).txt"));
    }
}
