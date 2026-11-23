//! Settings, in six sections: General (name, start at sign-in, theme, reset), Sharing,
//! Transfers (speed limits), Network (invite, extra hosts, bridge, leaving), Updates and
//! About. Reachable before joining a group too.

use std::cell::RefCell;

use remedy_ui::core::Insets;
use remedy_ui::layout::Length;
use remedy_ui::reactive::{Effect, Signal, untrack};
use remedy_ui::view::{Align, Justify};
use remedy_ui::{El, Ui};
use remedy_ui_services_desktop::autostart;
use remedy_ui_services_i18n::{CalendarSymbols, Date, Time};
use remedy_ui_services_update::UpdateState;
use remedy_ui_widgets_core::{self as w, DialogButton, Modal};

use crate::i18n::{LANGUAGES, t, tf, tr};
use crate::state::H;

/// The sections, as (id, title).
pub const SECTIONS: [(&str, &str); 6] = [
    ("general", "General"),
    ("sharing", "Sharing"),
    ("transfers", "Transfers"),
    ("network", "Network"),
    ("updates", "Updates"),
    ("about", "About"),
];

/// Theme ids in the order 0.3.x lists them.
pub const THEMES: [&str; 13] = [
    "system", "light", "dark", "neutral", "forest", "green", "purple", "alien", "orange", "cyan",
    "deutan", "protan", "tritan",
];

const KIB: u64 = 1024;
const MIB: u64 = 1024 * 1024;
/// Speed limit presets in bytes per second (0: unlimited).
pub const LIMITS: [(&str, u64); 9] = [
    ("Unlimited", 0),
    ("256 KB/s", 256 * KIB),
    ("512 KB/s", 512 * KIB),
    ("1 MB/s", MIB),
    ("5 MB/s", 5 * MIB),
    ("10 MB/s", 10 * MIB),
    ("25 MB/s", 25 * MIB),
    ("50 MB/s", 50 * MIB),
    ("100 MB/s", 100 * MIB),
];

thread_local! {
    static OPEN: RefCell<Option<(Modal, Signal<String>)>> = const { RefCell::new(None) };
}

/// The catalog key naming a theme (the names the Python app gives the RemedyAI palettes).
fn theme_key(id: &str) -> Option<&'static str> {
    Some(match id {
        "system" => "System",
        "light" => "Light",
        "dark" => "Dark",
        "neutral" => "Neutral Dark",
        "forest" => "Dark Forest",
        "green" => "Emerald",
        "purple" => "Amethyst",
        "alien" => "Dark Purple",
        "orange" => "Amber",
        "cyan" => "Ocean",
        "deutan" => "Deutan",
        "protan" => "Protan",
        "tritan" => "Tritan",
        _ => return None,
    })
}

fn theme_name(ui: &Ui, id: &str) -> String {
    theme_key(id).map_or_else(|| id.to_owned(), |k| t(ui, k))
}

/// The autostart entry: this program, `--background`, and the profile when it is not the
/// default one.
pub fn startup_entry(h: &H) -> std::io::Result<autostart::Entry> {
    let root = h.core.root();
    let mut args = vec!["--background".to_owned()];
    if root != hearth_core::state::user_root() {
        args.push("--home".into());
        args.push(root.to_string_lossy().into_owned());
    }
    let refs: Vec<&str> = args.iter().map(String::as_str).collect();
    autostart::Entry::current("Hearth", "com.ahmi.hearth", &refs)
}

/// A settings page (`.settings-section`): its title over its parts. The last block has no
/// rule under it.
fn page(ui: &Ui, title: &str, parts: Vec<El>) -> El {
    if let Some(last) = parts
        .last()
        .filter(|p| p.info().is_some_and(|i| i.widget == "settings_block"))
    {
        let _ = last.clone().variant("last");
    }
    ui.el("settings_section")
        .column()
        .gap(12.0)
        .fill_width()
        .child(w::heading(ui, 3, t(ui, title)))
        .children(parts)
}

/// `.settings-block`: one setting, with a rule under it.
fn blk(ui: &Ui, parts: Vec<El>) -> El {
    ui.el("settings_block")
        .column()
        .gap(8.0)
        .fill_width()
        .children(parts)
}

/// A setting's name (a catalog key).
fn label(ui: &Ui, key: &str) -> El {
    w::text(ui, t(ui, key)).variant("label")
}

/// What a setting does (a catalog key).
fn hint(ui: &Ui, key: &str) -> El {
    w::text(ui, t(ui, key)).variant("hint")
}

/// A control at its own width in a block (a column stretches what it holds).
fn own_width(ui: &Ui, control: El) -> El {
    w::row(ui).child(control)
}

/// A drop-down drawn as the page drew its `<select>`: the value at the start, the arrow at
/// the end.
fn picker(select: El) -> El {
    select.variant("select").justify(Justify::SpaceBetween)
}

