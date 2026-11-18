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
    "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8",
    "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
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
    if unified.starts_with('/')
        || (b.len() >= 2
            && b.first().is_some_and(u8::is_ascii_alphabetic)
            && b.get(1) == Some(&b':'))
    {
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
    let joined = if rel.is_empty() {
        root.clone()
    } else {
        root.join(&rel)
    };
    // A missing target is still checked lexically (it may be a new upload's folder).
    let target = std::fs::canonicalize(&joined).unwrap_or(joined);
    if target.starts_with(&root) {
        Ok(target)
    } else {
        Err(PathError::Escapes)
    }
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

/// `GetDriveTypeW` result: unknown. Missing and unknown letters are never probed further,
/// because `exists()` and `GetVolumeInformationW` can hang on a disconnected network drive.
pub const DRIVE_UNKNOWN: u32 = 0;
/// `GetDriveTypeW` result: no such drive.
pub const DRIVE_NO_ROOT: u32 = 1;
/// `GetDriveTypeW` result: removable (USB stick, card).
pub const DRIVE_REMOVABLE: u32 = 2;
/// `GetDriveTypeW` result: fixed disk.
pub const DRIVE_FIXED: u32 = 3;
/// `GetDriveTypeW` result: network drive.
pub const DRIVE_REMOTE: u32 = 4;
/// `GetDriveTypeW` result: CD or DVD.
pub const DRIVE_CDROM: u32 = 5;
/// `GetDriveTypeW` result: RAM disk.
pub const DRIVE_RAMDISK: u32 = 6;

/// What is known about a drive letter: its type, its volume name (local drives only) and the
/// `\\server\share` a network drive points at.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct DriveFacts {
    /// A `DRIVE_*` value.
    pub kind: u32,
    /// The volume label; empty when there is none or the drive is not probed.
    pub volume: String,
    /// The `\\server\share` of a network drive; empty otherwise.
    pub unc: String,
}

/// Facts a caller already knows about a path; anything left `None` is looked up.
#[derive(Clone, Debug, Default)]
pub struct LabelHints {
    /// The volume label (`Some("")` means "known to have none").
    pub volume: Option<String>,
    /// The drive type.
    pub drive_type: Option<u32>,
    /// The network target of a mapped drive.
    pub unc_target: Option<String>,
}

const GENERIC_LABELS: [&str; 12] = [
    "",
    "shared folder",
    "shared drive",
    "shared",
    "folder",
    "share",
    "drive",
    "local disk",
    "network",
    "removable disk",
    "cd drive",
    "ram disk",
];

fn is_slash(c: char) -> bool {
    c == '\\' || c == '/'
}

fn is_unc(raw: &str) -> bool {
    raw.starts_with(r"\\") || raw.starts_with("//")
}

/// `C:` plus any slashes -> the drive letter (as typed).
fn drive_root_letter(raw: &str) -> Option<char> {
    let mut chars = raw.chars();
    let letter = chars.next().filter(char::is_ascii_alphabetic)?;
    (chars.next() == Some(':') && chars.all(is_slash)).then_some(letter)
}

/// `C:foo` -> `(C, "foo")`: a drive letter followed by a path that does not start with a slash.
fn drive_relative(raw: &str) -> Option<(char, &str)> {
    let mut chars = raw.chars();
    let letter = chars.next().filter(char::is_ascii_alphabetic)?;
    if chars.next() != Some(':') {
        return None;
    }
    let rest = raw.get(2..)?;
    (!rest.is_empty() && !rest.starts_with(is_slash)).then_some((letter, rest))
}

/// `\\host\share` (any slashes, trailing ones allowed) -> `(host, share)`.
fn unc_root(raw: &str) -> Option<(&str, &str)> {
    let rest = raw.strip_prefix(r"\\").or_else(|| raw.strip_prefix("//"))?;
    let (host, rest) = rest.split_once(is_slash)?;
    if rest.starts_with(is_slash) {
        return None;
    }
    let share = rest.trim_end_matches(is_slash);
    (!host.is_empty() && !share.is_empty() && !share.contains(is_slash)).then_some((host, share))
}

