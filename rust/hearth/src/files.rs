//! The house: the top bar (group, who is on, search, invite, share), the sidebar (the PCs
//! and this PC's shares), and the Files and Chat tabs. Files browses a share on any PC with
//! back, forward and up, share pills, grid or list, selection, a context menu, downloads,
//! uploads and drag and drop both ways.

use std::path::PathBuf;
use std::rc::Rc;

use hearth_core::Member;
use hearth_core::client::Entry;
use remedy_ui::core::{Insets, Size};
use remedy_ui::host::FileDialog;
use remedy_ui::input::Key;
use remedy_ui::layout::Length;
use remedy_ui::reactive::{Effect, Memo, Signal, untrack};
use remedy_ui::view::{Align, Drag, DragData, DropEffect};
use remedy_ui::{El, Ui};
use remedy_ui_widgets_core::{self as w, DialogButton, Icon, MenuItem};
use remedy_ui_widgets_data::{
    Column, GridState, Selection, SortValue, TableState, Tiles, grid_view, table,
};
use remedy_ui_widgets_nav::{self as nav, Tab};

use crate::i18n::{t, tf, tr};
use crate::state::{Cursor, H, Listing, size_in};

/// The key of the "Up" entry inside a subfolder.
pub const UP: &str = "..";

/// One entry as the views show it.
#[derive(Clone, Debug, PartialEq)]
pub struct Item {
    /// Its name ([`UP`] for the parent folder).
    pub name: String,
    /// A folder.
    pub dir: bool,
    /// Bytes (files).
    pub size: u64,
}

impl Item {
    fn is_up(&self) -> bool {
        self.name == UP
    }

    fn size_text(&self, ui: &Ui) -> String {
        if self.is_up() {
            t(ui, "Parent folder")
        } else if self.dir {
            t(ui, "Folder")
        } else {
            size_in(ui, self.size)
        }
    }
}

/// What a listing shows: dot names hidden, folders first then by name ignoring case, only
/// names matching `search`, and "Up" first inside a subfolder.
pub fn items_for(entries: &[Entry], in_subfolder: bool, search: &str) -> Vec<Item> {
    let q = search.trim().to_lowercase();
    let mut v: Vec<Item> = entries
        .iter()
        .filter(|e| !e.name.starts_with('.'))
        .filter(|e| q.is_empty() || e.name.to_lowercase().contains(&q))
        .map(|e| Item {
            name: e.name.clone(),
            dir: e.dir,
            size: e.size,
        })
        .collect();
    v.sort_by(|a, b| {
        b.dir
            .cmp(&a.dir)
            .then_with(|| a.name.to_lowercase().cmp(&b.name.to_lowercase()))
    });
    if in_subfolder {
        v.insert(
            0,
            Item {
                name: UP.into(),
                dir: true,
                size: 0,
            },
        );
    }
    v
}

/// `name` inside the cursor's folder, as a share-relative path.
pub fn rel(c: &Cursor, name: &str) -> String {
    if c.path.is_empty() {
        name.to_owned()
    } else {
        format!("{}/{name}", c.path)
    }
}

/// What a drop holds: the files to send, and how many folders were in it. Hearth sends
/// files, never folders: since 0.4 a PC refuses uploads that would make one.
pub fn split_drop(paths: &[PathBuf]) -> (Vec<PathBuf>, usize) {
    let mut files = Vec::new();
    let mut folders = 0;
    for p in paths {
        match std::fs::metadata(p) {
            Ok(m) if m.is_dir() => folders += 1,
            Ok(m) if m.is_file() => files.push(p.clone()),
            _ => {}
        }
    }
    (files, folders)
}

/// Sends dropped or chosen files into `to`.
pub fn upload_into(h: &H, to: &Cursor, paths: &[PathBuf]) {
    let (files, folders) = split_drop(paths);
    if files.is_empty() {
        if folders > 0 {
            h.toast(
                "Hearth sends files, not folders. Open the folder and drop the files inside it.",
            );
        } else {
            h.toast("Nothing to upload. Empty folders are skipped.");
        }
        return;
    }
    if folders > 0 {
        h.toast("Folders were left out. Hearth sends the files you drop, not folders.");
    } else if files.len() > 20 {
        h.toast_f(
            "Uploading {count} files",
            &[("count", files.len().to_string())],
        );
    }
    let _ = h.core.upload(&to.device, &to.share, &to.path, &files);
}

fn member_of(h: &H, device: &str) -> Option<Member> {
    h.core.members().into_iter().find(|m| m.device_id == device)
}

fn reachable(h: &H, device: &str) -> bool {
    member_of(h, device).is_some_and(|m| m.is_self || m.online)
}

/// Downloads `names` (files only) from the current folder.
pub fn download(h: &H, names: &[String]) {
    let Some(c) = h.cursor.get_untracked().ok().flatten() else {
        return;
    };
    let files: Vec<String> = match h.listing.get_untracked().ok() {
        Some(Listing::Items(entries)) => names
            .iter()
            .filter(|n| entries.iter().any(|e| e.name == **n && !e.dir))
            .map(|n| rel(&c, n))
            .collect(),
        _ => Vec::new(),
    };
    if names.is_empty() {
        h.toast("Select a file to download");
        return;
    }
    if files.is_empty() {
        h.toast("Nothing to download");
        return;
    }
    if !reachable(h, &c.device) {
        h.toast("That PC is offline");
        return;
    }
    let _ = h.core.download(&c.device, &c.share, &files);
    match files.as_slice() {
        [one] => h.toast_f(
            "Downloading {name}",
            &[("name", one.rsplit('/').next().unwrap_or(one).to_owned())],
        ),
        many => h.toast_f(
            "Downloading {count} files",
            &[("count", many.len().to_string())],
        ),
    }
}

