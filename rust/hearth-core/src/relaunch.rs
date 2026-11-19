//! What the installer needs to bring Hearth back after an update, in the files Hearth 0.4
//! writes, so an installer made for either version restarts the other the same way:
//!
//! - `relaunch_ui.json` in the profile: `{"ui": "window"}` or `{"ui": "tray"}`, whether the
//!   window was open, so an update reopens it as it was.
//! - `relaunch-args.json` and `relaunch-cmdline.txt` next to an installed `Hearth.exe`: this
//!   profile's flags (`--home`, `--port`, …), which Setup passes to the restarted copy. The
//!   command line is written in the system's ANSI code page, which Inno's
//!   `LoadStringFromFile` reads.

use std::path::{Path, PathBuf};

use serde_json::{Value, json};

/// The profile file saying whether the window or the tray comes back.
pub const RELAUNCH_UI_NAME: &str = "relaunch_ui.json";
/// The install-folder file with each profile's launch flags.
pub const RELAUNCH_ARGS_NAME: &str = "relaunch-args.json";
/// The install-folder file with this profile's command line, for Setup.
pub const RELAUNCH_CMDLINE_NAME: &str = "relaunch-cmdline.txt";

/// Window or tray.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum UiMode {
    /// The window was open.
    Window,
    /// Only the tray icon.
    Tray,
}

impl UiMode {
    fn as_str(self) -> &'static str {
        match self {
            UiMode::Window => "window",
            UiMode::Tray => "tray",
        }
    }
}