fn general(h: &H, ui: &Ui) -> El {
    let name = w::text_input(ui, t(ui, "PC name"));
    name.set_value(&h.core.settings().device_name);
    // Saved on Save, Enter, or leaving the field; a blank or unchanged name changes nothing.
    let save = {
        let (h, name) = (h.clone(), name.clone());
        move || {
            let typed = name.value();
            let typed = typed.trim();
            if typed.is_empty() || typed == h.core.settings().device_name {
                return;
            }
            h.core.set_device_name(typed);
            name.set_value(&h.core.settings().device_name);
            h.toast("PC name saved");
        }
    };
    let save = std::rc::Rc::new(save);
    let (s2, s3) = (save.clone(), save.clone());
    let name_el = name
        .on_submit(move || s2())
        .el()
        .on_focus_change(move |focused| {
            if !focused {
                s3();
            }
        })
        .grow(1.0);
    // Start at sign-in: the shortcut (or LaunchAgent, or autostart entry) is the setting.
    let on = Signal::new(startup_entry(h).is_ok_and(|e| autostart::enabled(&e)));
    let h2 = h.clone();
    let first = std::cell::Cell::new(true);
    let _ = Effect::new(move || {
        let want = on.get();
        if first.replace(false) {
            return;
        }
        untrack(|| {
            let r = startup_entry(&h2)
                .map_err(|e| e.to_string())
                .and_then(|e| autostart::set(&e, want).map_err(|e| e.to_string()));
            match r {
                Ok(()) if want => h2.toast("Hearth will start when you sign in"),
                Ok(()) => h2.toast("Removed the Startup shortcut"),
                Err(e) => {
                    h2.toast(e);
                    on.set(!want);
                }
            }
        });
    });
    let login = if cfg!(windows) {
        t(ui, "Start Hearth when I sign in to Windows")
    } else {
        t(ui, "Start Hearth when I sign in")
    };
    let themes = theme_picker(h, ui);
    // Language: the system's, or one of the catalogs'; it applies at once.
    let saved = hearth_core::state::normalize_language(&h.core.settings().language);
    let lang = Signal::new(
        saved
            .and_then(|c| LANGUAGES.iter().position(|(l, _)| *l == c))
            .map_or(0, |i| i + 1),
    );
    let h6 = h.clone();
    let first = std::cell::Cell::new(true);
    let _ = Effect::new(move || {
        let i = lang.get();
        if first.replace(false) {
            return;
        }
        untrack(|| {
            let code = i
                .checked_sub(1)
                .and_then(|i| LANGUAGES.get(i))
                .map_or("", |(c, _)| *c);
            if h6.core.settings().language != code
                && let Err(e) = h6.core.set_language(code)
            {
                h6.toast(e);
            }
        });
    });
    let mut languages = vec![t(ui, "System")];
    languages.extend(LANGUAGES.iter().map(|(_, native)| (*native).to_owned()));
    let (h4, u4) = (h.clone(), ui.clone());
    let reset = w::button(ui, t(ui, "Reset settings to defaults"), move || {
        let h5 = h4.clone();
        let _ = w::dialog(
            &u4,
            t(&u4, "Reset settings?"),
            t(
                &u4,
                "Reset theme, startup, download folder, update checks, bridge, and speed limits to defaults?",
            ),
            vec![
                DialogButton::new(t(&u4, "Reset"), "danger", move || {
                    h5.core.reset_settings();
                    if let Ok(e) = startup_entry(&h5) {
                        let _ = autostart::set(&e, false);
                    }
                    h5.toast("Settings restored to defaults");
                }),
                DialogButton::new(t(&u4, "Cancel"), "quiet", || {}),
            ],
        );
    });
    let mut startup = vec![w::check(ui, login, on)];
    if cfg!(windows) {
        startup.push(hint(
            ui,
            "Adds or removes a shortcut in your Startup folder. Hearth never writes a Registry Run key.",
        ));
    }
    page(
        ui,
        "General",
        vec![
            blk(
                ui,
                vec![
                    label(ui, "PC name"),
                    hint(
                        ui,
                        "This is how other PCs in the house see you. Changes apply as soon as you leave the field.",
                    ),
                    w::row(ui)
                        .gap(8.0)
                        .fill_width()
                        .child(name_el)
                        .child(w::button(ui, t(ui, "Save"), move || save())),
                ],
            ),
            blk(
                ui,
                vec![
                    label(ui, "Language"),
                    hint(
                        ui,
                        "Hearth uses this language right away. Your other settings, shares, and chat stay as they are.",
                    ),
                    own_width(
                        ui,
                        picker(w::select(ui, t(ui, "Language"), languages, lang))
                            .width(320.0)
                            .layout(|l| l.max_width = Length::Percent(1.0)),
                    ),
                ],
            ),
            blk(ui, startup),
            themes,
            font_picker(h, ui),
            blk(
                ui,
                vec![
                    reset,
                    hint(
                        ui,
                        "Restores theme, startup, download folder, update checks, bridge, and speed limits. Your home group and shared folders are left alone.",
                    ),
                ],
            ),
        ],
    )
}