/// Opens an entry: a folder opens, a file downloads, Up goes up.
fn activate(h: &H, name: &str) {
    let Some(c) = h.cursor.get_untracked().ok().flatten() else {
        return;
    };
    if name == UP {
        h.go_up();
        return;
    }
    let dir = match h.listing.get_untracked().ok() {
        Some(Listing::Items(v)) => v.iter().find(|e| e.name == name).map(|e| e.dir),
        _ => None,
    };
    match dir {
        Some(true) => h.open(Cursor {
            path: rel(&c, name),
            ..c
        }),
        Some(false) => download(h, &[name.to_owned()]),
        None => {}
    }
}

fn copy_paths(h: &H, names: &[String]) {
    let Some(c) = h.cursor.get_untracked().ok().flatten() else {
        return;
    };
    let paths: Vec<String> = names
        .iter()
        .filter(|n| *n != UP)
        .map(|n| rel(&c, n))
        .collect();
    if paths.is_empty() {
        return;
    }
    h.app.copy(paths.join("\n"), false);
    h.toast("Path copied");
}

/// Shares a dropped folder read-only.
fn share_dropped(h: &H, paths: &[PathBuf]) {
    let mut done = 0;
    for p in paths.iter().filter(|p| p.is_dir()) {
        match h.core.add_share(p, "", false) {
            Ok(s) => {
                done += 1;
                h.toast_f(
                    "Shared {label} with the house",
                    &[("label", s.display_label())],
                );
            }
            Err(e) => h.toast(e),
        }
    }
    if done == 0 {
        h.toast("Drop a folder to share it");
    }
}

/// Asks before stopping a share.
pub fn confirm_unshare(h: &H, ui: &Ui, id: &str, label: &str) {
    let (h2, id) = (h.clone(), id.to_owned());
    let _ = w::dialog(
        ui,
        t(ui, "Stop sharing?"),
        tf(
            ui,
            "The other PCs will no longer see {label}. Nothing on this PC is deleted.",
            &[("label", label.to_owned())],
        ),
        vec![
            DialogButton::new(t(ui, "Stop sharing"), "danger", move || {
                h2.core.remove_share(&id);
                h2.toast("Share removed");
            }),
            DialogButton::new(t(ui, "Cancel"), "quiet", || {}),
        ],
    );
}

// ---------------------------------------------------------------- top bar

fn top_bar(h: &H, ui: &Ui, narrow: Signal<bool>, sidebar_open: Signal<bool>) -> El {
    let unit = ui.theme().spacing.unit;
    let (core, g) = (h.core.clone(), h.group);
    let name = w::heading(ui, 2, t(ui, "Home")).text(move || {
        let _ = g.get();
        core.group().map(|g| g.name).unwrap_or_default()
    });
    let (core, p, u1) = (h.core.clone(), h.peers, ui.clone());
    let online = w::text(ui, "").variant("muted").nowrap().text(move || {
        let _ = p.get();
        let n = core
            .members()
            .iter()
            .filter(|m| m.online || m.is_self)
            .count();
        tf(&u1, "{count} on the network", &[("count", n.to_string())])
    });
    let search = h.search;
    let search = w::search(ui, t(ui, "Search PCs or files"), move |q| search.set(q)).width(260.0);
    let h1 = h.clone();
    let invite = w::button(ui, t(ui, "Copy invite"), move || match h1.core.invite() {
        Some(i) => {
            h1.app.copy(i, true);
            h1.toast("Invite copied");
        }
        None => h1.toast("Join or start a home group first"),
    });
    let (h2, u2) = (h.clone(), ui.clone());
    let share = w::button(ui, t(ui, "Share a folder"), move || {
        crate::share_dialog::open(&h2, &u2, crate::share_dialog::Mode::Share)
    })
    .variant("primary");
    let toggle = w::icon_button(ui, Icon::Users, t(ui, "PCs and shares"), move || {
        sidebar_open.update(|o| *o = !*o)
    });
    let t2 = toggle.clone();
    let toggle = toggle.bind(move || {
        let _ = t2.clone().hidden(!narrow.get());
    });
    w::row(ui)
        .fill_width()
        .align(Align::Center)
        .gap(unit * 3.0)
        .padding(Insets::symmetric(unit * 2.0, unit * 4.0))
        .child(toggle)
        .child(w::column(ui).grow(1.0).child(name).child(online))
        .child(search)
        .child(invite)
        .child(share)
}

// ---------------------------------------------------------------- sidebar

