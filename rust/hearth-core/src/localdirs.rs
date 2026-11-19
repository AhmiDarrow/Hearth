//! The share dialog's folder browser: the folders of this PC, as Hearth 0.4's
//! `/api/local-folders` lists them. It never leaves this PC.
//!
//! At the top ("This PC"): Home, the user's libraries and, on Windows, every mounted drive
//! under its Explorer name. Inside a folder: its subfolders by name ignoring case, without
//! dot names or the system folders Hearth will not browse (Windows, Program Files, …).

use std::path::{Path, PathBuf};

use crate::paths::{
    canonical_stored_path, default_share_label, drive_relative, drive_root_letter,
    folder_is_available, is_unc,
};

/// Folders the browser never lists or opens (compared ignoring case).
pub const SKIP_DIR_NAMES: [&str; 9] = [
    "windows",
    "program files",
    "program files (x86)",
    "programdata",
    "system volume information",
    "$recycle.bin",
    "recovery",
    "boot",
    "config.msi",
];

/// One folder the browser shows.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LocalDir {
    /// What it is called (a library's or a drive's name at the top).
    pub name: String,
    /// Where it is.
    pub path: String,
}

/// A folder's subfolders, or the top of this PC (`path` empty).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LocalDirs {
    /// The folder listed ("" for the top).
    pub path: String,
    /// The folder above it, when there is one.
    pub parent: Option<String>,
    /// Its subfolders.
    pub items: Vec<LocalDir>,
}

/// Why a folder could not be listed (the message is a catalog key).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum BrowseError {
    /// Not there.
    Missing,
    /// A system folder, or one this PC will not read.
    Unbrowsable,
    /// It did not answer in time (a stuck network drive).
    NotResponding,
}

impl BrowseError {
    /// What the user reads.
    pub fn message(self) -> &'static str {
        match self {
            BrowseError::Missing => "Folder does not exist on this PC",
            BrowseError::Unbrowsable => "That folder cannot be browsed",
            BrowseError::NotResponding => "That folder is not responding",
        }
    }
}

fn home() -> PathBuf {
    std::env::var_os("USERPROFILE")
        .or_else(|| std::env::var_os("HOME"))
        .map(PathBuf::from)
        .unwrap_or_default()
}

/// Whether the browser may open `path`: not a skipped folder, nor one directly under a
/// drive root that is (`C:\Windows`, `C:\Program Files`).
pub fn is_browsable(path: &Path) -> bool {
    let skipped = |s: &str| SKIP_DIR_NAMES.contains(&s.to_lowercase().as_str());
    if path
        .file_name()
        .is_some_and(|n| skipped(&n.to_string_lossy()))
    {
        return false;
    }
    if cfg!(windows) {
        let parts: Vec<String> = path
            .components()
            .map(|c| c.as_os_str().to_string_lossy().into_owned())
            .collect();
        // `C:\Windows` is [`C:`, `\`, `Windows`].
        if parts.get(2).is_some_and(|p| skipped(p)) {
            return false;
        }
    }
    true
}

fn dedup_key(path: &str) -> String {
    if drive_root_letter(path).is_some() || is_unc(path) {
        return canonical_stored_path(path);
    }
    std::fs::canonicalize(path)
        .map(crate::paths::strip_verbatim)
        .map_or_else(|_| path.to_owned(), |p| p.to_string_lossy().into_owned())
}

/// A typed folder as the share dialog understands it (Hearth 0.4's `resolve_user_path`): a
/// drive or UNC path as stored, `~` expanded, a name under Home, or a library's name
/// ("documents"); then canonical as shares are stored. `None` when nothing was typed or the
/// folder is not on this PC.
pub fn resolve_user_path(raw: &str, libraries: &[(String, PathBuf)]) -> Option<String> {
    let raw = raw.trim();
    if raw.is_empty() {
        return None;
    }
    let found = if drive_root_letter(raw).is_some() || drive_relative(raw).is_some() || is_unc(raw)
    {
        canonical_stored_path(raw)
    } else {
        let p = PathBuf::from(crate::paths::expanduser(raw));
        if p.exists() || p.is_absolute() {
            p.to_string_lossy().into_owned()
        } else {
            let under_home = home().join(raw);
            let last = Path::new(raw)
                .file_name()
                .map(|n| n.to_string_lossy().to_lowercase())
                .unwrap_or_default();
            if under_home.exists() {
                under_home.to_string_lossy().into_owned()
            } else if let Some((_, lib)) = libraries.iter().find(|(_, lib)| {
                lib.file_name().is_some_and(|n| {
                    let n = n.to_string_lossy().to_lowercase();
                    n == raw.to_lowercase() || n == last
                }) && lib.exists()
            }) {
                lib.to_string_lossy().into_owned()
            } else {
                p.to_string_lossy().into_owned()
            }
        }
    };
    folder_is_available(&found).then(|| canonical_stored_path(&found))
}