/// The typeface: Remedy Sans UI, or another of Remedy's own. It applies at once.
fn font_picker(h: &H, ui: &Ui) -> El {
    let at = |id: &str| {
        crate::look::FONTS
            .iter()
            .position(|f| f.0 == id)
            .unwrap_or(0)
    };
    let idx = Signal::new(at(&h.core.settings().font));
    // The setting can change elsewhere too (Reset).
    let (st, core) = (h.settings, h.core.clone());
    let _ = Effect::new(move || {
        let _ = st.get();
        let now = at(&core.settings().font);
        untrack(|| idx.set_if_changed(now));
    });
    let h2 = h.clone();
    let first = std::cell::Cell::new(true);
    let _ = Effect::new(move || {
        let i = idx.get();
        if first.replace(false) {
            return;
        }
        untrack(|| {
            let id = crate::look::FONTS.get(i).map_or("", |f| f.0);
            if h2.core.settings().font != id {
                h2.core.set_font(id);
            }
        });
    });
    let names = crate::look::FONTS.iter().map(|f| f.2.to_owned()).collect();
    blk(
        ui,
        vec![
            label(ui, "Font"),
            hint(
                ui,
                "The typeface Hearth is drawn in. The change applies immediately.",
            ),
            own_width(
                ui,
                picker(w::select(ui, t(ui, "Font"), names, idx))
                    .width(320.0)
                    .layout(|l| l.max_width = Length::Percent(1.0)),
            ),
        ],
    )
}

/// What each palette is like, as the Python app's theme cards say it.
fn theme_note(id: &str) -> &'static str {
    match id {
        "system" => "Follows Windows",
        "light" => "Always light",
        "dark" => "Always dark",
        "neutral" => "Slate",
        "forest" => "RemedyAI default",
        "green" => "Green glow",
        "purple" => "Violet",
        "alien" => "Neon violet",
        "orange" => "Warm",
        "cyan" => "Teal",
        "deutan" => "Blue/teal safe",
        "protan" => "Teal/blue safe",
        "tritan" => "Rose/green safe",
        _ => "",
    }
}

/// The theme: System, Light or Dark side by side, then a card for each palette with its
/// background, accent and text colors, its name and what it is like.
fn theme_picker(h: &H, ui: &Ui) -> El {
    let chosen = Signal::new(h.core.settings().theme);
    // The setting can change elsewhere too (Reset).
    let (st, core) = (h.settings, h.core.clone());
    let _ = Effect::new(move || {
        let _ = st.get();
        let now = core.settings().theme;
        untrack(|| chosen.set_if_changed(now));
    });
    let h2 = h.clone();
    let pick = std::rc::Rc::new(move |id: &str| {
        if h2.core.settings().theme != id {
            h2.core.set_theme(id);
        }
        chosen.set_if_changed(id.to_owned());
    });
    const BASIC: [&str; 3] = ["system", "light", "dark"];
    let basic_i = Signal::new(BASIC.len());
    let _ = Effect::new(move || {
        let c = chosen.get();
        let i = BASIC.iter().position(|b| *b == c).unwrap_or(BASIC.len());
        untrack(|| basic_i.set_if_changed(i));
    });
    let p1 = pick.clone();
    let _ = Effect::new(move || {
        let i = basic_i.get();
        untrack(|| {
            if let Some(id) = BASIC.get(i) {
                p1(id);
            }
        });
    });
    let names: Vec<String> = BASIC.iter().map(|id| theme_name(ui, id)).collect();
    let refs: Vec<&str> = names.iter().map(String::as_str).collect();
    let basic = w::segmented(ui, t(ui, "Theme"), &refs, basic_i);
    // `.theme-grid`: two cards to a row, each a swatch beside the palette's name and note.
    let mut cards: Vec<El> = Vec::new();
    for id in THEMES.iter().filter(|id| !BASIC.contains(id)) {
        let Some(p) = remedy_ui_themes::palette(id) else {
            continue;
        };
        let colors: Vec<remedy_ui::core::Color> = [p.bg, p.accent, p.text]
            .iter()
            .filter_map(|c| remedy_ui::core::Color::from_hex(c).ok())
            .collect();
        let swatch = ui
            .el("swatch")
            .width(34.0)
            .height(22.0)
            .layout(|l| l.shrink = 0.0)
            .paint(move |cx| {
                use remedy_ui::paint::{Brush, Stop};
                let r = remedy_ui::core::Rect::new(0.0, 0.0, cx.size.width, cx.size.height);
                let n = colors.len().saturating_sub(1).max(1) as f32;
                let stops = colors
                    .iter()
                    .enumerate()
                    .map(|(i, c)| Stop {
                        offset: i as f32 / n,
                        color: *c,
                    })
                    .collect();
                cx.list.fill_rounded(
                    r,
                    remedy_ui::core::CornerRadii::all(7.0),
                    Brush::Linear {
                        start: remedy_ui::core::Point::new(0.0, 0.0),
                        end: remedy_ui::core::Point::new(cx.size.width, 0.0),
                        stops,
                    },
                );
            });
        let (name, note) = (theme_name(ui, id), t(ui, theme_note(id)));
        let p2 = pick.clone();
        let card = ui
            .el("theme_card")
            .row()
            .gap(10.0)
            .align(Align::Center)
            .grow(1.0)
            .layout(|l| {
                l.basis = Length::Px(0.0);
                l.min_width = Length::Px(0.0);
            })
            .on_click(move || p2(id))
            .child(swatch)
            .child(crate::look::meta(ui, name.clone(), note.clone()));
        let c2 = card.clone();
        cards.push(card.bind(move || {
            let on = chosen.get() == *id;
            c2.state(remedy_ui::style::States::SELECTED, on);
            let _ = c2.clone().semantics(
                remedy_ui_a11y::Semantics::new(remedy_ui_a11y::Role::Radio)
                    .label(format!("{name}, {note}"))
                    .checked(on)
                    .action(remedy_ui_a11y::Action::Click),
            );
        }));
    }
    let mut grid = w::column(ui).gap(8.0).fill_width().semantics(
        remedy_ui_a11y::Semantics::new(remedy_ui_a11y::Role::Group).label(t(ui, "Theme")),
    );
    let mut cards = cards.into_iter();
    while let Some(first) = cards.next() {
        let row = w::row(ui).gap(8.0).fill_width().child(first);
        // An odd card out keeps the width of half a row.
        grid = grid.child(match cards.next() {
            Some(second) => row.child(second),
            None => row.child(w::spacer(ui).layout(|l| l.basis = Length::Px(0.0))),
        });
    }
    blk(
        ui,
        vec![
            label(ui, "Theme"),
            hint(
                ui,
                "System follows your Windows light or dark mode. The palettes below use the same color schemes as RemedyAI. The change applies immediately.",
            ),
            own_width(ui, basic),
            grid,
        ],
    )
}