fn pc_row(h: &H, ui: &Ui, m: &Member) -> El {
    let unit = ui.theme().spacing.unit;
    let mut sub: Vec<String> = m.ips.iter().take(2).cloned().collect();
    if sub.is_empty() {
        sub.push(t(ui, "local"));
    }
    let mut subtitle = sub.join(" · ");
    if m.bridge {
        subtitle.push_str(&t(ui, " · bridge"));
    }
    let title = if m.is_self {
        format!("{}{}", m.name, t(ui, " · you"))
    } else {
        m.name.clone()
    };
    let lit = Signal::new(m.online || m.is_self);
    let (h1, id, name) = (h.clone(), m.device_id.clone(), m.name.clone());
    let row = w::button(ui, "", move || h1.open_pc(&id, &name))
        .variant("quiet")
        .fill_width();
    // The button's own label is replaced by the row's content.
    for c in row.child_elements() {
        c.remove();
    }
    let row = row
        .child(
            w::row(ui)
                .gap(unit * 2.0)
                .align(Align::Center)
                .fill_width()
                .child(w::avatar(ui, &m.name, 32.0, ui.theme().colors.accent))
                .child(
                    w::column(ui)
                        .grow(1.0)
                        .child(w::text(ui, title.clone()).variant("strong").nowrap())
                        .child(w::text(ui, subtitle).variant("muted").nowrap()),
                )
                .child(w::lamp(
                    ui,
                    if m.online || m.is_self {
                        t(ui, "Online")
                    } else {
                        t(ui, "Offline")
                    },
                    lit,
                )),
        )
        .semantics(
            remedy_ui_a11y::Semantics::new(remedy_ui_a11y::Role::Button)
                .label(title)
                .action(remedy_ui_a11y::Action::Click),
        );
    // Files dropped on a PC go to its first share that takes uploads; the view stays put.
    let (h2, h3, dev, dev2, nm) = (
        h.clone(),
        h.clone(),
        m.device_id.clone(),
        m.device_id.clone(),
        m.name.clone(),
    );
    row.on_drop(
        move |d, _| {
            let _ = &h2;
            if d.data.files.is_empty() || dev.is_empty() {
                DropEffect::None
            } else {
                DropEffect::Copy
            }
        },
        move |d, _, _| {
            let (h4, files, dev3, nm2) =
                (h3.clone(), d.data.files.clone(), dev2.clone(), nm.clone());
            let core = h3.core.clone();
            let d4 = dev3.clone();
            h3.run(async move { core.shares_of(&d4).await }, move |r| match r {
                Ok(shares) => match shares.into_iter().find(|s| s.writable) {
                    Some(s) => {
                        upload_into(
                            &h4,
                            &Cursor {
                                device: dev3,
                                share: s.id,
                                path: String::new(),
                            },
                            &files,
                        );
                        h4.toast_f("Sent to {name}", &[("name", nm2)]);
                    }
                    None => {
                        h4.toast_f("{name} has no folder that allows uploads", &[("name", nm2)])
                    }
                },
                Err(e) => h4.toast(e),
            });
        },
    )
}

fn own_share_card(h: &H, ui: &Ui, s: &hearth_core::state::Share) -> El {
    let unit = ui.theme().spacing.unit;
    let label = crate::i18n::place(ui, &s.display_label());
    let me = h.core.device_id();
    let (h1, id1, me1) = (h.clone(), s.id.clone(), me.clone());
    let open = move || {
        h1.chat_tab.set(false);
        h1.open(Cursor {
            device: me1.clone(),
            share: id1.clone(),
            path: String::new(),
        });
    };
    let open = Rc::new(open);
    let o1 = open.clone();
    let (h2, u2, id2) = (h.clone(), ui.clone(), s.id.clone());
    let edit = w::button(ui, t(ui, "Edit"), move || {
        crate::share_dialog::open(&h2, &u2, crate::share_dialog::Mode::Edit(id2.clone()))
    })
    .variant("quiet");
    let (h3, u3, id3, l3) = (h.clone(), ui.clone(), s.id.clone(), label.clone());
    let remove = w::icon_button(ui, Icon::Close, t(ui, "Remove share"), move || {
        confirm_unshare(&h3, &u3, &id3, &l3)
    })
    .variant("quiet");
    let card = w::card(ui)
        .fill_width()
        .on_click(move || o1())
        .child(
            w::row(ui)
                .gap(unit * 2.0)
                .align(Align::Center)
                .child(w::icon(ui, Icon::Folder, 20.0))
                .child(
                    w::column(ui)
                        .grow(1.0)
                        .child(w::text(ui, label.clone()).variant("strong").nowrap())
                        .child(
                            w::text(
                                ui,
                                if s.writable {
                                    t(ui, "Read & write")
                                } else {
                                    t(ui, "Read only")
                                },
                            )
                            .variant("muted"),
                        ),
                )
                .child(edit)
                .child(remove),
        )
        .focusable(true)
        .semantics(
            remedy_ui_a11y::Semantics::new(remedy_ui_a11y::Role::Button)
                .label(tf(ui, "Open {label}", &[("label", label.clone())]))
                .action(remedy_ui_a11y::Action::Click),
        );
    let (h4, u4, id4, l4) = (h.clone(), ui.clone(), s.id.clone(), label);
    w::context_menu(&card, move || {
        let (o, h5, u5, id5, h6, u6, id6, l6) = (
            open.clone(),
            h4.clone(),
            u4.clone(),
            id4.clone(),
            h4.clone(),
            u4.clone(),
            id4.clone(),
            l4.clone(),
        );
        vec![
            MenuItem::new(t(&u5, "Open"), move || o()),
            MenuItem::new(t(&u5, "Edit"), {
                let u5 = u5.clone();
                move || {
                    crate::share_dialog::open(
                        &h5,
                        &u5,
                        crate::share_dialog::Mode::Edit(id5.clone()),
                    )
                }
            }),
            MenuItem::new(t(&u6, "Unshare"), {
                let u6 = u6.clone();
                move || confirm_unshare(&h6, &u6, &id6, &l6)
            })
            .danger(),
        ]
    })
}