/// `"C:\"` for the drive a path starts with (any `X:` prefix, not UNC); empty otherwise.
pub fn drive_key(path: &str) -> String {
    let raw = path.trim();
    let mut chars = raw.chars();
    match (chars.next(), chars.next()) {
        (Some(l), Some(':')) if l.is_ascii_alphabetic() && !is_unc(raw) => {
            format!("{}:\\", l.to_ascii_uppercase())
        }
        _ => String::new(),
    }
}

fn expanduser(raw: &str) -> String {
    let home = || {
        std::env::var("USERPROFILE")
            .or_else(|_| std::env::var("HOME"))
            .ok()
            .filter(|h| !h.is_empty())
    };
    if raw == "~" {
        return home().unwrap_or_else(|| raw.to_owned());
    }
    match (
        raw.strip_prefix("~/").or_else(|| raw.strip_prefix("~\\")),
        home(),
    ) {
        (Some(rest), Some(h)) => format!(
            "{}{}{rest}",
            h.trim_end_matches(is_slash),
            std::path::MAIN_SEPARATOR
        ),
        _ => raw.to_owned(),
    }
}

/// Windows' `\\?\C:\…` canonical form, made ordinary (what 0.3.x stores).
pub(crate) fn strip_verbatim(p: PathBuf) -> PathBuf {
    let s = p.to_string_lossy();
    match s.strip_prefix(r"\\?\") {
        Some(rest) if !rest.starts_with("UNC") => PathBuf::from(rest),
        Some(rest) => PathBuf::from(format!(r"\{}", rest.trim_start_matches("UNC"))),
        None => p,
    }
}

/// How a path is stored: drive roots as `C:\`, `C:foo` as `C:\foo`, UNC as `\\server\share`,
/// none of them resolved (resolving `C:` gives the working folder, and resolving a
/// disconnected network drive can hang). Other folders are expanded and, when they exist,
/// made absolute.
pub fn canonical_stored_path(path: &str) -> String {
    let raw = path.trim();
    if raw.is_empty() {
        return String::new();
    }
    if let Some(letter) = drive_root_letter(raw) {
        return format!("{}:\\", letter.to_ascii_uppercase());
    }
    if let Some((letter, rest)) = drive_relative(raw) {
        return format!(
            "{}:\\{}",
            letter.to_ascii_uppercase(),
            rest.replace('/', "\\")
        );
    }
    if is_unc(raw) {
        let mut parts: Vec<String> = Vec::new();
        for part in raw.replace('/', "\\").split('\\') {
            match part {
                "" | "." => {}
                ".." => {
                    if parts.len() > 2 {
                        parts.pop();
                    }
                }
                other => parts.push(other.to_owned()),
            }
        }
        return format!(r"\\{}", parts.join("\\"));
    }
    let expanded = expanduser(raw);
    std::fs::canonicalize(&expanded)
        .map(strip_verbatim)
        .map(|p| p.to_string_lossy().into_owned())
        .unwrap_or(expanded)
}

/// `Volume (C:)`, or the Explorer name for the drive type.
fn format_drive(letter: &str, volume: &str, kind: u32, unc: &str) -> String {
    let shown = if letter.ends_with(':') {
        letter.to_uppercase()
    } else {
        format!("{}:", letter.to_uppercase())
    };
    if !volume.is_empty() {
        return format!("{volume} ({shown})");
    }
    match kind {
        DRIVE_REMOTE => {
            let unified = unc.replace('/', "\\");
            match unified.split('\\').rfind(|p| !p.is_empty()) {
                Some(name) => format!("{name} ({shown})"),
                None => format!("Network ({shown})"),
            }
        }
        DRIVE_REMOVABLE => format!("Removable Disk ({shown})"),
        DRIVE_CDROM => format!("CD Drive ({shown})"),
        DRIVE_RAMDISK => format!("RAM Disk ({shown})"),
        _ => format!("Local Disk ({shown})"),
    }
}

/// A label that is only the drive letter (`C:`, `c`, `C:\`) of a drive-root path.
fn bare_drive_label(explicit: &str, raw: &str) -> bool {
    let Some(letter) = drive_root_letter(raw.trim()) else {
        return false;
    };
    let text = explicit.trim().replace('/', "\\");
    let text = text.trim_end_matches('\\').to_uppercase();
    let upper = letter.to_ascii_uppercase();
    text == format!("{upper}:") || text == upper.to_string()
}