fn sharing(h: &H, ui: &Ui) -> El {
    let list = w::column(ui).gap(8.0).fill_width();
    let (l2, h2, u2, sh) = (list.clone(), h.clone(), ui.clone(), h.shares);
    let list = list.bind(move || {
        let _ = sh.get();
        let shares = h2.core.shares();
        untrack(|| {
            l2.clear();
            if shares.is_empty() {
                let _ = l2.clone().child(
                    w::text(
                        &u2,
                        t(
                            &u2,
                            "Nothing shared yet. Share a folder so the other PCs can see it.",
                        ),
                    )
                    .variant("hint"),
                );
            }
            for s in &shares {
                let mode = Signal::new(usize::from(s.writable));
                let (h3, id) = (h2.clone(), s.id.clone());
                let first = std::cell::Cell::new(true);
                let _ = Effect::new(move || {
                    let rw = mode.get() == 1;
                    if first.replace(false) {
                        return;
                    }
                    untrack(|| {
                        if let Err(e) = h3.core.update_share(&id, None, None, Some(rw)) {
                            h3.toast(e);
                        }
                    });
                });
                let (h4, u4, id4) = (h2.clone(), u2.clone(), s.id.clone());
                let shown = crate::i18n::place(&u2, &s.display_label());
                let (h5, u5, id5, l5) = (h2.clone(), u2.clone(), s.id.clone(), shown.clone());
                // `.settings-share`
                let row = u2
                    .el("settings_share")
                    .row()
                    .fill_width()
                    .gap(8.0)
                    .align(Align::Center)
                    .child(crate::look::meta(
                        &u2,
                        shown.clone(),
                        s.path.to_string_lossy().into_owned(),
                    ))
                    .child(
                        w::select(
                            &u2,
                            tf(&u2, "Permission for {name}", &[("name", shown.clone())]),
                            vec![t(&u2, "Read only"), t(&u2, "Read & write")],
                            mode,
                        )
                        .variant("select_small"),
                    )
                    .child(w::button(&u2, t(&u2, "Edit"), move || {
                        crate::share_dialog::open(
                            &h4,
                            &u4,
                            crate::share_dialog::Mode::Edit(id4.clone()),
                        )
                    }))
                    .child(
                        w::button(&u2, t(&u2, "Remove"), move || {
                            crate::files::confirm_unshare(&h5, &u5, &id5, &l5)
                        })
                        .variant("quiet"),
                    );
                let _ = l2.clone().child(row);
            }
        });
    });
    let (h6, u6, h7, u7) = (h.clone(), ui.clone(), h.clone(), ui.clone());
    let (core, st) = (h.core.clone(), h.settings);
    let dl = w::text(ui, "")
        .variant("readonly")
        .truncate()
        .data_text()
        .text(move || {
            let _ = st.get();
            core.download_dir().to_string_lossy().into_owned()
        });
    page(
        ui,
        "Sharing",
        vec![
            blk(
                ui,
                vec![
                    label(ui, "Folders shared with the house"),
                    hint(
                        ui,
                        "Edit a share to rename it, point it at a different folder, or change who can write. Read only lets others browse and download; read & write also lets them add files. Changes apply immediately.",
                    ),
                    list,
                    w::button(ui, t(ui, "Share a folder"), move || {
                        crate::share_dialog::open(&h6, &u6, crate::share_dialog::Mode::Share)
                    })
                    .variant("primary"),
                ],
            ),
            blk(
                ui,
                vec![
                    label(ui, "Default download folder"),
                    hint(
                        ui,
                        "Files you download from other PCs are saved here. Defaults to your Downloads folder.",
                    ),
                    w::row(ui)
                        .gap(8.0)
                        .fill_width()
                        .align(Align::Center)
                        .child(dl.grow(1.0).layout(|l| l.min_width = Length::Px(0.0)))
                        .child(w::button(ui, t(ui, "Browse…"), move || {
                            crate::share_dialog::open(&h7, &u7, crate::share_dialog::Mode::Download)
                        })),
                ],
            ),
        ],
    )
}