fn sidebar(h: &H, ui: &Ui) -> El {
    let unit = ui.theme().spacing.unit;
    // PCs.
    let (p, search, core) = (h.peers, h.search, h.core.clone());
    let count = w::text(ui, "").variant("muted").text(move || {
        let _ = p.get();
        core.members()
            .iter()
            .filter(|m| m.online || m.is_self)
            .count()
            .to_string()
    });
    let list = w::column(ui).gap(unit).fill_width();
    let (l2, h2, u2) = (list.clone(), h.clone(), ui.clone());
    // The rows are made again only when who is listed changes: each build owns the rows'
    // state, so a run that kept the old rows would leave them reading disposed signals.
    let (core2, g) = (h.core.clone(), h.group);
    let members = Memo::new(move || {
        let _ = p.get();
        let _ = g.get();
        let q = search.get().trim().to_lowercase();
        let mut members: Vec<Member> = core2
            .members()
            .into_iter()
            .filter(|m| q.is_empty() || m.name.to_lowercase().contains(&q))
            .collect();
        members.sort_by(|a, b| {
            b.is_self
                .cmp(&a.is_self)
                .then(b.online.cmp(&a.online))
                .then_with(|| a.name.to_lowercase().cmp(&b.name.to_lowercase()))
        });
        members
    });
    let list = list.bind(move || {
        let members = members.get();
        untrack(|| {
            l2.clear();
            for m in &members {
                let _ = l2.clone().child(pc_row(&h2, &u2, m));
            }
        });
    });
    // This PC's shares.
    let shares = w::column(ui).gap(unit * 2.0).fill_width();
    let (s2, h3, u3, sh) = (shares.clone(), h.clone(), ui.clone(), h.shares);
    let shares = shares.bind(move || {
        let _ = sh.get();
        let list = h3.core.shares();
        untrack(|| {
            s2.clear();
            if list.is_empty() {
                let (h4, u4) = (h3.clone(), u3.clone());
                let _ = s2
                    .clone()
                    .child(
                        w::text(&u3, t(&u3, "Share a folder, or drop one here.")).variant("muted"),
                    )
                    .child(
                        w::button(&u3, t(&u3, "Browse…"), move || {
                            crate::share_dialog::browse_and_share(&h4, &u4)
                        })
                        .variant("quiet"),
                    );
            }
            for s in &list {
                let _ = s2.clone().child(own_share_card(&h3, &u3, s));
            }
        });
    });
    let h5 = h.clone();
    let shares_section = w::column(ui)
        .gap(unit * 2.0)
        .fill_width()
        .child(w::text(ui, t(ui, "Shared from this PC")).variant("strong"))
        .child(shares)
        .on_drop(
            |d, _| {
                if d.data.files.iter().any(|f| f.is_dir()) {
                    DropEffect::Copy
                } else {
                    DropEffect::None
                }
            },
            move |d, _, _| share_dropped(&h5, &d.data.files),
        )
        .keyboard_alternative(t(
            ui,
            "Share a folder (or Browse…) chooses a folder to share",
        ));
    w::scroll(ui)
        .width(280.0)
        .layout(|l| {
            l.shrink = 0.0;
            l.min_height = Length::Px(0.0);
        })
        .child(
            w::column(ui)
                .gap(unit * 4.0)
                .padding(Insets::all(unit * 3.0))
                .fill_width()
                .child(
                    w::row(ui)
                        .gap(unit * 2.0)
                        .child(w::text(ui, t(ui, "PCs")).variant("strong"))
                        .child(count),
                )
                .child(list)
                .child(w::divider(ui, false))
                .child(shares_section),
        )
}

// ---------------------------------------------------------------- files pane