fn placeholder_label(explicit: &str, raw: &str) -> bool {
    let text = explicit.trim();
    GENERIC_LABELS.contains(&text.to_lowercase().as_str()) || bare_drive_label(text, raw)
}

/// Name to show for a share or drive, reading real drive facts. See
/// [`default_share_label_with`].
pub fn default_share_label(path: &str, explicit: &str) -> String {
    default_share_label_with(
        path,
        explicit,
        &LabelHints::default(),
        &crate::volume::drive_facts,
    )
}

/// Name to show for a share or drive.
///
/// A typed name wins. Placeholders such as "Shared folder", a bare `C:`, and a generated
/// fallback ("Local Disk (C:)", "games (Z:)") are replaced with the Explorer-style name when
/// a better one is known. Drive and UNC roots are recognised from the path string, so labels
/// still resolve off Windows. `facts` is asked for a drive letter only when `hints` is not
/// enough.
pub fn default_share_label_with(
    path: &str,
    explicit: &str,
    hints: &LabelHints,
    facts: &dyn Fn(&str) -> DriveFacts,
) -> String {
    let raw = path.trim();

    let computed_label =
        |vol_arg: Option<&str>, type_arg: Option<u32>, unc_arg: Option<&str>| -> String {
            if let Some(letter) = drive_root_letter(raw) {
                let letter = format!("{}:", letter.to_ascii_uppercase());
                if let Some(vol) = vol_arg.filter(|v| !v.is_empty()) {
                    return format!("{vol} ({letter})");
                }
                let mut kind = type_arg;
                let mut vol = vol_arg.map(str::to_owned);
                let mut unc = unc_arg.map(str::to_owned);
                if vol.is_none() || kind.is_none() || (kind == Some(DRIVE_REMOTE) && unc.is_none())
                {
                    let f = facts(raw);
                    kind = kind.or(Some(f.kind));
                    vol = vol.or(Some(f.volume));
                    unc = unc.or(Some(f.unc));
                }
                if let Some(vol) = vol.filter(|v| !v.is_empty()) {
                    return format!("{vol} ({letter})");
                }
                return format_drive(
                    &letter,
                    "",
                    kind.unwrap_or(DRIVE_UNKNOWN),
                    unc.as_deref().unwrap_or(""),
                );
            }
            if let Some((host, share)) = unc_root(raw) {
                let label = format!("{host}:{share}");
                return match vol_arg.filter(|v| !v.is_empty()) {
                    Some(v) => format!("{v} ({label})"),
                    None => label,
                };
            }
            let named = if is_unc(raw) || drive_relative(raw).is_some() {
                raw.to_owned()
            } else {
                expanduser(raw)
            };
            let unified = named.replace('\\', "/");
            if let Some(last) = unified.split('/').rfind(|p| !matches!(*p, "" | "." | "..")) {
                return last.to_owned();
            }
            if let Some(v) = vol_arg.filter(|v| !v.is_empty()) {
                return v.to_owned();
            }
            let anchor = match named.chars().next() {
                Some(c) if is_slash(c) => c.to_string(),
                Some(_) => named.clone(),
                None => "/".to_owned(),
            };
            match anchor.trim_end_matches(is_slash) {
                "" => "/".to_owned(),
                other => other.to_owned(),
            }
        };

    let (volume, kind, unc) = (
        hints.volume.as_deref(),
        hints.drive_type,
        hints.unc_target.as_deref(),
    );
    let computed = computed_label(volume, kind, unc);
    let explicit = explicit.trim();
    if placeholder_label(explicit, raw) {
        return computed;
    }
    if explicit == computed {
        return explicit.to_owned();
    }
    let fallback = computed_label(Some(""), kind, unc);
    if explicit == fallback && computed != fallback {
        return computed;
    }
    explicit.to_owned()
}

/// True when a stored label is a placeholder (or a stale generated name) that
/// [`default_share_label`] would replace.
pub fn share_label_needs_repair(path: &str, label: &str) -> bool {
    share_label_needs_repair_with(path, label, &crate::volume::drive_facts)
}

