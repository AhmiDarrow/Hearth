//! What Windows knows about drive letters: type, volume label, network target. Used to name
//! shares the way Explorer does ("Local Disk (C:)", "Media (Z:)").
//!
//! Off Windows nothing is known, so every drive is `DRIVE_UNKNOWN` with no names.

use std::collections::HashMap;
use std::sync::{Mutex, PoisonError};

use crate::paths::{DRIVE_REMOTE, DRIVE_REMOVABLE, DriveFacts, canonical_stored_path, drive_key};

/// Mounted drives only: a drive that is missing now is not remembered, so one plugged in
/// later is not stuck as missing.
static CACHE: Mutex<Option<HashMap<String, DriveFacts>>> = Mutex::new(None);

#[cfg(windows)]
fn wide(text: &str) -> Vec<u16> {
    use std::os::windows::ffi::OsStrExt;
    std::ffi::OsStr::new(text)
        .encode_wide()
        .chain(std::iter::once(0))
        .collect()
}

#[cfg(windows)]
fn from_wide(buf: &[u16]) -> String {
    let end = buf.iter().position(|c| *c == 0).unwrap_or(buf.len());
    String::from_utf16_lossy(buf.get(..end).unwrap_or_default())
        .trim()
        .to_owned()
}

/// `GetDriveTypeW` for a root such as `C:\`.
#[cfg(windows)]
#[allow(unsafe_code)]
pub fn lookup_drive_type(root: &str) -> u32 {
    let w = wide(root);
    // SAFETY: `w` is a NUL-terminated UTF-16 string that outlives the call.
    unsafe { windows_sys::Win32::Storage::FileSystem::GetDriveTypeW(w.as_ptr()) }
}

/// Off Windows no drive is known.
#[cfg(not(windows))]
pub fn lookup_drive_type(_root: &str) -> u32 {
    crate::paths::DRIVE_UNKNOWN
}

/// The volume label of a mounted local drive root such as `C:\`; empty when it has none.
#[cfg(windows)]
#[allow(unsafe_code)]
pub fn lookup_volume(root: &str) -> String {
    let w = wide(root);
    let mut buf = [0u16; 261];
    // SAFETY: `w` is NUL-terminated; `buf` holds the 261 characters the call is told about;
    // every other out pointer is null, which the call allows.
    let ok = unsafe {
        windows_sys::Win32::Storage::FileSystem::GetVolumeInformationW(
            w.as_ptr(),
            buf.as_mut_ptr(),
            buf.len() as u32,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            0,
        )
    };
    if ok == 0 {
        String::new()
    } else {
        from_wide(&buf)
    }
}

/// Off Windows there are no volume labels.
#[cfg(not(windows))]
pub fn lookup_volume(_root: &str) -> String {
    String::new()
}

/// Maps `Z:` to `\\server\share`. This does not touch the server.
#[cfg(windows)]
#[allow(unsafe_code)]
pub fn lookup_unc(local_name: &str) -> String {
    use windows_sys::Win32::NetworkManagement::WNet::WNetGetConnectionW;
    const ERROR_MORE_DATA: u32 = 234;
    let name = wide(local_name);
    let mut size: u32 = 260;
    for _ in 0..2 {
        let mut buf = vec![0u16; size as usize];
        // SAFETY: `name` is NUL-terminated; `buf` has `size` characters and `size` is the
        // length passed in, which the call updates (to the needed length on ERROR_MORE_DATA).
        let rc = unsafe { WNetGetConnectionW(name.as_ptr(), buf.as_mut_ptr(), &mut size) };
        if rc == 0 {
            return from_wide(&buf);
        }
        if rc != ERROR_MORE_DATA {
            break;
        }
    }
    String::new()
}

/// Off Windows there are no mapped drives.
#[cfg(not(windows))]
pub fn lookup_unc(_local_name: &str) -> String {
    String::new()
}

/// `(type, volume, unc)` for a drive using the given lookups and cache. The type is read on
/// every call; only a mounted drive is cached, and the cache is used only while the type
/// still matches. Volume names are not queried for remote, missing or unknown drives.
pub(crate) fn facts_via(
    key: &str,
    cache: &Mutex<Option<HashMap<String, DriveFacts>>>,
    drive_type: &dyn Fn(&str) -> u32,
    volume: &dyn Fn(&str) -> String,
    unc: &dyn Fn(&str) -> String,
) -> DriveFacts {
    let kind = drive_type(key);
    if kind < DRIVE_REMOVABLE {
        return DriveFacts {
            kind,
            ..DriveFacts::default()
        };
    }
    let mut guard = cache.lock().unwrap_or_else(PoisonError::into_inner);
    let map = guard.get_or_insert_with(HashMap::new);
    if let Some(hit) = map.get(key).filter(|f| f.kind == kind) {
        return hit.clone();
    }
    let facts = if kind == DRIVE_REMOTE {
        DriveFacts {
            kind,
            volume: String::new(),
            unc: unc(key.get(..2).unwrap_or(key)),
        }
    } else {
        DriveFacts {
            kind,
            volume: volume(key),
            unc: String::new(),
        }
    };
    map.insert(key.to_owned(), facts.clone());
    facts
}