fn toolbar(h: &H, ui: &Ui, sel: &Selection<String>) -> El {
    let unit = ui.theme().spacing.unit;
    let (h1, h2, h3) = (h.clone(), h.clone(), h.clone());
    let back = w::icon_button(ui, Icon::ArrowLeft, t(ui, "Back"), move || h1.go_back());
    let fwd = w::icon_button(ui, Icon::ArrowRight, t(ui, "Forward"), move || {
        h2.go_forward()
    });
    let up = w::icon_button(ui, Icon::ArrowUp, t(ui, "Up one folder"), move || {
        h3.go_up()
    });
    let (b2, f2, u2, cb, cf, cur) = (
        back.clone(),
        fwd.clone(),
        up.clone(),
        h.can_back,
        h.can_forward,
        h.cursor,
    );
    let back = back.bind(move || {
        let _ = b2.clone().disabled(!cb.get());
    });
    let fwd = fwd.bind(move || {
        let _ = f2.clone().disabled(!cf.get());
    });
    let up = up.bind(move || {
        let _ = u2
            .clone()
            .disabled(cur.get().is_none_or(|c| c.path.is_empty()));
    });
    // Share pills: the shares of the PC being browsed.
    let pills = w::row(ui).gap(unit);
    let (p2, h4, u4, rs) = (pills.clone(), h.clone(), ui.clone(), h.remote_shares);
    let pills = pills.bind(move || {
        let shares = rs.get();
        let c = cur.get();
        untrack(|| {
            p2.clear();
            for s in &shares {
                let on = c.as_ref().is_some_and(|c| c.share == s.id);
                let (h5, id, c2) = (h4.clone(), s.id.clone(), c.clone());
                let b = w::button(&u4, crate::i18n::place(&u4, &s.label), move || {
                    if let Some(c) = &c2 {
                        h5.open(Cursor {
                            device: c.device.clone(),
                            share: id.clone(),
                            path: String::new(),
                        });
                    }
                })
                .variant(if on { "primary" } else { "quiet" });
                let _ = p2.clone().child(b);
            }
        });
    });
    // Grid or list.
    let view = Signal::new(if h.grid.get_untracked().unwrap_or(true) {
        0usize
    } else {
        1
    });
    let grid = h.grid;
    let _ = Effect::new(move || {
        let g = view.get() == 0;
        untrack(|| grid.set_if_changed(g));
    });
    let (grid_name, list_name) = (t(ui, "Grid"), t(ui, "List"));
    let seg = w::segmented(
        ui,
        t(ui, "View"),
        &[grid_name.as_str(), list_name.as_str()],
        view,
    );
    // Download (files selected) and Upload (a share that takes files).
    let (h6, sel2) = (h.clone(), sel.clone());
    let dl = w::button(ui, t(ui, "Download"), move || {
        download(&h6, &sel2.selected.get_untracked().unwrap_or_default())
    })
    .variant("primary");
    let (d2, sel3, lst, ud) = (dl.clone(), sel.clone(), h.listing, ui.clone());
    let dl = dl.bind(move || {
        let picked = sel3.selected.get();
        let n = match lst.get() {
            Listing::Items(v) => picked
                .iter()
                .filter(|p| v.iter().any(|e| e.name == **p && !e.dir))
                .count(),
            _ => 0,
        };
        let label = if n > 1 {
            tf(&ud, "Download {count}", &[("count", n.to_string())])
        } else {
            t(&ud, "Download")
        };
        let _ = d2.clone().hidden(n == 0);
        if let Some(l) = d2.child_elements().into_iter().next() {
            let _ = l.text(remedy_ui::view::RichText::plain(label));
        }
    });
    let (h7, u7) = (h.clone(), ui.clone());
    let upload = w::icon_button(ui, Icon::Upload, t(ui, "Upload"), move || {
        pick_and_upload(&h7, &u7)
    });
    let (up2, rs2) = (upload.clone(), h.remote_shares);
    let upload = upload.bind(move || {
        let c = cur.get();
        let writable = c.is_some_and(|c| rs2.get().iter().any(|s| s.id == c.share && s.writable));
        let _ = up2.clone().hidden(!writable);
    });
    w::row(ui)
        .fill_width()
        .align(Align::Center)
        .gap(unit * 2.0)
        .padding(Insets::symmetric(unit, unit * 3.0))
        .child(back)
        .child(fwd)
        .child(up)
        .child(pills.grow(1.0))
        .child(seg)
        .child(dl)
        .child(upload)
}

fn pick_and_upload(h: &H, ui: &Ui) {
    let Some(c) = h.cursor.get_untracked().ok().flatten() else {
        return;
    };
    let h2 = h.clone();
    let win = h.window.borrow().clone();
    h.app.file_dialog(
        win.as_ref(),
        {
            let mut d = FileDialog::default();
            d.title = t(ui, "Upload to this folder");
            d.multiple = true;
            d
        },
        move |paths| {
            if !paths.is_empty() {
                upload_into(&h2, &c, &paths);
            }
        },
    );
}

fn crumbs(h: &H, ui: &Ui) -> El {
    let path: Signal<Vec<String>> = Signal::new(Vec::new());
    let (cur, rs, u1) = (h.cursor, h.remote_shares, ui.clone());
    let _ = Effect::new(move || {
        let c = cur.get();
        let shares = rs.get();
        let v = match c {
            Some(c) => {
                let label = shares.iter().find(|s| s.id == c.share).map_or_else(
                    || t(&u1, "Shared folder"),
                    |s| crate::i18n::place(&u1, &s.label),
                );
                std::iter::once(label)
                    .chain(
                        c.path
                            .split('/')
                            .filter(|s| !s.is_empty())
                            .map(str::to_owned),
                    )
                    .collect()
            }
            None => Vec::new(),
        };
        untrack(|| path.set_if_changed(v));
    });
    let h2 = h.clone();
    nav::breadcrumbs(ui, t(ui, "Folder"), path, move |i| {
        let Some(c) = h2.cursor.get_untracked().ok().flatten() else {
            return;
        };
        let segs: Vec<&str> = c.path.split('/').filter(|s| !s.is_empty()).collect();
        let to = segs.get(..i).map(|s| s.join("/")).unwrap_or_default();
        if to != c.path {
            h2.open(Cursor { path: to, ..c });
        }
    })
}

/// Whether the folder shown takes files.
fn writable_here(h: &H) -> bool {
    let Some(c) = h.cursor.get_untracked().ok().flatten() else {
        return false;
    };
    h.remote_shares
        .with_untracked(|v| v.iter().any(|s| s.id == c.share && s.writable))
        .unwrap_or(false)
}