/// [`share_label_needs_repair`] with the drive facts supplied.
pub fn share_label_needs_repair_with(
    path: &str,
    label: &str,
    facts: &dyn Fn(&str) -> DriveFacts,
) -> bool {
    let stored = label.trim();
    default_share_label_with(path, stored, &LabelHints::default(), facts) != stored
}

/// True when a folder can be shared. Drive roots are judged by their drive type, not
/// `exists()`, which can hang on a disconnected network drive.
pub fn folder_is_available(path: &str) -> bool {
    folder_is_available_with(path, &crate::volume::drive_facts)
}

/// [`folder_is_available`] with the drive facts supplied.
pub fn folder_is_available_with(path: &str, facts: &dyn Fn(&str) -> DriveFacts) -> bool {
    let raw = path.trim();
    if drive_root_letter(raw).is_some() {
        return facts(&canonical_stored_path(raw)).kind >= DRIVE_REMOVABLE;
    }
    Path::new(&expanduser(raw)).is_dir()
}

/// A name that does not clash with what is in `dir`: `name (1).ext`, `name (2).ext`, …
pub fn unique_name(dir: &Path, name: &str) -> PathBuf {
    let first = dir.join(name);
    if !first.exists() {
        return first;
    }
    let p = Path::new(name);
    let stem = p
        .file_stem()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_else(|| name.to_owned());
    let ext = p
        .extension()
        .map(|e| format!(".{}", e.to_string_lossy()))
        .unwrap_or_default();
    (1..)
        .map(|n| dir.join(format!("{stem} ({n}){ext}")))
        .find(|c| !c.exists())
        .unwrap_or(first)
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
        assert_eq!(
            sanitize_relpath("file.txt:secret"),
            Err(PathError::AlternateStream)
        );
        assert_eq!(sanitize_relpath("docs/aux.txt"), Err(PathError::Reserved));
        assert_eq!(
            sanitize_relpath("docs/console.txt").as_deref(),
            Ok("docs/console.txt")
        );
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

    fn unknown(_: &str) -> DriveFacts {
        DriveFacts::default()
    }

    fn label(
        path: &str,
        explicit: &str,
        vol: Option<&str>,
        kind: Option<u32>,
        unc: Option<&str>,
    ) -> String {
        let hints = LabelHints {
            volume: vol.map(str::to_owned),
            drive_type: kind,
            unc_target: unc.map(str::to_owned),
        };
        default_share_label_with(path, explicit, &hints, &unknown)
    }

    #[test]
    fn canonical_drive_and_unc_paths() {
        assert_eq!(canonical_stored_path("C:"), r"C:\");
        assert_eq!(canonical_stored_path("c:/"), r"C:\");
        assert_eq!(canonical_stored_path(r"C:\"), r"C:\");
        assert_eq!(canonical_stored_path("C:foo"), r"C:\foo");
        assert_eq!(
            canonical_stored_path("//nas/public/files"),
            r"\\nas\public\files"
        );
        assert_eq!(canonical_stored_path(r"\\nas\public"), r"\\nas\public");
        assert_eq!(
            canonical_stored_path(r"\\nas\public\a\..\..\.."),
            r"\\nas\public"
        );
        assert_eq!(canonical_stored_path("  "), "");
    }

    #[test]
    fn drive_kind_labels_and_custom_names() {
        let n = Some(r"\\nas\games");
        assert_eq!(
            label("Z:", "", Some(""), Some(DRIVE_REMOTE), n),
            "games (Z:)"
        );
        assert_eq!(
            label("Z:", "", Some("Games"), Some(DRIVE_REMOTE), n),
            "Games (Z:)"
        );
        assert_eq!(
            label("Z:", "", Some(""), Some(DRIVE_REMOTE), None),
            "Network (Z:)"
        );
        assert_eq!(
            label("E:", "", Some(""), Some(2), None),
            "Removable Disk (E:)"
        );
        assert_eq!(
            label("D:", "", Some(""), Some(DRIVE_CDROM), None),
            "CD Drive (D:)"
        );
        assert_eq!(
            label("R:", "", Some(""), Some(DRIVE_RAMDISK), None),
            "RAM Disk (R:)"
        );
        assert_eq!(
            label(r"C:\", "OS Disk", Some("Windows"), Some(DRIVE_FIXED), None),
            "OS Disk"
        );
        assert_eq!(
            label(
                r"C:\",
                "Shared folder",
                Some("Windows"),
                Some(DRIVE_FIXED),
                None
            ),
            "Windows (C:)"
        );
        assert_eq!(
            label("/home/joe/Photos", "Shared folder", None, None, None),
            "Photos"
        );
    }

    #[test]
    fn drive_and_unc_labels_are_never_empty() {
        assert_eq!(
            label(r"C:\ ", "", Some("Windows"), None, None),
            "Windows (C:)"
        );
        assert_eq!(label("C:/", "", Some("Data"), None, None), "Data (C:)");
        assert_eq!(label("E:", "", None, None, None), "Local Disk (E:)");
        assert_eq!(
            label(r"\\media\films", "", Some("Media"), None, None),
            "Media (media:films)"
        );
        assert_eq!(label("//nas/public", "", None, None, None), "nas:public");
        assert_eq!(label("/home/joe/Photos", "", None, None, None), "Photos");
        assert_eq!(
            label("/home/joe/Photos", "  Family  ", None, None, None),
            "Family"
        );
        assert_eq!(label(r"C:\", "  ", None, None, None), "Local Disk (C:)");
        assert_eq!(label(r"C:\", "C:", None, None, None), "Local Disk (C:)");
        assert_eq!(label(r"C:\", "c", None, None, None), "Local Disk (C:)");
        assert_eq!(label("/", "", None, None, None), "/");
        assert_eq!(label("D:/Photos/2024/", "", None, None, None), "2024");
    }

    #[test]
    fn placeholder_labels_become_names() {
        // The cases of the Python UI's shareLabel helper, computed by the engine here.
        assert_eq!(
            label(r"C:\", "Shared folder", None, Some(DRIVE_UNKNOWN), None),
            "Local Disk (C:)"
        );
        assert_eq!(label("", "Windows (C:)", None, None, None), "Windows (C:)");
        assert_eq!(
            label(r"D:\Games", "Shared folder", None, None, None),
            "Games"
        );
        assert_eq!(label(r"\\nas\games", "", None, None, None), "nas:games");
    }

    #[test]
    fn a_stale_generated_name_is_upgraded_when_the_volume_is_known() {
        let facts = |_: &str| DriveFacts {
            kind: DRIVE_FIXED,
            volume: "Windows".into(),
            unc: String::new(),
        };
        let name =
            |label: &str| default_share_label_with(r"C:\", label, &LabelHints::default(), &facts);
        assert_eq!(name("Local Disk (C:)"), "Windows (C:)");
        assert_eq!(name("Mine"), "Mine");
        assert!(share_label_needs_repair_with(
            r"C:\",
            "Shared folder",
            &facts
        ));
        assert!(share_label_needs_repair_with(
            r"C:\",
            "Local Disk (C:)",
            &facts
        ));
        assert!(!share_label_needs_repair_with(
            r"C:\",
            "Windows (C:)",
            &facts
        ));
        assert!(!share_label_needs_repair_with(
            "/home/joe/Downloads",
            "Downloads",
            &unknown
        ));
        assert!(share_label_needs_repair_with(
            "/home/joe/Downloads",
            "",
            &unknown
        ));
    }

    #[test]
    fn drive_roots_are_available_by_type_and_folders_by_existence() {
        let fixed = |_: &str| DriveFacts {
            kind: DRIVE_FIXED,
            ..DriveFacts::default()
        };
        let missing = |_: &str| DriveFacts {
            kind: DRIVE_NO_ROOT,
            ..DriveFacts::default()
        };
        assert!(folder_is_available_with("C:", &fixed));
        assert!(!folder_is_available_with("Z:", &missing));
        assert!(!folder_is_available_with("C:", &unknown));
        let dir = tempfile::tempdir().unwrap_or_else(|e| unreachable!("{e}"));
        let text = dir.path().to_string_lossy().into_owned();
        assert!(folder_is_available_with(&text, &missing));
        std::fs::write(dir.path().join("f"), b"x").unwrap_or_else(|e| unreachable!("{e}"));
        assert!(!folder_is_available_with(
            &dir.path().join("f").to_string_lossy(),
            &fixed
        ));
        assert!(!folder_is_available_with(
            &dir.path().join("none").to_string_lossy(),
            &fixed
        ));
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
