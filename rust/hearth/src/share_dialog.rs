//! The folder dialog, in three modes: share a folder, edit a share, and choose the download
//! folder. A folder comes from the system's folder picker, a typed path, or one of the
//! user's libraries.

use std::cell::RefCell;
use std::path::PathBuf;
use std::rc::Rc;

use remedy_ui::host::FileDialog;
use remedy_ui::reactive::Signal;
use remedy_ui::view::Align;
use remedy_ui::{El, TextField, Ui};
use remedy_ui_services_desktop::{KnownFolder, known_folder};
use remedy_ui_widgets_core::{self as w, Modal};

use crate::state::{Cursor, H};

/// What the dialog is for.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Mode {
    /// Share a new folder.
    Share,
    /// Change the share with this id.
    Edit(String),
    /// Choose where downloads go.
    Download,
}

struct Text {
    title: &'static str,
    hint: &'static str,
    ok: &'static str,
}

fn text(mode: &Mode) -> Text {
    match mode {
        Mode::Share => Text {
            title: "Share a folder",
            hint: "Browse for a folder on this PC, type a path, or pick a library below.",
            ok: "Share this folder",
        },
        Mode::Edit(_) => Text {
            title: "Edit share",
            hint: "Rename this share, browse to a different folder, or change permissions. Changes apply as soon as you save.",
            ok: "Save changes",
        },
        Mode::Download => Text {
            title: "Download folder",
            hint: "Browse for a folder, or pick a library below.",
            ok: "Use this folder",
        },
    }
}

/// The libraries this PC has.
pub fn libraries() -> Vec<(&'static str, PathBuf)> {
    KnownFolder::LIBRARIES
        .iter()
        .filter_map(|k| known_folder(*k).map(|p| (k.name(), p)))
        .collect()
}

fn pick_folder(h: &H, into: TextField) {
    let win = h.window.borrow().clone();
    let start = PathBuf::from(into.value().trim());
    h.app.file_dialog(
        win.as_ref(),
        FileDialog {
            title: "Choose a folder".into(),
            folders: true,
            directory: start.is_dir().then_some(start),
            ..FileDialog::default()
        },
        move |paths| {
            if let Some(p) = paths.into_iter().next() {
                into.set_value(&p.to_string_lossy());
            }
        },
    );
}

/// Opens the dialog.
pub fn open(h: &H, ui: &Ui, mode: Mode) {
    let _ = build(h, ui, mode);
}

/// The Share dialog with the system's folder picker already open.
pub fn browse_and_share(h: &H, ui: &Ui) {
    let (_, folder) = build(h, ui, Mode::Share);
    pick_folder(h, folder);
}