/// `(drive_type, volume_label, unc_target)` for the drive a path is on. Not Windows, or not a
/// drive path: unknown with no names.
pub fn drive_facts(path: &str) -> DriveFacts {
    if !cfg!(windows) {
        return DriveFacts::default();
    }
    let key = drive_key(path);
    if key.is_empty() {
        return DriveFacts::default();
    }
    facts_via(
        &key,
        &CACHE,
        &lookup_drive_type,
        &lookup_volume,
        &lookup_unc,
    )
}

/// The volume name of a mounted local drive. UNC paths, network drives and missing drives are
/// not probed; the result is empty for them.
pub fn volume_label(path: &str) -> String {
    volume_label_with(path, &drive_facts)
}

/// [`volume_label`] with the drive facts supplied.
pub fn volume_label_with(path: &str, facts: &dyn Fn(&str) -> DriveFacts) -> String {
    let raw = path.trim();
    if raw.is_empty() || raw.starts_with(r"\\") || raw.starts_with("//") {
        return String::new();
    }
    let key = drive_key(raw);
    if key.is_empty() {
        return String::new();
    }
    let f = facts(&key);
    if f.kind < DRIVE_REMOVABLE || f.kind == DRIVE_REMOTE {
        return String::new();
    }
    f.volume
}

/// Drive roots Windows reports (`C:\`, `D:\`…), without touching them. If the call fails, all
/// 26 letters, to be filtered by [`iter_mounted_drives`].
#[cfg(windows)]
#[allow(unsafe_code)]
pub fn iter_drive_roots() -> Vec<String> {
    use windows_sys::Win32::Storage::FileSystem::GetLogicalDriveStringsW;
    let mut size: u32 = 256;
    for _ in 0..3 {
        let mut buf = vec![0u16; size as usize];
        // SAFETY: `buf` has `size` UTF-16 units, the length passed to the call.
        let copied = unsafe { GetLogicalDriveStringsW(size, buf.as_mut_ptr()) };
        if copied == 0 {
            break;
        }
        if copied > size {
            size = copied + 2;
            continue;
        }
        let roots: Vec<String> = buf
            .get(..copied as usize)
            .unwrap_or_default()
            .split(|c| *c == 0)
            .filter(|part| !part.is_empty())
            .map(String::from_utf16_lossy)
            .collect();
        if !roots.is_empty() {
            return roots;
        }
        break;
    }
    all_letters()
}

/// Off Windows there are no drive letters.
#[cfg(not(windows))]
pub fn iter_drive_roots() -> Vec<String> {
    Vec::new()
}

#[cfg(windows)]
fn all_letters() -> Vec<String> {
    ('A'..='Z').map(|l| format!("{l}:\\")).collect()
}

/// A mounted drive.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct MountedDrive {
    /// `C:\`.
    pub root: String,
    /// The facts of the drive.
    pub facts: DriveFacts,
}

/// Mounted drives, missing letters omitted. `None` for `roots` asks Windows.
pub fn iter_mounted_drives(roots: Option<&[String]>) -> Vec<MountedDrive> {
    let own;
    let roots = match roots {
        Some(r) => r,
        None => {
            own = iter_drive_roots();
            &own
        }
    };
    iter_mounted_drives_with(roots, &drive_facts)
}