/// What dragging entries out carries: this PC's files themselves; another PC's are fetched
/// first.
fn drag_out(h: &H, names: &[String]) -> Option<Drag> {
    let c = h.cursor.get_untracked().ok().flatten()?;
    let names: Vec<String> = names.iter().filter(|n| *n != UP).cloned().collect();
    if names.is_empty() {
        return None;
    }
    if c.device == h.core.device_id() {
        let files: Vec<PathBuf> = names
            .iter()
            .filter_map(|n| h.core.local_path(&c.share, &rel(&c, n)))
            .collect();
        return (!files.is_empty()).then(|| {
            Drag::new(DragData {
                files,
                ..DragData::default()
            })
            .allow(&[DropEffect::Copy])
        });
    }
    download(h, &names);
    h.toast("Downloading first — drag it out once it finishes.");
    None
}

fn accept_drop(h: &H, d: &Drag) -> DropEffect {
    let can = !d.data.files.is_empty()
        && h.cursor.get_untracked().ok().flatten().is_some()
        && writable_here(h);
    if can {
        DropEffect::Copy
    } else {
        DropEffect::None
    }
}

/// The hint while files are dragged over the folder shown.
fn drop_hint(h: &H, ui: &Ui, d: Option<&Drag>) -> Option<String> {
    let d = d.filter(|d| !d.data.files.is_empty() && !d.from_this_app())?;
    h.cursor.get_untracked().ok().flatten()?;
    let _ = d;
    Some(if writable_here(h) {
        t(ui, "Drop files to add them to this folder")
    } else {
        t(ui, "This folder is read-only")
    })
}

fn take_drop(h: &H, d: &Drag, target: Option<&String>) {
    let Some(c) = h.cursor.get_untracked().ok().flatten() else {
        return;
    };
    let into_folder =
        target
            .filter(|t| *t != UP)
            .and_then(|t| match h.listing.get_untracked().ok() {
                Some(Listing::Items(v)) => v
                    .iter()
                    .find(|e| e.name == *t && e.dir)
                    .map(|e| rel(&c, &e.name)),
                _ => None,
            });
    let to = match into_folder {
        Some(p) => Cursor { path: p, ..c },
        None => c,
    };
    upload_into(h, &to, &d.data.files);
}

