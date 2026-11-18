//! A drive's volume label ("Windows", "Media"), as 0.3.x reads it for share names.

use std::path::Path;

/// The volume label of the drive or network share `path` is on; `None` when it has none
/// (and on systems without volume labels).
#[cfg(windows)]
#[allow(unsafe_code)]
pub fn label(path: &Path) -> Option<String> {
    use std::os::windows::ffi::OsStrExt;
    use std::path::Component;
    let root: std::path::PathBuf = path
        .components()
        .take_while(|c| matches!(c, Component::Prefix(_) | Component::RootDir))
        .collect();
    let mut root = root.to_string_lossy().replace('/', "\\");
    if root.is_empty() {
        return None;
    }
    if !root.ends_with('\\') {
        root.push('\\');
    }
    let wide: Vec<u16> = std::ffi::OsStr::new(&root)
        .encode_wide()
        .chain(std::iter::once(0))
        .collect();
    let mut buf = [0u16; 261];
    // SAFETY: `wide` is NUL-terminated; `buf` holds the 261 characters the call is told about;
    // every other out pointer is null, which the call allows.
    let ok = unsafe {
        windows_sys::Win32::Storage::FileSystem::GetVolumeInformationW(
            wide.as_ptr(),
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
        return None;
    }
    let end = buf.iter().position(|c| *c == 0).unwrap_or(buf.len());
    let s = String::from_utf16_lossy(buf.get(..end).unwrap_or_default())
        .trim()
        .to_owned();
    (!s.is_empty()).then_some(s)
}

/// Volume labels are a Windows idea.
#[cfg(not(windows))]
pub fn label(_path: &Path) -> Option<String> {
    None
}

#[cfg(all(test, windows))]
mod tests {
    #[test]
    fn a_folder_reads_its_drive_label_and_names_a_root_share_like_0_3() {
        let drive = std::env::var("SystemDrive").unwrap_or_else(|_| "C:".into());
        let root = super::label(std::path::Path::new(&format!("{drive}\\")));
        assert_eq!(
            super::label(std::path::Path::new(&format!("{drive}\\Windows"))),
            root
        );
        let want = match &root {
            Some(v) => format!("{v} ({drive})"),
            None => drive.clone(),
        };
        assert_eq!(
            crate::paths::share_label(&format!("{drive}\\"), "", root.as_deref()),
            want
        );
    }
}