fn build(h: &H, ui: &Ui, mode: Mode) -> (Modal, TextField) {
    let unit = ui.theme().spacing.unit;
    let t = text(&mode);
    let existing = match &mode {
        Mode::Edit(id) => h.core.shares().into_iter().find(|s| s.id == *id),
        _ => None,
    };
    let name = w::text_input(ui, "Name shown to the house");
    let folder = w::text_input(ui, "Folder").placeholder("Type a folder path, or click Browse…");
    let writable = Signal::new(false);
    match (&mode, &existing) {
        (Mode::Edit(_), Some(s)) => {
            name.set_value(&s.label);
            folder.set_value(&s.path.to_string_lossy());
            writable.set(s.writable);
        }
        (Mode::Download, _) => folder.set_value(&h.core.settings().download_folder),
        _ => {}
    }
    let error: Signal<Option<String>> = Signal::new(None);
    let slot: Rc<RefCell<Option<Modal>>> = Rc::default();
    let close = {
        let slot = slot.clone();
        move || {
            if let Some(m) = slot.borrow().as_ref() {
                m.close();
            }
        }
    };

    let ok = {
        let (h, mode, name, folder, close) = (
            h.clone(),
            mode.clone(),
            name.clone(),
            folder.clone(),
            close.clone(),
        );
        Rc::new(move || {
            let raw = folder.value().trim().to_owned();
            if raw.is_empty() {
                error.set(Some("Choose a folder first".into()));
                return;
            }
            let path = PathBuf::from(&raw);
            if !path.is_dir() {
                error.set(Some("That folder does not exist on this PC".into()));
                return;
            }
            let label = name.value().trim().to_owned();
            let rw = writable.get_untracked().unwrap_or(false);
            let r = match &mode {
                Mode::Download => h
                    .core
                    .set_download_folder(&raw)
                    .map(|()| format!("Downloads will go to {raw}")),
                Mode::Edit(id) => h
                    .core
                    .update_share(id, Some(&path), Some(&label), Some(rw))
                    .map(|()| "Share updated".to_owned()),
                Mode::Share => h.core.add_share(&path, &label, rw).map(|s| {
                    h.chat_tab.set(false);
                    h.open(Cursor {
                        device: h.core.device_id(),
                        share: s.id.clone(),
                        path: String::new(),
                    });
                    format!("Shared {} with the house", s.display_label())
                }),
            };
            match r {
                Ok(msg) => {
                    close();
                    h.toast(msg);
                }
                Err(e) => error.set(Some(e)),
            }
        })
    };

    let (h2, f2) = (h.clone(), folder.clone());
    let browse = w::button(ui, "Browse…", move || pick_folder(&h2, f2.clone()));
    let mut body = w::column(ui)
        .gap(unit * 3.0)
        .fill_width()
        .width(480.0)
        .child(w::label(ui, t.hint, "muted"));
    if mode != Mode::Download {
        body = body.child(w::field(
            ui,
            "Name shown to the house",
            name.el().fill_width(),
        ));
    }
    body = body.child(w::field(
        ui,
        "Folder",
        w::row(ui)
            .gap(unit * 2.0)
            .fill_width()
            .align(Align::Center)
            .child(folder.el().grow(1.0))
            .child(browse),
    ));
    // The libraries.
    let libs = libraries();
    let mut picks = w::wrap(ui).gap(unit * 2.0);
    if libs.is_empty() {
        picks = picks.child(w::label(
            ui,
            "No usual libraries found. Browse or type a path.",
            "muted",
        ));
    }
    for (label, path) in libs {
        let f = folder.clone();
        picks = picks.child(
            w::button(ui, label, move || f.set_value(&path.to_string_lossy())).variant("quiet"),
        );
    }
    body = body.child(picks);
    if mode != Mode::Download {
        body = body.child(w::check(ui, "Allow others to add files", writable));
    }
    body = body.child(crate::gate::error_text(ui, error));
    // Buttons.
    let o2 = ok.clone();
    let mut buttons = w::row(ui).gap(unit * 2.0).fill_width();
    if let Mode::Edit(id) = &mode {
        let (h3, u3, id, c2) = (h.clone(), ui.clone(), id.clone(), close.clone());
        let label = existing
            .as_ref()
            .map(hearth_core::state::Share::display_label)
            .unwrap_or_default();
        buttons = buttons.child(
            w::button(ui, "Remove share", move || {
                c2();
                crate::files::confirm_unshare(&h3, &u3, &id, &label);
            })
            .variant("danger"),
        );
    }
    let c3 = close.clone();
    buttons = buttons
        .child(w::spacer(ui))
        .child(w::button(ui, "Cancel", c3).variant("quiet"))
        .child(w::button(ui, t.ok, move || o2()).variant("primary"));
    let o3 = ok.clone();
    let _ = folder.clone().on_submit(move || o3());
    let content: El = body.child(buttons);
    let m = w::modal(ui, t.title, content, true);
    *slot.borrow_mut() = Some(m.clone());
    (m, folder)
}

#[cfg(test)]
mod tests {
    #[test]
    fn each_mode_says_what_it_does() {
        use super::{Mode, text};
        assert_eq!(text(&Mode::Share).ok, "Share this folder");
        assert_eq!(text(&Mode::Edit("x".into())).title, "Edit share");
        assert_eq!(text(&Mode::Download).ok, "Use this folder");
    }
}