/// A speed limit picker: the presets, or Custom… with a number and a unit.
fn limit(h: &H, ui: &Ui, label: String, download: bool) -> El {
    let s = h.core.settings();
    let now = if download {
        s.download_limit
    } else {
        s.upload_limit
    };
    let custom_i = LIMITS.len();
    let idx = Signal::new(
        LIMITS
            .iter()
            .position(|(_, v)| *v == now)
            .unwrap_or(custom_i),
    );
    let mut options: Vec<String> = LIMITS.iter().map(|(n, _)| t(ui, n)).collect();
    options.push(t(ui, "Custom…"));
    let amount = w::text_input(ui, tf(ui, "{label} amount", &[("label", label.clone())]));
    let in_mb = now >= MIB && now % MIB == 0;
    amount.set_value(&if now == 0 {
        String::new()
    } else if in_mb {
        (now / MIB).to_string()
    } else {
        (now / KIB).max(1).to_string()
    });
    let unit_i = Signal::new(usize::from(in_mb));
    let apply = {
        let h = h.clone();
        move |bps: u64| {
            if download {
                h.core.set_limits(Some(bps), None);
            } else {
                h.core.set_limits(None, Some(bps));
            }
        }
    };
    let apply = std::rc::Rc::new(apply);
    let a2 = apply.clone();
    let first = std::cell::Cell::new(true);
    let _ = Effect::new(move || {
        let i = idx.get();
        if first.replace(false) {
            return;
        }
        untrack(|| {
            if let Some((_, v)) = LIMITS.get(i) {
                a2(*v);
            }
        });
    });
    let (a3, am, h3) = (apply.clone(), amount.clone(), h.clone());
    let set_custom = move || match am.value().trim().parse::<u64>() {
        Ok(n) if n >= 1 => {
            let mult = if unit_i.get_untracked().unwrap_or(0) == 1 {
                MIB
            } else {
                KIB
            };
            a3(n.saturating_mul(mult));
            h3.toast("Speed limit saved");
        }
        _ => h3.toast("Enter a whole number, 1 or more"),
    };
    let custom = w::row(ui)
        .gap(8.0)
        .align(Align::Center)
        .child(amount.el().width(120.0))
        .child(picker(w::select(
            ui,
            tf(ui, "{label} unit", &[("label", label.clone())]),
            vec![t(ui, "KB/s"), t(ui, "MB/s")],
            unit_i,
        )))
        .child(w::button(ui, t(ui, "Apply"), set_custom));
    let c2 = custom.clone();
    let custom = custom.bind(move || {
        let _ = c2.clone().hidden(idx.get() != custom_i);
    });
    // `.limit-row`: the presets, then the custom amount beside them when chosen.
    w::wrap(ui)
        .gap(8.0)
        .align(Align::Center)
        .child(
            picker(w::select(ui, label, options, idx)).layout(|l| l.min_width = Length::Px(180.0)),
        )
        .child(custom)
}

fn transfers(h: &H, ui: &Ui) -> El {
    page(
        ui,
        "Transfers",
        vec![
            hint(
                ui,
                "Speed limits apply immediately to transfers already running and to new ones. Unlimited is the default and is fastest on a home network.",
            ),
            blk(
                ui,
                vec![
                    label(ui, "Download speed limit"),
                    limit(h, ui, t(ui, "Download speed limit"), true),
                ],
            ),
            blk(
                ui,
                vec![
                    label(ui, "Upload speed limit"),
                    limit(h, ui, t(ui, "Upload speed limit"), false),
                ],
            ),
        ],
    )
}