fn files_pane(h: &H, ui: &Ui) -> El {
    let unit = ui.theme().spacing.unit;
    let items: Signal<Vec<Item>> = Signal::new(Vec::new());
    let (lst, cur, search) = (h.listing, h.cursor, h.search);
    let _ = Effect::new(move || {
        let v = match lst.get() {
            Listing::Items(e) => items_for(
                &e,
                cur.get().is_some_and(|c| !c.path.is_empty()),
                &search.get(),
            ),
            _ => Vec::new(),
        };
        untrack(|| items.set_if_changed(v));
    });
    // One selection for both views.
    let gstate: GridState<String> = GridState::new(true);
    let mut tstate: TableState<String> = TableState::new(true);
    tstate.selection = gstate.selection.clone();
    let sel = gstate.selection.clone();
    // A new folder starts with nothing selected.
    let s0 = sel.clone();
    let _ = Effect::new(move || {
        let _ = cur.get();
        untrack(|| {
            s0.clear();
            s0.cursor.set(None);
        });
    });
    let hint: Signal<Option<String>> = Signal::new(None);
    let (ha, hb, hc, hd) = (h.clone(), h.clone(), h.clone(), h.clone());
    gstate.set_drag(move |k| drag_out(&ha, k));
    gstate.set_drop(
        move |d, _| accept_drop(&hb, d),
        move |d, k, _| take_drop(&hc, d, k),
    );
    let (he, hf, hg) = (h.clone(), h.clone(), h.clone());
    tstate.set_drag(move |k| drag_out(&hd, k));
    tstate.set_drop(
        move |d, _| accept_drop(&he, d),
        move |d, k, _| take_drop(&hf, d, k),
    );

    let tiles = Tiles::new(Size::new(132.0, 112.0), |ui: &Ui, it: &Item| {
        let unit = ui.theme().spacing.unit;
        let icon = if it.dir { Icon::Folder } else { Icon::File };
        let name = if it.is_up() {
            t(ui, "Up")
        } else {
            it.name.clone()
        };
        w::column(ui)
            .fill_width()
            .align(Align::Center)
            .gap(unit)
            .padding(Insets::all(unit * 2.0))
            .child(w::icon(
                ui,
                if it.is_up() { Icon::ArrowUp } else { icon },
                40.0,
            ))
            .child(w::text(ui, name).nowrap().data_text())
            .child(w::text(ui, it.size_text(ui)).variant("muted").nowrap())
    });
    let h1 = hg.clone();
    let grid = grid_view(
        ui,
        t(ui, "Folder contents"),
        items,
        |i: &Item| i.name.clone(),
        tiles,
        &gstate,
        move |k| activate(&h1, k),
    );
    let columns = vec![
        Column::new("name", t(ui, "Name"), 360.0, |ui: &Ui, it: &Item| {
            let icon = if it.is_up() {
                Icon::ArrowUp
            } else if it.dir {
                Icon::Folder
            } else {
                Icon::File
            };
            w::row(ui)
                .gap(ui.theme().spacing.unit * 2.0)
                .align(Align::Center)
                .child(w::icon(ui, icon, 18.0))
                .child(
                    w::text(
                        ui,
                        if it.is_up() {
                            t(ui, "Up")
                        } else {
                            it.name.clone()
                        },
                    )
                    .nowrap()
                    .data_text(),
                )
        })
        .min_width(160.0)
        .sort_by(|i: &Item| {
            SortValue::Text(format!(
                "{}{}{}",
                u8::from(!i.is_up()),
                u8::from(!i.dir),
                i.name.to_lowercase()
            ))
        }),
        Column::new("size", t(ui, "Size"), 140.0, |ui: &Ui, it: &Item| {
            w::text(ui, it.size_text(ui)).variant("muted").nowrap()
        })
        .sort_by(|i: &Item| {
            if i.dir {
                SortValue::Missing
            } else {
                SortValue::Number(i.size as f64)
            }
        })
        .align_end(),
    ];
    let h2 = hg.clone();
    let list = table(
        ui,
        t(ui, "Folder contents"),
        items,
        |i: &Item| i.name.clone(),
        columns,
        &tstate,
        move |k| activate(&h2, k),
    );
    let gr = h.grid;
    let (g2, l2) = (grid.clone(), list.clone());
    let views = w::column(ui)
        .grow(1.0)
        .fill_width()
        .layout(|l| l.min_height = Length::Px(0.0))
        .child(grid)
        .child(list)
        .bind(move || {
            let g = gr.get();
            let _ = g2.clone().hidden(!g);
            let _ = l2.clone().hidden(g);
        });

    // What shows instead of entries: nothing open, loading, a failure, an empty folder.
    let note = w::column(ui)
        .grow(1.0)
        .fill_width()
        .align(Align::Center)
        .padding(Insets::all(unit * 8.0));
    let (n2, v2, h3, u3, sh) = (note.clone(), views.clone(), h.clone(), ui.clone(), h.shares);
    let note = note.bind(move || {
        let l = lst.get();
        let c = cur.get();
        let _ = sh.get();
        let n_items = items.with(Vec::len);
        untrack(|| {
            n2.clear();
            let in_sub = c.as_ref().is_some_and(|c| !c.path.is_empty());
            let empty = matches!(&l, Listing::Items(_)) && n_items <= usize::from(in_sub);
            let content: Option<El> = match &l {
                Listing::None => {
                    let (h4, u4) = (h3.clone(), u3.clone());
                    Some(
                        w::column(&u3)
                            .gap(16.0)
                            .align(Align::Center)
                            .child(w::empty_state(
                                &u3,
                                t(&u3, "Nothing open"),
                                t(&u3, "Share a folder with the house, or drop one here."),
                            ))
                            .child(w::button(&u3, t(&u3, "Browse…"), move || {
                                crate::share_dialog::browse_and_share(&h4, &u4)
                            })),
                    )
                }
                Listing::Loading => Some(w::spinner(&u3, t(&u3, "Loading…"))),
                Listing::Failed(e) => Some(w::text(&u3, tr(&u3, e)).variant("error")),
                Listing::Items(_)
                    if empty && search.get_untracked().is_ok_and(|s| !s.trim().is_empty()) =>
                {
                    Some(w::text(&u3, t(&u3, "Nothing here matches the search.")).variant("muted"))
                }
                Listing::Items(_) if empty && writable_here(&h3) => Some(w::empty_state(
                    &u3,
                    t(&u3, "Empty folder"),
                    t(&u3, "Empty folder. Drag files here to add them."),
                )),
                Listing::Items(_) if empty => Some(w::empty_state(&u3, t(&u3, "Empty folder"), "")),
                Listing::Items(_) => None,
            };
            // An empty folder still shows Up (and takes drops) above the note.
            let _ = v2
                .clone()
                .hidden(!matches!(&l, Listing::Items(_)) || (empty && !in_sub));
            let _ = n2.clone().hidden(content.is_none());
            if let Some(c) = content {
                let _ = n2.clone().child(c);
            }
        });
    });
    // The drop hint, shown while something is dragged over the files.
    let hint_label = w::text(ui, "")
        .variant("muted")
        .text(move || hint.get().unwrap_or_default());
    let hl = hint_label.clone();
    let hint_label = hint_label.bind(move || {
        let on = hint.get().is_some();
        let _ = hl.clone().hidden(!on);
    });

    // Keyboard: back, forward, up, copy paths.
    let (h5, sel5) = (h.clone(), sel.clone());
    let on_key = move |e: &remedy_ui::input::KeyEvent| {
        if !e.pressed {
            return false;
        }
        let m = e.modifiers;
        match &e.key {
            Key::Backspace if !m.ctrl && !m.alt => h5.go_back(),
            Key::ArrowLeft if m.alt => h5.go_back(),
            Key::ArrowRight if m.alt => h5.go_forward(),
            Key::ArrowUp if m.alt => h5.go_up(),
            Key::Character(s) if s == "c" && (m.ctrl || m.meta) => {
                copy_paths(&h5, &sel5.selected.get_untracked().unwrap_or_default());
            }
            _ => return false,
        }
        true
    };
    let (hh, uh) = (h.clone(), ui.clone());
    let pane = w::column(ui)
        .grow(1.0)
        .fill_width()
        .layout(|l| l.min_height = Length::Px(0.0))
        .child(toolbar(h, ui, &sel))
        .child(crumbs(h, ui).padding(Insets::symmetric(0.0, unit * 3.0)))
        .child(hint_label.padding(Insets::symmetric(unit, unit * 3.0)))
        .child(views.on_drag_hover(move |d| hint.set(drop_hint(&hh, &uh, d))))
        .child(note)
        .child(crate::transfers::panel(h, ui))
        .on_key(on_key);
    let (h8, sel8, um) = (h.clone(), sel.clone(), ui.clone());
    w::context_menu(&pane, move || {
        let picked = sel8.selected.get_untracked().unwrap_or_default();
        let (a, b, c) = (h8.clone(), h8.clone(), h8.clone());
        let (p1, p2, p3) = (picked.clone(), picked.clone(), picked);
        vec![
            MenuItem::new(t(&um, "Open"), move || {
                if let Some(first) = p1.first() {
                    activate(&a, first);
                }
            }),
            MenuItem::new(t(&um, "Download"), move || download(&b, &p2)),
            MenuItem::new(t(&um, "Copy path"), move || copy_paths(&c, &p3)),
        ]
    })
}

