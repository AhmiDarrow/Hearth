//! Hearth on RemedyUI: share folders and chat with the other PCs in the house, no cloud.
//!
//! The engine (`hearth-core`) speaks Hearth 0.3.x's protocol, so this version and the Python
//! one share a home group; this crate is the interface: first run, the PCs and their shares,
//! files, transfers, chat, settings, the tray, updates and starting with Windows.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod chat;
mod files;
mod gate;
mod i18n;
mod log;
mod look;
mod settings;
mod share_dialog;
mod shell;
mod state;
mod transfers;
mod tray;
mod update;

#[cfg(test)]
mod flow_tests;

use std::path::PathBuf;

/// How Hearth was started.
#[derive(Clone, Debug, Default)]
pub struct Launch {
    /// The TCP port to listen on.
    pub port: u16,
    /// The profile folder.
    pub home: PathBuf,
    /// Tray only: no window until asked for.
    pub background: bool,
    /// Started by the installer after an update (the version, when given).
    pub updated: Option<String>,
    /// Anything else (an invite to join).
    pub open: Vec<String>,
    /// `--quit`: the Hearth already running on this profile quits (and this one starts nothing).
    pub quit: bool,
    /// `--install-update`: look for an update now and install it without asking again, in
    /// the Hearth already running on this profile or in this one.
    pub install_update: bool,
}

/// Reads the command line (the same flags Hearth 0.3.x takes).
pub fn launch(args: impl IntoIterator<Item = String>) -> Launch {
    let mut l = Launch {
        port: std::env::var("HEARTH_PORT")
            .ok()
            .and_then(|p| p.parse().ok())
            .unwrap_or(hearth_core::net::PEER_PORT),
        home: hearth_core::state::default_root(),
        ..Launch::default()
    };
    let mut it = args.into_iter().peekable();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--port" => {
                if let Some(p) = it.next().and_then(|p| p.parse().ok()) {
                    l.port = p;
                }
            }
            "--home" => {
                if let Some(h) = it.next() {
                    l.home = PathBuf::from(h);
                }
            }
            "--background" => l.background = true,
            "--quit" => l.quit = true,
            "--install-update" => l.install_update = true,
            "--updated" => {
                let v = it.next_if(|n| !n.starts_with("--"));
                l.updated = Some(v.unwrap_or_default());
            }
            // Flags of the Python app that mean nothing here.
            "--desktop" | "--no-browser" => {}
            "--host" => {
                let _ = it.next();
            }
            s if s.starts_with("--") => {}
            _ => l.open.push(a),
        }
    }
    l
}

/// Whether this run is the hidden CI (`HEARTH_CI=1`), which never remembers window or tray.
pub fn ci() -> bool {
    std::env::var("HEARTH_CI").is_ok_and(|v| v == "1")
}

fn main() {
    let mut l = launch(std::env::args().skip(1));
    // Restarted by the installer: the window comes back when it was open (0.4's rule).
    if l.updated.is_some() && !ci() {
        l.background = hearth_core::relaunch::background_after_update(
            hearth_core::relaunch::read_ui_mode(&l.home),
            l.background,
        );
    }
    // The release checks run hidden: no window, whatever started Hearth.
    if ci() {
        l.background = true;
    }
    // One Hearth per profile.
    let name = if l.home == hearth_core::state::default_root() {
        "Hearth".to_owned()
    } else {
        format!("Hearth ({})", l.home.display())
    };
    // The window's place and the interface's own choices (grid or list) live in the
    // profile, beside what the Python app keeps there.
    let config = remedy_ui::AppConfig::new(name)
        .single_instance()
        .data_dir(l.home.join("ui"))
        .theme(shell::theme_for("system", None, false), look::sheet);
    let home = l.home.clone();
    if let Err(e) = remedy_ui::run(config, move |app| {
        let _ = shell::start(app, &l);
    }) {
        log::line(&home, &format!("Hearth could not run: {e:?}"));
        eprintln!("Hearth: {e:?}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn the_command_line_reads_like_0_3() {
        let l = super::launch(
            [
                "--port",
                "7480",
                "--home",
                "C:/p",
                "--background",
                "--updated",
                "0.4.0",
                "hearth|a|b|c|Home",
            ]
            .map(String::from),
        );
        assert_eq!(
            (l.port, l.background, l.updated.as_deref()),
            (7480, true, Some("0.4.0"))
        );
        assert_eq!(l.home, std::path::PathBuf::from("C:/p"));
        assert_eq!(l.open, vec!["hearth|a|b|c|Home"]);
        let l = super::launch(["--updated", "--background"].map(String::from));
        assert_eq!(l.updated.as_deref(), Some(""));
        assert!(l.background);
        assert!(!l.quit && !l.install_update);
        let l = super::launch(["--quit", "--install-update"].map(String::from));
        assert!(l.quit && l.install_update && l.open.is_empty());
    }
}