fn network(h: &H, ui: &Ui) -> El {
    let body = w::column(ui).gap(12.0).fill_width();
    let (b2, h2, u2, g, hs) = (body.clone(), h.clone(), ui.clone(), h.group, h.settings);
    let body = body.bind(move || {
        let _ = g.get();
        let _ = hs.get();
        let group = h2.core.group();
        untrack(|| {
            b2.clear();
            let ui = &u2;
            let Some(group) = group else {
                let _ = b2
                    .clone()
                    .child(hint(ui, "Join or start a home group to get an invite."))
                    .child(hint(ui, "Join a group to add hosts."));
                return;
            };
            let invite = h2.core.invite().unwrap_or_default();
            let h3 = h2.clone();
            let copy = w::button(ui, t(ui, "Copy invite"), move || match h3.core.invite() {
                Some(i) => {
                    h3.app.copy(i, true);
                    h3.toast("Invite copied");
                }
                None => h3.toast("Join or start a home group first"),
            });
            let host = w::text_input(ui, t(ui, "Extra host"))
                .placeholder(t(ui, "192.168.0.12 or 192.168.1.8:7474"));
            let (h4, ho) = (h2.clone(), host.clone());
            let add = std::rc::Rc::new(move || {
                let v = ho.value();
                if v.trim().is_empty() {
                    h4.toast("Host required");
                    return;
                }
                h4.core.add_host(v.trim());
                ho.set_value("");
            });
            let a2 = add.clone();
            let host_el = host.on_submit(move || a2()).el().grow(1.0);
            let bridge = Signal::new(h2.core.settings().bridge);
            let h5 = h2.clone();
            let first = std::cell::Cell::new(true);
            let _ = Effect::new(move || {
                let on = bridge.get();
                if !first.replace(false) {
                    untrack(|| h5.core.set_bridge(on));
                }
            });
            let mut hosts = w::column(ui).fill_width();
            let known = h2.core.manual_hosts();
            if known.is_empty() {
                hosts = hosts.child(hint(ui, "No extra hosts yet."));
            }
            for k in known {
                let (h6, k2) = (h2.clone(), k.clone());
                hosts = hosts.child(
                    ui.el("share_card")
                        .row()
                        .gap(10.0)
                        .align(Align::Center)
                        .fill_width()
                        .child(w::text(ui, k.clone()).variant("name").data_text().grow(1.0))
                        .child(
                            w::icon_button(
                                ui,
                                w::Icon::Close,
                                tf(ui, "Forget {host}", &[("host", k.clone())]),
                                move || h6.core.remove_host(&k2),
                            )
                            .variant("quiet"),
                        ),
                );
            }
            let (h7, u7) = (h2.clone(), u2.clone());
            let leave = w::button(ui, t(ui, "Leave this hearth"), move || {
                let h8 = h7.clone();
                let _ = w::dialog(
                    &u7,
                    t(&u7, "Leave this hearth"),
                    format!(
                        "{} {}",
                        t(&u7, "Leave this hearth on this PC?"),
                        t(&u7, "Stops sharing with this home group on this PC. Folders on disk are not deleted.")
                    ),
                    vec![DialogButton::new(t(&u7, "Leave"), "danger", move || h8.core.leave_group()), DialogButton::new(t(&u7, "Cancel"), "quiet", || {})],
                );
            })
            .variant("danger");
            let ports = tf(
                ui,
                "This PC talks to others on TCP {peer} (files and chat) and UDP {beacon} (discovery). The installer opens those ports on the Windows Private profile.",
                &[
                    ("peer", h2.core.port().to_string()),
                    ("beacon", hearth_core::net::BEACON_PORT.to_string()),
                ],
            );
            let _ = b2.clone().children(vec![
                blk(
                    ui,
                    vec![
                        label(ui, "Invite — give this to another PC"),
                        hint(
                            ui,
                            "Paste this invite in Hearth on the other computer to join this home group.",
                        ),
                        w::text(ui, invite).variant("mono_box").data_text(),
                        own_width(ui, copy),
                    ],
                ),
                blk(
                    ui,
                    vec![
                        label(ui, "Password"),
                        hint(
                            ui,
                            "Needed only if you join from a nearby offer instead of an invite.",
                        ),
                        w::text(ui, group.password.clone())
                            .variant("mono_box")
                            .data_text(),
                    ],
                ),
                blk(
                    ui,
                    vec![
                        label(ui, "Extra host"),
                        hint(
                            ui,
                            "Sub-routers hide UDP broadcasts. Add the other PC's IP to join across subnets, or turn on Bridge on a machine that can see both networks.",
                        ),
                        w::row(ui)
                            .gap(8.0)
                            .fill_width()
                            .child(host_el)
                            .child(w::button(ui, t(ui, "Add host"), move || add())),
                        w::check(ui, t(ui, "Bridge discovery across subnets"), bridge),
                        label(ui, "Known extra hosts"),
                        hosts,
                    ],
                ),
                blk(
                    ui,
                    vec![label(ui, "Ports"), w::text(ui, ports).variant("hint")],
                ),
                blk(
                    ui,
                    vec![
                        leave,
                        hint(
                            ui,
                            "Stops sharing with this home group on this PC. Folders on disk are not deleted.",
                        ),
                    ],
                )
                .variant("last"),
            ]);
        });
    });
    page(ui, "Network", vec![body])
}

/// When updates were last looked for, on the local clock.
pub fn last_checked(at: f64, utc_offset: i32, cal: &CalendarSymbols) -> String {
    if at <= 0.0 {
        return "never".into();
    }
    let local = at as i64 + i64::from(utc_offset);
    let date = Date::from_days_since_epoch(local.div_euclid(86_400));
    let secs = local.rem_euclid(86_400);
    let time = Time::new((secs / 3600) as u8, ((secs % 3600) / 60) as u8)
        .map(|t| cal.format_time(t))
        .unwrap_or_default();
    format!("{} {time}", cal.long_date(date))
}