/// The top of this PC: Home, `libraries` (name, folder) and the mounted drives; each place
/// once.
pub fn top(libraries: &[(String, PathBuf)]) -> LocalDirs {
    let mut roots = vec![LocalDir {
        name: "Home".into(),
        path: home().to_string_lossy().into_owned(),
    }];
    roots.extend(libraries.iter().map(|(name, path)| LocalDir {
        name: name.clone(),
        path: path.to_string_lossy().into_owned(),
    }));
    if cfg!(windows) {
        roots.extend(
            crate::volume::iter_mounted_drives(None)
                .into_iter()
                .map(|d| LocalDir {
                    name: default_share_label(&d.root, ""),
                    path: d.root,
                }),
        );
    } else {
        roots.push(LocalDir {
            name: "/".into(),
            path: "/".into(),
        });
    }
    let mut seen = std::collections::HashSet::new();
    roots.retain(|r| seen.insert(dedup_key(&r.path)));
    LocalDirs {
        path: String::new(),
        parent: None,
        items: roots,
    }
}

/// The subfolders of `raw` (typed, so `~`, a drive letter, a UNC path or a path under Home
/// are understood), read on this thread.
pub fn subfolders(raw: &str) -> Result<LocalDirs, BrowseError> {
    let raw = raw.trim();
    let drive_root = drive_root_letter(raw).is_some();
    let path = if drive_root || drive_relative(raw).is_some() || is_unc(raw) {
        let canon = canonical_stored_path(raw);
        if drive_root && !folder_is_available(&canon) {
            return Err(BrowseError::Missing);
        }
        PathBuf::from(canon)
    } else {
        let p = PathBuf::from(crate::paths::expanduser(raw));
        let p = if p.is_absolute() { p } else { home().join(p) };
        std::fs::canonicalize(&p)
            .map(crate::paths::strip_verbatim)
            .map_err(|_| BrowseError::Missing)?
    };
    if !drive_root {
        match std::fs::metadata(&path) {
            Ok(m) if m.is_dir() => {}
            _ => return Err(BrowseError::Missing),
        }
    }
    if !is_browsable(&path) {
        return Err(BrowseError::Unbrowsable);
    }
    let mut items: Vec<LocalDir> = std::fs::read_dir(&path)
        .map(|rd| {
            rd.flatten()
                .filter_map(|e| {
                    let name = e.file_name().to_string_lossy().into_owned();
                    if name.starts_with('.')
                        || SKIP_DIR_NAMES.contains(&name.to_lowercase().as_str())
                    {
                        return None;
                    }
                    // Follows links, as Python's is_dir() does.
                    std::fs::metadata(e.path())
                        .ok()
                        .filter(std::fs::Metadata::is_dir)?;
                    Some(LocalDir {
                        path: e.path().to_string_lossy().into_owned(),
                        name,
                    })
                })
                .collect()
        })
        .unwrap_or_default();
    items.sort_by_key(|d| d.name.to_lowercase());
    let parent = path
        .parent()
        .filter(|p| *p != path && !p.as_os_str().is_empty())
        .map(|p| p.to_string_lossy().into_owned());
    Ok(LocalDirs {
        path: path.to_string_lossy().into_owned(),
        parent,
        items,
    })
}