/// Remembers whether the window is open (written as 0.4 writes it).
pub fn write_ui_mode(root: &Path, mode: UiMode) -> std::io::Result<()> {
    std::fs::create_dir_all(root)?;
    std::fs::write(
        root.join(RELAUNCH_UI_NAME),
        // `json.dumps({"ui": mode})`, as 0.4 writes it.
        format!(r#"{{"ui": "{}"}}"#, mode.as_str()),
    )
}

/// What [`write_ui_mode`] (or Hearth 0.4) last remembered.
pub fn read_ui_mode(root: &Path) -> Option<UiMode> {
    let text = std::fs::read_to_string(root.join(RELAUNCH_UI_NAME)).ok()?;
    let v: Value = serde_json::from_str(&text).ok()?;
    match v.get("ui")?.as_str()?.trim().to_lowercase().as_str() {
        "window" => Some(UiMode::Window),
        "tray" => Some(UiMode::Tray),
        _ => None,
    }
}

/// Whether Hearth starts in the tray after the installer restarted it (`--updated`), as 0.4's
/// `apply_saved_ui_mode` decides: the window comes back when it was open, the tray when it
/// was not; with nothing remembered, `--background` decides.
pub fn background_after_update(saved: Option<UiMode>, background_flag: bool) -> bool {
    match saved {
        Some(UiMode::Window) => false,
        Some(UiMode::Tray) => true,
        None => background_flag,
    }
}

/// The folder Hearth is installed in: `HEARTH_UPDATE_DIR`, else this program's folder when
/// Setup installed it there (its uninstaller sits beside it). `None` for a build that was
/// not installed, which has no Setup to restart it.
pub fn install_dir() -> Option<PathBuf> {
    if let Some(d) = std::env::var_os("HEARTH_UPDATE_DIR").filter(|d| !d.is_empty()) {
        return Some(PathBuf::from(d));
    }
    let dir = std::env::current_exe().ok()?.parent()?.to_path_buf();
    dir.join("unins000.exe").is_file().then_some(dir)
}

/// Where a silent update installs (`/DIR=`): the install folder, unless it is under Program
/// Files or this user cannot write there (then Setup's own default).
pub fn update_dir() -> Option<PathBuf> {
    if let Some(d) = std::env::var_os("HEARTH_UPDATE_DIR").filter(|d| !d.is_empty()) {
        return Some(PathBuf::from(d));
    }
    let dir = install_dir()?;
    let program_files = dir.components().any(|c| {
        let s = c.as_os_str().to_string_lossy().to_lowercase();
        s == "program files" || s == "program files (x86)"
    });
    let probe = dir.join(format!(".hearth-w-{}", crate::crypto::random_hex(4)));
    let writable = std::fs::write(&probe, b"").is_ok() && std::fs::remove_file(&probe).is_ok();
    (!program_files && writable).then_some(dir)
}

/// `argv` without `--updated [version]`.
pub fn drop_updated_args(argv: &[String]) -> Vec<String> {
    let mut out = Vec::new();
    let mut i = 0;
    while let Some(a) = argv.get(i) {
        if a == "--updated" {
            let skip_value = argv.get(i + 1).is_some_and(|n| !n.starts_with('-'));
            i += if skip_value { 2 } else { 1 };
            continue;
        }
        if !a.starts_with("--updated=") {
            out.push(a.clone());
        }
        i += 1;
    }
    out
}

fn home_from_argv(argv: &[String]) -> Option<String> {
    let mut it = argv.iter();
    while let Some(a) = it.next() {
        if a == "--home" {
            return it.next().cloned();
        }
        if let Some(h) = a.strip_prefix("--home=") {
            return Some(h.to_owned());
        }
    }
    None
}

/// One Windows command-line argument, quoted when it needs to be.
pub fn quote_windows_arg(arg: &str) -> String {
    if !arg.is_empty() && !arg.contains([' ', '\t', '"']) {
        return arg.to_owned();
    }
    let mut escaped = arg.replace('"', "\\\"");
    // A trailing backslash would escape the closing quote.
    if escaped.ends_with('\\') {
        escaped.push('\\');
    }
    format!("\"{escaped}\"")
}

/// The command line Setup passes, without `--updated`.
pub fn format_cmdline(argv: &[String]) -> String {
    drop_updated_args(argv)
        .iter()
        .map(|a| quote_windows_arg(a))
        .collect::<Vec<_>>()
        .join(" ")
}

/// The flags that bring this profile back: `--home`, `--port`, `--host`, and `--background`
/// for a tray start (as 0.4's `argv_for_current_launch`).
pub fn argv_for_launch(home: &Path, port: u16, background: bool) -> Vec<String> {
    let mut argv = vec![
        "--home".to_owned(),
        home.to_string_lossy().into_owned(),
        "--port".to_owned(),
        port.to_string(),
        "--host".to_owned(),
        "0.0.0.0".to_owned(),
    ];
    if background {
        argv.push("--background".into());
    }
    argv
}

fn same_path(a: &str, b: &str) -> bool {
    let norm = |s: &str| {
        let s = s.replace('/', "\\");
        let s = s.trim_end_matches('\\').to_owned();
        if cfg!(windows) { s.to_lowercase() } else { s }
    };
    norm(a) == norm(b)
}

/// Records this profile's flags next to Hearth.exe for Setup, keeping other profiles already
/// recorded, and writes this profile's command line.
pub fn write_relaunch_args(dir: &Path, argv: &[String], home: &Path) -> std::io::Result<PathBuf> {
    std::fs::create_dir_all(dir)?;
    let path = dir.join(RELAUNCH_ARGS_NAME);
    let cleaned = drop_updated_args(argv);
    let me = home.to_string_lossy().into_owned();
    let mut instances = vec![json!({"home": me, "argv": cleaned})];
    if let Some(prev) = std::fs::read_to_string(&path)
        .ok()
        .and_then(|t| serde_json::from_str::<Value>(&t).ok())
    {
        for inst in prev
            .get("instances")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
        {
            let other: Vec<String> = inst
                .get("argv")
                .and_then(Value::as_array)
                .map(|a| {
                    a.iter()
                        .filter_map(|v| v.as_str().map(str::to_owned))
                        .collect()
                })
                .unwrap_or_default();
            let other_home = inst
                .get("home")
                .and_then(Value::as_str)
                .filter(|h| !h.is_empty())
                .map(str::to_owned)
                .or_else(|| home_from_argv(&other))
                .unwrap_or_default();
            if !other_home.is_empty() && same_path(&other_home, &me) {
                continue;
            }
            if !other.is_empty() {
                instances.push(json!({"home": other_home, "argv": other}));
            }
        }
    }
    std::fs::write(
        &path,
        crate::pyjson::dumps_indent(&json!({"version": 1, "instances": instances})),
    )?;
    std::fs::write(
        dir.join(RELAUNCH_CMDLINE_NAME),
        ansi(&format_cmdline(&cleaned)),
    )?;
    Ok(path)
}

/// `text` in the system's ANSI code page (UTF-8 elsewhere).
#[cfg(windows)]
#[allow(unsafe_code)]
fn ansi(text: &str) -> Vec<u8> {
    use windows_sys::Win32::Globalization::{CP_ACP, WideCharToMultiByte};
    let wide: Vec<u16> = text.encode_utf16().collect();
    if wide.is_empty() {
        return Vec::new();
    }
    let Ok(n) = i32::try_from(wide.len()) else {
        return text.as_bytes().to_vec();
    };
    // SAFETY: `wide` holds `n` UTF-16 units; the first call only measures, the second writes
    // at most `need` bytes into a buffer of that length.
    let need = unsafe {
        WideCharToMultiByte(
            CP_ACP,
            0,
            wide.as_ptr(),
            n,
            std::ptr::null_mut(),
            0,
            std::ptr::null(),
            std::ptr::null_mut(),
        )
    };
    let Ok(len) = usize::try_from(need) else {
        return text.as_bytes().to_vec();
    };
    if len == 0 {
        return text.as_bytes().to_vec();
    }
    let mut out = vec![0u8; len];
    // SAFETY: as above; `out` is `need` bytes long.
    let wrote = unsafe {
        WideCharToMultiByte(
            CP_ACP,
            0,
            wide.as_ptr(),
            n,
            out.as_mut_ptr(),
            need,
            std::ptr::null(),
            std::ptr::null_mut(),
        )
    };
    out.truncate(usize::try_from(wrote).unwrap_or(0));
    out
}

#[cfg(not(windows))]
fn ansi(text: &str) -> Vec<u8> {
    text.as_bytes().to_vec()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp() -> tempfile::TempDir {
        tempfile::tempdir().unwrap_or_else(|e| unreachable!("{e}"))
    }

    fn s(v: &[&str]) -> Vec<String> {
        v.iter().map(|x| (*x).to_owned()).collect()
    }

    #[test]
    fn the_ui_mode_round_trips_in_0_4s_format() {
        let d = tmp();
        assert_eq!(read_ui_mode(d.path()), None);
        write_ui_mode(d.path(), UiMode::Tray).unwrap_or_else(|e| unreachable!("{e}"));
        assert_eq!(
            std::fs::read_to_string(d.path().join(RELAUNCH_UI_NAME)).unwrap_or_default(),
            r#"{"ui": "tray"}"#
        );
        assert_eq!(read_ui_mode(d.path()), Some(UiMode::Tray));
        std::fs::write(d.path().join(RELAUNCH_UI_NAME), r#"{"ui": " Window "}"#)
            .unwrap_or_else(|e| unreachable!("{e}"));
        assert_eq!(read_ui_mode(d.path()), Some(UiMode::Window));
        assert!(!background_after_update(Some(UiMode::Window), true));
        assert!(background_after_update(Some(UiMode::Tray), false));
        assert!(background_after_update(None, true));
    }

    #[test]
    fn command_lines_quote_like_0_4_and_drop_updated() {
        assert_eq!(
            drop_updated_args(&s(&["--updated", "0.4.1", "--home", "x", "--updated"])),
            s(&["--home", "x"])
        );
        assert_eq!(
            drop_updated_args(&s(&["--updated", "--background"])),
            s(&["--background"])
        );
        assert_eq!(quote_windows_arg("plain"), "plain");
        assert_eq!(quote_windows_arg(""), "\"\"");
        assert_eq!(quote_windows_arg(r"C:\My Folder\"), "\"C:\\My Folder\\\\\"");
        assert_eq!(
            format_cmdline(&s(&["--home", r"C:\A B", "--port", "7474"])),
            "--home \"C:\\A B\" --port 7474"
        );
    }

    #[test]
    fn relaunch_args_keep_other_profiles_and_write_this_ones_command_line() {
        let d = tmp();
        let other = d.path().join("other");
        let mine = d.path().join("mine");
        let a = argv_for_launch(&other, 7480, true);
        write_relaunch_args(d.path(), &a, &other).unwrap_or_else(|e| unreachable!("{e}"));
        let b = argv_for_launch(&mine, 7474, false);
        write_relaunch_args(d.path(), &b, &mine).unwrap_or_else(|e| unreachable!("{e}"));
        // Writing the same profile again replaces its row.
        write_relaunch_args(d.path(), &b, &mine).unwrap_or_else(|e| unreachable!("{e}"));
        let v: Value = serde_json::from_str(
            &std::fs::read_to_string(d.path().join(RELAUNCH_ARGS_NAME)).unwrap_or_default(),
        )
        .unwrap_or_default();
        let rows = v
            .get("instances")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        assert_eq!(rows.len(), 2);
        assert_eq!(
            rows.first().and_then(|r| r.get("home")),
            Some(&json!(mine.to_string_lossy()))
        );
        assert_eq!(rows.get(1).and_then(|r| r.get("argv")), Some(&json!(a)));
        let line = std::fs::read(d.path().join(RELAUNCH_CMDLINE_NAME)).unwrap_or_default();
        assert_eq!(String::from_utf8_lossy(&line), format_cmdline(&b));
    }
}