fn updates(h: &H, ui: &Ui) -> El {
    let (core, st, u2) = (h.core.clone(), h.settings, ui.clone());
    let checked = w::text(ui, "").variant("hint").text(move || {
        let _ = st.get();
        let cal = CalendarSymbols::for_locale(&u2.locale());
        let at = core.settings().last_update_check;
        if at <= 0.0 {
            t(&u2, "Last checked: never")
        } else {
            tf(
                &u2,
                "Last checked: {when}",
                &[("when", last_checked(at, u2.local_time().utc_offset, &cal))],
            )
        }
    });
    let auto = Signal::new(h.core.settings().auto_check_updates);
    let h2 = h.clone();
    let first = std::cell::Cell::new(true);
    let _ = Effect::new(move || {
        let on = auto.get();
        if !first.replace(false) {
            untrack(|| h2.core.set_auto_check_updates(on));
        }
    });
    let state = h.update;
    let u3 = ui.clone();
    let msg = w::text(ui, "").text(move || match state.get() {
        UpdateState::Idle => String::new(),
        UpdateState::Checking => t(&u3, "Checking for updates…"),
        UpdateState::UpToDate => t(&u3, "You're up to date"),
        UpdateState::Available(r) => tf(
            &u3,
            "Version {version} is available.",
            &[("version", r.version.trim_start_matches('v').to_owned())],
        ),
        UpdateState::Downloading(_) => t(&u3, "Downloading update…"),
        UpdateState::Ready => t(&u3, "Installing…"),
        UpdateState::Failed(e) => tr(&u3, &e),
        _ => String::new(),
    });
    let (h3, h4, h5) = (h.clone(), h.clone(), h.clone());
    let install = w::button(ui, t(ui, "Install update"), move || {
        crate::update::install(&h4)
    })
    .variant("primary");
    let i2 = install.clone();
    let install = install.bind(move || {
        let _ = i2
            .clone()
            .hidden(!matches!(state.get(), UpdateState::Available(_)));
    });
    page(
        ui,
        "Updates",
        vec![
            w::text(
                ui,
                tf(
                    ui,
                    "Current version: v{version}",
                    &[("version", crate::update::version().to_owned())],
                ),
            )
            .variant("hint"),
            checked,
            hint(
                ui,
                "Hearth checks GitHub Releases on startup and every few hours. Click Install update to download, verify, and apply a release. A single progress bar at the top of the window shows the download percent, then Installing…, then Hearth closes itself, installs in the background with no installer window, and reopens with your data intact.",
            ),
            msg,
            w::check(ui, t(ui, "Check for updates automatically"), auto),
            w::row(ui)
                .gap(8.0)
                .child(w::button(ui, t(ui, "Check now"), move || {
                    crate::update::check_now(&h3)
                }))
                .child(install),
            own_width(
                ui,
                w::link(ui, t(ui, "Release notes on GitHub"), move || {
                    h5.app.open_url(crate::update::RELEASES, |_| {})
                }),
            ),
        ],
    )
}

fn about(h: &H, ui: &Ui) -> El {
    let h2 = h.clone();
    // `.about-dl`: what it is beside its value.
    let row = |k: &str, v: El| {
        w::row(ui)
            .gap(8.0)
            .align(Align::Start)
            .child(
                w::text(ui, k.to_owned())
                    .variant("label")
                    .width(200.0)
                    .layout(|l| l.shrink = 0.0),
            )
            .child(v)
    };
    page(
        ui,
        "About",
        vec![
            w::text(
                ui,
                t(
                    ui,
                    "Hearth is one simple home for sharing - local file sharing and PC-to-PC chat, with no cloud.",
                ),
            )
            .line_height(remedy_ui::style::LineHeight::Relative(1.5)),
            // What Hearth is made with, as its page on GitHub says.
            own_width(ui, w::badge(ui, t(ui, "Built with RemedyUI"))),
            w::column(ui)
                .gap(10.0)
                .fill_width()
                .child(row(
                    &t(ui, "Version"),
                    w::text(ui, format!("v{}", crate::update::version())),
                ))
                .child(row(&t(ui, "License"), w::text(ui, "MIT")))
                .child(row(
                    "GitHub",
                    w::link(ui, "AhmiDarrow/Hearth", move || {
                        h2.app
                            .open_url("https://github.com/AhmiDarrow/Hearth", |_| {})
                    }),
                ))
                .child(row(
                    &t(ui, "Certificate thumbprint"),
                    w::text(ui, crate::update::THUMBPRINT)
                        .variant("mono")
                        .data_text(),
                )),
            w::heading(ui, 3, t(ui, "Help")),
            hint(
                ui,
                "Start a home group on one PC, then copy the invite from Settings → Network and paste it on the others. Share a folder with Browse…, or drag a folder onto “Shared from this PC”. Open a folder by clicking it, or press Enter. Download a file with Download, or drag it out of Hearth onto your desktop. Right-click a file for more actions. The menu in the title bar (or the gear) always opens Settings, even before you join a group.",
            ),
            w::text(ui, t(ui, "\"Hope this helps\" - Ahmi"))
                .variant("muted")
                .padding(Insets {
                    top: 6.0,
                    end: 0.0,
                    bottom: 0.0,
                    start: 0.0,
                }),
        ],
    )
}