/// [`iter_mounted_drives`] with the drive facts supplied.
pub fn iter_mounted_drives_with(
    roots: &[String],
    facts: &dyn Fn(&str) -> DriveFacts,
) -> Vec<MountedDrive> {
    roots
        .iter()
        .filter_map(|root| {
            let mut key = drive_key(root);
            if key.is_empty() {
                key = canonical_stored_path(root);
            }
            let f = facts(&key);
            (f.kind >= DRIVE_REMOVABLE).then_some(MountedDrive {
                root: key,
                facts: f,
            })
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::paths::{
        DRIVE_FIXED, DRIVE_NO_ROOT, DRIVE_UNKNOWN, LabelHints, default_share_label,
        default_share_label_with,
    };
    use std::cell::Cell;

    #[cfg(windows)]
    #[test]
    fn the_system_drive_has_facts_and_names_like_explorer() {
        let drive = std::env::var("SystemDrive").unwrap_or_else(|_| "C:".into());
        let root = format!("{drive}\\");
        let f = drive_facts(&root);
        assert_eq!(f.kind, DRIVE_FIXED);
        assert_eq!(volume_label(&format!("{root}Windows")), f.volume);
        let want = if f.volume.is_empty() {
            format!("Local Disk ({drive})")
        } else {
            format!("{} ({drive})", f.volume)
        };
        assert_eq!(default_share_label(&root, ""), want);
        assert!(iter_drive_roots().contains(&root));
        assert!(
            iter_mounted_drives(None)
                .iter()
                .any(|d| d.root == root.to_uppercase())
        );
    }

    #[test]
    fn a_missing_drive_is_never_probed_for_a_volume() {
        let cache = Mutex::new(None);
        let f = facts_via(
            "Z:\\",
            &cache,
            &|_| DRIVE_NO_ROOT,
            &|root| unreachable!("volume queried for {root}"),
            &|root| unreachable!("unc queried for {root}"),
        );
        assert_eq!(f.kind, DRIVE_NO_ROOT);
        assert!(f.volume.is_empty());
        let label = default_share_label_with("Z:", "", &LabelHints::default(), &|_| f.clone());
        assert_eq!(label, "Local Disk (Z:)");
        assert!(
            cache
                .lock()
                .is_ok_and(|c| c.as_ref().is_none_or(HashMap::is_empty))
        );
    }

    #[test]
    fn only_mounted_drives_are_cached_and_the_type_is_read_each_call() {
        let cache = Mutex::new(None);
        let kind = Cell::new(DRIVE_NO_ROOT);
        let reads = Cell::new(0);
        let lookup = |root: &str| {
            reads.set(reads.get() + 1);
            if root == "E:\\" {
                "Stick".to_owned()
            } else {
                String::new()
            }
        };
        let get = || facts_via("E:\\", &cache, &|_| kind.get(), &lookup, &|_| String::new());
        assert_eq!(get().kind, DRIVE_NO_ROOT);
        assert_eq!(reads.get(), 0);
        kind.set(DRIVE_REMOVABLE);
        assert_eq!(get().volume, "Stick");
        assert_eq!(get().volume, "Stick");
        assert_eq!(reads.get(), 1);
        // The drive becomes a different kind: the cached entry no longer matches.
        kind.set(DRIVE_FIXED);
        assert_eq!(get().kind, DRIVE_FIXED);
        assert_eq!(reads.get(), 2);
        kind.set(DRIVE_NO_ROOT);
        assert_eq!(get().kind, DRIVE_NO_ROOT);
    }

    #[test]
    fn a_network_drive_asks_for_its_target_not_a_volume() {
        let cache = Mutex::new(None);
        let f = facts_via(
            "Z:\\",
            &cache,
            &|_| DRIVE_REMOTE,
            &|root| unreachable!("volume queried for {root}"),
            &|name| {
                assert_eq!(name, "Z:");
                r"\\nas\games".to_owned()
            },
        );
        assert_eq!(f.unc, r"\\nas\games");
        assert_eq!(volume_label_with("Z:\\", &|_| f.clone()), "");
    }

    #[test]
    fn mounted_drives_skip_missing_letters() {
        let facts = |path: &str| {
            if path.to_uppercase().starts_with('C') {
                DriveFacts {
                    kind: DRIVE_FIXED,
                    volume: "Windows".into(),
                    unc: String::new(),
                }
            } else {
                DriveFacts {
                    kind: DRIVE_NO_ROOT,
                    ..DriveFacts::default()
                }
            }
        };
        let mounted = iter_mounted_drives_with(&["C:\\".into(), "Z:\\".into()], &facts);
        assert_eq!(mounted.len(), 1);
        let m = mounted.first().cloned().unwrap_or_else(|| unreachable!());
        assert_eq!(m.root, "C:\\");
        let hints = LabelHints {
            volume: Some(m.facts.volume.clone()),
            drive_type: Some(m.facts.kind),
            unc_target: Some(m.facts.unc.clone()),
        };
        assert_eq!(
            default_share_label_with(&m.root, "", &hints, &|_| unreachable!("facts not needed")),
            "Windows (C:)"
        );
    }

    #[test]
    fn off_windows_nothing_is_known() {
        if !cfg!(windows) {
            assert_eq!(drive_facts("C:\\").kind, DRIVE_UNKNOWN);
            assert!(iter_drive_roots().is_empty());
        }
    }
}