// ---------------------------------------------------------------- the house

/// The main window's content once this PC is in a group.
pub fn main(h: &H, ui: &Ui) -> El {
    let unit = ui.theme().spacing.unit;
    let narrow = Signal::new(false);
    let sidebar_open = Signal::new(false);
    let side = sidebar(h, ui);
    let s2 = side.clone();
    let side = side.bind(move || {
        let _ = s2.clone().hidden(narrow.get() && !sidebar_open.get());
    });
    let tabs = Signal::new(vec![
        Tab {
            id: "files".into(),
            title: t(ui, "Files"),
            closable: false,
        },
        Tab {
            id: "chat".into(),
            title: t(ui, "Chat"),
            closable: false,
        },
    ]);
    let tab: Signal<Option<String>> = Signal::new(Some("files".into()));
    let ct = h.chat_tab;
    // The tab and `chat_tab` follow each other (opening a PC switches to Files).
    let _ = Effect::new(move || {
        let chat = ct.get();
        untrack(|| tab.set_if_changed(Some(if chat { "chat" } else { "files" }.to_owned())));
    });
    let _ = Effect::new(move || {
        let t = tab.get();
        untrack(|| ct.set_if_changed(t.as_deref() == Some("chat")));
    });
    let files = files_pane(h, ui);
    let chat = crate::chat::chat(h, ui);
    let (f2, c2) = (files.clone(), chat.clone());
    let body = w::column(ui)
        .grow(1.0)
        .fill_width()
        .layout(|l| l.min_height = Length::Px(0.0))
        .child(
            nav::tabs(ui, t(ui, "Sections"), tabs, tab, |_| {})
                .padding(Insets::symmetric(0.0, unit * 3.0)),
        )
        .child(files)
        .child(chat)
        .bind(move || {
            let chat = ct.get();
            let _ = f2.clone().hidden(chat);
            let _ = c2.clone().hidden(!chat);
        });
    w::column(ui)
        .grow(1.0)
        .fill_width()
        .layout(|l| l.min_height = Length::Px(0.0))
        .on_resize(move |s| narrow.set_if_changed(s.width <= 900.0))
        .child(top_bar(h, ui, narrow, sidebar_open))
        .child(w::divider(ui, false))
        .child(
            w::row(ui)
                .grow(1.0)
                .fill_width()
                .layout(|l| l.min_height = Length::Px(0.0))
                .child(side)
                .child(w::divider(ui, true))
                .child(body),
        )
}

#[cfg(test)]
mod tests {
    use hearth_core::client::Entry;

    use super::*;

    fn e(name: &str, dir: bool) -> Entry {
        Entry {
            name: name.into(),
            dir,
            size: 2048,
            mtime: 0.0,
        }
    }

    #[test]
    fn listings_hide_dot_names_and_put_folders_first() {
        let v = items_for(
            &[
                e("b.txt", false),
                e(".git", true),
                e("Zeta", true),
                e("alpha", true),
                e("A.txt", false),
            ],
            false,
            "",
        );
        let names: Vec<&str> = v.iter().map(|i| i.name.as_str()).collect();
        assert_eq!(names, ["alpha", "Zeta", "A.txt", "b.txt"]);
        let v = items_for(&[e("b.txt", false)], true, "");
        assert!(
            v.first().is_some_and(Item::is_up),
            "Up comes first in a subfolder"
        );
        assert_eq!(
            v.get(1).map(|i| crate::state::size_text(i.size)).as_deref(),
            Some("2.0 KB")
        );
        assert_eq!(
            items_for(&[e("Photos", true), e("notes.txt", false)], false, "PHO").len(),
            1
        );
    }

    #[test]
    fn drops_send_files_and_count_the_folders_left_out() {
        let d = tempfile::tempdir().unwrap_or_else(|e| unreachable!("{e}"));
        let trip = d.path().join("Trip");
        let _ = std::fs::create_dir_all(&trip);
        let loose = d.path().join("loose.txt");
        let _ = std::fs::write(&loose, b"x");
        let (files, folders) = split_drop(&[trip, loose.clone(), d.path().join("gone.txt")]);
        assert_eq!((files, folders), (vec![loose], 1));
    }

    #[test]
    fn paths_join_inside_the_folder() {
        let c = Cursor {
            device: "d".into(),
            share: "s".into(),
            path: String::new(),
        };
        assert_eq!(rel(&c, "a.txt"), "a.txt");
        let c = Cursor {
            path: "x/y".into(),
            ..c
        };
        assert_eq!(rel(&c, "a.txt"), "x/y/a.txt");
    }
}