/// Makes Settings again, in the language now in use, when it is open (at the section it
/// was showing).
pub fn reopen(h: &H, ui: &Ui) {
    let open = OPEN.with(|o| {
        o.borrow()
            .as_ref()
            .filter(|(m, _)| m.is_open())
            .map(|(m, s)| (m.clone(), *s))
    });
    if let Some((modal, section)) = open {
        let at = section
            .get_untracked()
            .unwrap_or_else(|_| "general".to_owned());
        modal.close();
        open_at(h, ui, &at);
    }
}

/// Opens Settings at `section` (or switches the open one to it).
pub fn open(h: &H, ui: &Ui, at: &str) {
    open_at(h, ui, at);
}

fn open_at(h: &H, ui: &Ui, at: &str) {
    let existing = OPEN.with(|o| {
        o.borrow()
            .as_ref()
            .filter(|(m, _)| m.is_open())
            .map(|(_, s)| *s)
    });
    if let Some(s) = existing {
        s.set(at.to_owned());
        return;
    }
    let current = Signal::new(at.to_owned());
    // `.settings-nav`
    let mut nav = ui
        .el("settings_nav")
        .column()
        .gap(2.0)
        .width(190.0)
        .layout(|l| l.shrink = 0.0);
    for (id, title) in SECTIONS {
        let b = w::button(ui, t(ui, title), move || current.set(id.to_owned()))
            .variant("nav")
            .justify(Justify::Start)
            .fill_width();
        let b2 = b.clone();
        nav = nav.child(b.bind(move || {
            let _ = b2
                .clone()
                .variant(if current.get() == id { "nav_on" } else { "nav" });
        }));
    }
    let pages: Vec<(&str, El)> = vec![
        ("general", general(h, ui)),
        ("sharing", sharing(h, ui)),
        ("transfers", transfers(h, ui)),
        ("network", network(h, ui)),
        ("updates", updates(h, ui)),
        ("about", about(h, ui)),
    ];
    let mut content = w::column(ui).fill_width();
    for (id, page) in pages {
        let p2 = page.clone();
        content = content.child(page.bind(move || {
            let _ = p2.clone().hidden(current.get() != id);
        }));
    }
    // `.settings-layout`: the sections beside the one shown, which scrolls.
    let body = w::row(ui)
        .grow(1.0)
        .fill_width()
        .layout(|l| l.min_height = Length::Px(0.0))
        .child(nav)
        .child(
            w::scroll(ui)
                .grow(1.0)
                .layout(|l| {
                    l.min_height = Length::Px(0.0);
                    l.min_width = Length::Px(0.0);
                })
                .child(content),
        );
    let close = || {
        let open = OPEN.with(|o| o.borrow().as_ref().map(|(m, _)| m.clone()));
        if let Some(m) = open {
            m.close();
        }
    };
    // `.settings-head`
    let head = ui
        .el("settings_head")
        .row()
        .fill_width()
        .align(Align::Center)
        .child(w::heading(ui, 2, t(ui, "Settings")).grow(1.0))
        .child(w::icon_button(ui, w::Icon::Close, t(ui, "Close"), close).variant("quiet"));
    let framed = w::column(ui)
        .grow(1.0)
        .fill_width()
        .layout(|l| l.min_height = Length::Px(0.0))
        .child(head)
        .child(body);
    let m = w::modal_fitted(ui, t(ui, "Settings"), framed, true);
    // `.settings-shell`: 920 by 640, or as much of that as the window has.
    let _ = m.body().variant("bare").gap(0.0).layout(|l| {
        l.width = Length::Px(920.0);
        l.height = Length::Px(640.0);
    });
    OPEN.with(|o| *o.borrow_mut() = Some((m, current)));
}

#[cfg(test)]
mod tests {
    #[test]
    fn last_checked_reads_as_a_local_date_and_time() {
        let cal = remedy_ui_services_i18n::CalendarSymbols::for_locale("en-GB");
        assert_eq!(super::last_checked(0.0, 0, &cal), "never");
        // 2026-11-18 09:30 UTC, one hour east.
        let at = 1_795_512_600.0;
        let s = super::last_checked(at, 3600, &cal);
        assert!(s.contains("2026") && s.ends_with("10:30"), "{s}");
    }

    #[test]
    fn every_theme_has_a_name_in_the_catalogs() {
        for t in super::THEMES {
            assert!(
                super::theme_key(t).is_some_and(crate::i18n::is_key),
                "{t} is named in the catalogs"
            );
            assert!(
                t == "system" || remedy_ui_themes::palette(t).is_some(),
                "{t} is a RemedyUI palette"
            );
        }
    }

    #[test]
    fn the_fixed_texts_are_catalog_keys() {
        for (_, title) in super::SECTIONS {
            assert!(crate::i18n::is_key(title), "{title}");
        }
        for (name, _) in super::LIMITS {
            assert!(crate::i18n::is_key(name), "{name}");
        }
    }
}
