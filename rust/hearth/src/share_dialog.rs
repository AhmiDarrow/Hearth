//! The folder dialog, in three modes: share a folder, edit a share, and choose the download
//! folder. A folder comes from the system's folder picker, a typed path, one of the user's
//! libraries, or the folder browser under them (This PC: Home, the libraries and the drives,
//! then each folder's subfolders).

use std::cell::RefCell;
use std::path::PathBuf;
use std::rc::Rc;

use hearth_core::localdirs::{self, LocalDirs};
use remedy_ui::host::FileDialog;
use remedy_ui::layout::Length;
use remedy_ui::reactive::{Signal, untrack};
use remedy_ui::view::Align;
use remedy_ui::{El, TextField, Ui};
use remedy_ui_services_desktop::{KnownFolder, known_folder};
use remedy_ui_widgets_core::{self as w, Modal};

use crate::i18n::{place, t, tr};
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

/// What a mode says: catalog keys, shown in the window's language.
struct Text {
    title: &'static str,
    hint: &'static str,
    ok: &'static str,
}

/// What a successful dialog reports.
enum Done {
    Download(String),
    Updated,
    Shared(String),
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

/// [`libraries`] by their English names, for the engine.
fn named_libraries() -> Vec<(String, PathBuf)> {
    libraries()
        .into_iter()
        .map(|(k, p)| (k.to_owned(), p))
        .collect()
}

fn pick_folder(h: &H, ui: &Ui, into: TextField) {
    let win = h.window.borrow().clone();
    let start = PathBuf::from(into.value().trim());
    h.app.file_dialog(
        win.as_ref(),
        {
            let mut d = FileDialog::default();
            d.title = t(ui, "Choose a folder");
            d.folders = true;
            d.directory = start.is_dir().then_some(start);
            d
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
    pick_folder(h, ui, folder);
}

fn build(h: &H, ui: &Ui, mode: Mode) -> (Modal, TextField) {
    let tx = text(&mode);
    let existing = match &mode {
        Mode::Edit(id) => h.core.shares().into_iter().find(|s| s.id == *id),
        _ => None,
    };
    let name = w::text_input(ui, t(ui, "Name shown to the house"))
        .placeholder(t(ui, "Name shown to the house"));
    let folder = w::text_input(ui, t(ui, "Folder"))
        .placeholder(t(ui, "Type a folder path, or click Browse…"));
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
        let (h, mode, name, folder, close, u) = (
            h.clone(),
            mode.clone(),
            name.clone(),
            folder.clone(),
            close.clone(),
            ui.clone(),
        );
        Rc::new(move || {
            let raw = folder.value().trim().to_owned();
            if raw.is_empty() {
                error.set(Some(t(&u, "Choose a folder first")));
                return;
            }
            let Some(raw) = localdirs::resolve_user_path(&raw, &named_libraries()) else {
                error.set(Some(t(&u, "That folder does not exist on this PC")));
                return;
            };
            let path = PathBuf::from(&raw);
            let label = name.value().trim().to_owned();
            let rw = writable.get_untracked().unwrap_or(false);
            let r = match &mode {
                Mode::Download => h
                    .core
                    .set_download_folder(&raw)
                    .map(|()| Done::Download(raw.clone())),
                Mode::Edit(id) => h
                    .core
                    .update_share(id, Some(&path), Some(&label), Some(rw))
                    .map(|()| Done::Updated),
                Mode::Share => h.core.add_share(&path, &label, rw).map(|s| {
                    h.chat_tab.set(false);
                    h.open(Cursor {
                        device: h.core.device_id(),
                        share: s.id.clone(),
                        path: String::new(),
                    });
                    Done::Shared(s.display_label())
                }),
            };
            match r {
                Ok(done) => {
                    close();
                    match done {
                        Done::Download(path) => {
                            h.toast_f("Downloads will go to {path}", &[("path", path)]);
                        }
                        Done::Updated => h.toast("Share updated"),
                        Done::Shared(label) => {
                            h.toast_f("Shared {label} with the house", &[("label", label)])
                        }
                    }
                }
                Err(e) => error.set(Some(tr(&u, &e))),
            }
        })
    };

    let (h2, f2, u2) = (h.clone(), folder.clone(), ui.clone());
    let browse = w::button(ui, t(ui, "Browse…"), move || {
        pick_folder(&h2, &u2, f2.clone())
    })
    .variant("primary");
    // `.modal-body`
    let mut body = ui
        .el("modal_body")
        .column()
        .gap(12.0)
        .fill_width()
        .child(w::text(ui, t(ui, tx.hint)).variant("hint"));
    if mode != Mode::Download {
        body = body.child(w::field(
            ui,
            t(ui, "Display name"),
            name.el().fill_width(),
        ));
    }
    body = body.child(w::field(
        ui,
        t(ui, "Folder"),
        w::row(ui)
            .gap(8.0)
            .fill_width()
            .align(Align::Center)
            .child(
                folder
                    .el()
                    .grow(1.0)
                    .layout(|l| l.min_width = Length::Px(0.0)),
            )
            .child(browse),
    ));
    // `.quick-shares`: the libraries, each a card with its name over its folder.
    let libs = libraries();
    let mut picks = w::wrap(ui).gap(8.0).fill_width();
    if libs.is_empty() {
        picks = picks.child(
            w::text(
                ui,
                t(ui, "No usual libraries found. Browse or type a path."),
            )
            .variant("hint"),
        );
    }
    // What the browser lists, and which folder in it is picked.
    let listing: Signal<Option<Result<LocalDirs, String>>> = Signal::new(None);
    let picked: Signal<String> = Signal::new(String::new());
    let load = loader(h, ui, &folder, listing, picked, &libs);
    for (label, path) in libs.iter().cloned() {
        let (f, l2) = (folder.clone(), load.clone());
        let shown = path.to_string_lossy().into_owned();
        let path_shown = shown.clone();
        let b = ui
            .el("quick_share")
            .column()
            .gap(4.0)
            .grow(1.0)
            .layout(|l| {
                l.basis = Length::Px(140.0);
                l.min_width = Length::Px(0.0);
            })
            .on_click(move || {
                f.set_value(&path.to_string_lossy());
                l2(path.to_string_lossy().into_owned(), false);
            })
            .child(w::text(ui, t(ui, label)).variant("strong").truncate())
            .child(w::text(ui, shown.clone()).variant("small").truncate().data_text())
            .semantics(
                remedy_ui_a11y::Semantics::new(remedy_ui_a11y::Role::Button)
                    .label(t(ui, label))
                    .description(shown)
                    .action(remedy_ui_a11y::Action::Click),
            );
        let (b2, p3) = (b.clone(), path_shown);
        picks = picks.child(b.bind(move || {
            b2.state(remedy_ui::style::States::SELECTED, picked.get() == p3);
        }));
    }
    body = body
        .child(picks)
        .child(browser(ui, listing, picked, &folder, &load));
    body = body.child(crate::gate::error_text(ui, error));
    // `.modal-foot`: the permission, Remove share, then Cancel and the mode's own button.
    let o2 = ok.clone();
    let mut buttons = ui
        .el("modal_foot")
        .row()
        .gap(8.0)
        .align(Align::Center)
        .fill_width()
        .layout(|l| l.wrap = true);
    if mode != Mode::Download {
        buttons = buttons.child(
            w::check(ui, t(ui, "Allow others to add files"), writable)
                .grow(1.0)
                .layout(|l| l.basis = Length::Px(180.0)),
        );
    }
    if let Mode::Edit(id) = &mode {
        let (h3, u3, id, c2) = (h.clone(), ui.clone(), id.clone(), close.clone());
        let label = existing
            .as_ref()
            .map(hearth_core::state::Share::display_label)
            .unwrap_or_default();
        buttons = buttons.child(
            w::button(ui, t(ui, "Remove share"), move || {
                c2();
                crate::files::confirm_unshare(&h3, &u3, &id, &label);
            })
            .variant("danger"),
        );
    }
    let c3 = close.clone();
    // `.modal-actions`: together at the end of their line.
    buttons = buttons.child(
        w::row(ui)
            .gap(8.0)
            .grow(1.0)
            .justify(remedy_ui::view::Justify::End)
            .child(w::button(ui, t(ui, "Cancel"), c3))
            .child(w::button(ui, t(ui, tx.ok), move || o2()).variant("primary")),
    );
    let o3 = ok.clone();
    let _ = folder.clone().on_submit(move || o3());
    // The head and the foot stay; in a short window the body between them scrolls.
    let content: El = w::column(ui)
        .fill_width()
        .layout(|l| l.min_height = Length::Px(0.0))
        .child(
            crate::shell::dialog_head(ui, &t(ui, tx.title), close.clone())
                .layout(|l| l.shrink = 0.0),
        )
        .child(
            w::scroll(ui)
                .fill_width()
                .layout(|l| l.min_height = Length::Px(0.0))
                .child(body),
        )
        .child(buttons.layout(|l| l.shrink = 0.0));
    let m = w::modal_fitted(ui, t(ui, tx.title), content, true);
    // `.modal`: 560 wide, or as wide as the window allows.
    let _ = m
        .body()
        .variant("bare")
        .gap(0.0)
        .layout(|l| l.width = Length::Px(560.0));
    *slot.borrow_mut() = Some(m.clone());
    // The browser starts where the folder field points (the share's folder, the download
    // folder), else at the first library; This PC when that cannot be listed.
    let typed = folder.value().trim().to_owned();
    let start = if typed.is_empty() {
        libs.first()
            .map(|(_, p)| p.to_string_lossy().into_owned())
            .unwrap_or_default()
    } else {
        typed
    };
    load(start, true);
    (m, folder)
}

/// Lists a folder in the browser (`""`: This PC). What it opens becomes the folder chosen,
/// as in the Python app; with `or_top`, a folder that cannot be listed shows This PC.
type Load = Rc<dyn Fn(String, bool)>;

fn loader(
    h: &H,
    ui: &Ui,
    folder: &TextField,
    listing: Signal<Option<Result<LocalDirs, String>>>,
    picked: Signal<String>,
    libs: &[(&'static str, PathBuf)],
) -> Load {
    let names: Vec<(String, PathBuf)> = libs
        .iter()
        .map(|(k, p)| ((*k).to_owned(), p.clone()))
        .collect();
    let (h, u, f) = (h.clone(), ui.clone(), folder.clone());
    Rc::new(move |path: String, or_top: bool| {
        let (u2, f2, names) = (u.clone(), f.clone(), names.clone());
        let fut = async move {
            let top = path.trim().is_empty();
            match localdirs::browse(path, names.clone()).await {
                Err(_) if or_top && !top => localdirs::browse(String::new(), names).await,
                r => r,
            }
        };
        h.run(fut, move |r| match r {
            Ok(l) => {
                if !l.path.is_empty() {
                    f2.set_value(&l.path);
                    picked.set(l.path.clone());
                }
                listing.set(Some(Ok(l)));
            }
            Err(e) => listing.set(Some(Err(t(&u2, e.message())))),
        });
    })
}

/// The folder browser: Up, where it is, and the folders there. A click picks a folder; a
/// second click on it (or a double click) opens it.
fn browser(
    ui: &Ui,
    listing: Signal<Option<Result<LocalDirs, String>>>,
    picked: Signal<String>,
    folder: &TextField,
    load: &Load,
) -> El {
    let l1 = load.clone();
    let up = w::button(ui, t(ui, "Up"), move || {
        // Up from a drive (or the top of a network share) is This PC.
        let to = match listing.get_untracked().ok().flatten() {
            Some(Ok(l)) if !l.path.is_empty() => Some(l.parent.unwrap_or_default()),
            _ => None,
        };
        if let Some(to) = to {
            l1(to, false);
        }
    })
    .variant("quiet");
    let u2 = up.clone();
    let up = up.bind(move || {
        let at_top = !matches!(listing.get(), Some(Ok(l)) if !l.path.is_empty());
        let _ = u2.clone().disabled(at_top);
    });
    let u3 = ui.clone();
    let here = w::text(ui, "")
        .variant("mono_box")
        .truncate()
        .data_text()
        .text(move || match listing.get() {
            Some(Ok(l)) if !l.path.is_empty() => l.path,
            _ => t(&u3, "This PC"),
        });
    let list = w::column(ui).fill_width();
    let (l2, u4, f4, ld) = (list.clone(), ui.clone(), folder.clone(), load.clone());
    let list = list.bind(move || {
        let now = listing.get();
        untrack(|| {
            l2.clear();
            let ui = &u4;
            match now {
                None => {
                    let _ = l2.clone().child(w::spinner(ui, t(ui, "Loading…")));
                }
                Some(Err(e)) => {
                    let _ = l2.clone().child(w::text(ui, e).variant("error"));
                }
                Some(Ok(l)) if l.items.is_empty() => {
                    let _ = l2.clone().child(w::empty_state(
                        ui,
                        t(ui, "No subfolders"),
                        String::new(),
                    ));
                }
                Some(Ok(l)) => {
                    let at_top = l.path.is_empty();
                    for item in l.items {
                        let _ = l2
                            .clone()
                            .child(folder_row(ui, &item, at_top, picked, &f4, &ld));
                    }
                }
            }
        });
    });
    w::column(ui)
        .gap(12.0)
        .fill_width()
        // `.browser-head`
        .child(
            w::row(ui)
                .gap(8.0)
                .align(Align::Center)
                .fill_width()
                .child(up)
                .child(
                    here.grow(1.0)
                        .layout(|l| l.min_width = Length::Px(0.0)),
                ),
        )
        // `.folder-list`
        .child(
            ui.el("folder_list").column().fill_width().clip().child(
                w::scroll(ui)
                    .fill_width()
                    .height(200.0)
                    .layout(|l| l.min_height = Length::Px(120.0))
                    .child(list),
            ),
        )
}

fn folder_row(
    ui: &Ui,
    item: &localdirs::LocalDir,
    at_top: bool,
    picked: Signal<String>,
    folder: &TextField,
    load: &Load,
) -> El {
    let name = if at_top {
        place(ui, &item.name)
    } else {
        item.name.clone()
    };
    let (f, ld1, ld2, p1, p2, p3) = (
        folder.clone(),
        load.clone(),
        load.clone(),
        item.path.clone(),
        item.path.clone(),
        item.path.clone(),
    );
    // `.folder-item`
    let row = ui
        .el("folder_item")
        .row()
        .gap(10.0)
        .align(Align::Center)
        .fill_width()
        .on_click(move || {
            if picked.get_untracked().ok().as_deref() == Some(p1.as_str()) {
                ld1(p1.clone(), false);
            } else {
                f.set_value(&p1);
                picked.set(p1.clone());
            }
        })
        .child(crate::look::avatar_box(
            ui,
            34.0,
            w::icon(ui, w::Icon::Folder, 14.0),
        ))
        .child(crate::look::meta(ui, name.clone(), item.path.clone()))
        .on_double_click(move || ld2(p2.clone(), false));
    let r2 = row.clone();
    row.bind(move || {
        r2.state(remedy_ui::style::States::SELECTED, picked.get() == p3);
    })
    .semantics(
        remedy_ui_a11y::Semantics::new(remedy_ui_a11y::Role::Button)
            .label(name)
            .description(item.path.clone())
            .action(remedy_ui_a11y::Action::Click),
    )
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

    #[test]
    fn everything_the_dialog_says_is_a_catalog_key() {
        use super::{Mode, libraries, text};
        for mode in [Mode::Share, Mode::Edit("x".into()), Mode::Download] {
            let tx = text(&mode);
            for s in [tx.title, tx.hint, tx.ok] {
                assert!(crate::i18n::is_key(s), "{s:?}");
            }
        }
        for k in remedy_ui_services_desktop::KnownFolder::LIBRARIES {
            assert!(crate::i18n::is_key(k.name()), "{}", k.name());
        }
        let _ = libraries();
    }
}