/// [`top`] for an empty `raw`, else [`subfolders`], on its own thread and given up after
/// [`crate::server::list_timeout`]: a stuck network drive never holds the caller.
pub async fn browse(
    raw: String,
    libraries: Vec<(String, PathBuf)>,
) -> Result<LocalDirs, BrowseError> {
    let (tx, rx) = tokio::sync::oneshot::channel();
    let spawned = std::thread::Builder::new()
        .name("hearth-browse".into())
        .spawn(move || {
            let r = if raw.trim().is_empty() {
                Ok(top(&libraries))
            } else {
                subfolders(&raw)
            };
            let _ = tx.send(r);
        });
    if spawned.is_err() {
        return Err(BrowseError::Unbrowsable);
    }
    match tokio::time::timeout(crate::server::list_timeout(), rx).await {
        Ok(Ok(r)) => r,
        Ok(Err(_)) => Err(BrowseError::Unbrowsable),
        Err(_) => Err(BrowseError::NotResponding),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp() -> tempfile::TempDir {
        tempfile::tempdir().unwrap_or_else(|e| unreachable!("{e}"))
    }

    #[test]
    fn subfolders_by_name_without_files_dot_names_or_system_folders() {
        let d = tmp();
        for n in [
            "beta",
            "Alpha",
            ".hidden",
            "Windows",
            "$Recycle.Bin",
            "Gamma",
        ] {
            std::fs::create_dir(d.path().join(n)).unwrap_or_else(|e| unreachable!("{e}"));
        }
        std::fs::write(d.path().join("file.txt"), b"x").unwrap_or_else(|e| unreachable!("{e}"));
        let l = subfolders(&d.path().to_string_lossy()).unwrap_or_else(|e| unreachable!("{e:?}"));
        let names: Vec<&str> = l.items.iter().map(|i| i.name.as_str()).collect();
        assert_eq!(names, ["Alpha", "beta", "Gamma"]);
        assert!(l.parent.is_some());
        assert!(
            l.items
                .iter()
                .all(|i| Path::new(&i.path).parent() == Some(Path::new(&l.path)))
        );
    }

    #[test]
    fn missing_and_system_folders_say_why() {
        let d = tmp();
        let gone = d.path().join("gone");
        assert_eq!(
            subfolders(&gone.to_string_lossy()),
            Err(BrowseError::Missing)
        );
        let sys = d.path().join("ProgramData");
        std::fs::create_dir(&sys).unwrap_or_else(|e| unreachable!("{e}"));
        assert_eq!(
            subfolders(&sys.to_string_lossy()),
            Err(BrowseError::Unbrowsable)
        );
        assert!(!is_browsable(Path::new("C:/Windows")) || !cfg!(windows));
        assert!(is_browsable(&d.path().join("Photos")));
    }

    #[test]
    fn typed_folders_resolve_like_hearth_0_4() {
        let d = tmp();
        // A name nothing under Home has, so the library is what matches.
        let name = format!("HearthLibrary{}", std::process::id());
        let docs = d.path().join(&name);
        std::fs::create_dir(&docs).unwrap_or_else(|e| unreachable!("{e}"));
        let libs = vec![("Documents".to_owned(), docs.clone())];
        let canon = canonical_stored_path(&docs.to_string_lossy());
        assert_eq!(
            resolve_user_path(&docs.to_string_lossy(), &libs),
            Some(canon.clone())
        );
        let typed = format!("  {} ", name.to_lowercase());
        assert_eq!(
            resolve_user_path(&typed, &libs),
            Some(canon),
            "a library by name"
        );
        assert_eq!(resolve_user_path("", &libs), None);
        assert_eq!(
            resolve_user_path(&d.path().join("nope").to_string_lossy(), &libs),
            None
        );
        if cfg!(windows) {
            let sys = std::env::var("SystemDrive").unwrap_or_else(|_| "C:".into());
            assert_eq!(resolve_user_path(&sys, &libs), Some(format!("{sys}\\")));
        }
    }

    #[test]
    fn the_top_lists_home_then_libraries_then_drives_once_each() {
        let d = tmp();
        let home = home();
        let libs = vec![
            ("Documents".to_owned(), d.path().to_path_buf()),
            ("Again".to_owned(), d.path().to_path_buf()),
        ];
        let t = top(&libs);
        assert_eq!(t.path, "");
        assert_eq!(t.parent, None);
        assert_eq!(
            t.items.first().map(|i| i.path.clone()),
            Some(home.to_string_lossy().into_owned())
        );
        assert_eq!(t.items.iter().filter(|i| i.name == "Documents").count(), 1);
        assert!(
            t.items.iter().all(|i| i.name != "Again"),
            "a place listed once"
        );
        if cfg!(windows) {
            let sys = std::env::var("SystemDrive").unwrap_or_else(|_| "C:".into());
            assert!(
                t.items.iter().any(|i| i.path == format!("{sys}\\")),
                "drives"
            );
        }
    }

    #[test]
    fn browsing_runs_off_the_caller_and_lists_the_top_for_nothing_typed() {
        let rt = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap_or_else(|e| unreachable!("{e}"));
        let r = rt.block_on(browse(String::new(), Vec::new()));
        assert!(r.is_ok_and(|t| t.path.is_empty() && !t.items.is_empty()));
    }
}
