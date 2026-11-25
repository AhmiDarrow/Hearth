//! Starting up, the window, and what is always on it: the menu, the brand, Settings, the
//! update banner; then the first-run screen or the house.

use std::rc::Rc;
use std::sync::Arc;

use hearth_core::Hearth;
use remedy_ui::core::Insets;
use remedy_ui::reactive::untrack;
use remedy_ui::view::Align;
use remedy_ui::{App, El, Ui, Window, WindowSpec};
use remedy_ui::image::{Fit, Source};
use remedy_ui::widgets::{self as w, MenuItem};

use crate::Launch;
use crate::i18n::{t, tf};
use crate::state::{H, Home};

/// The house mark the Python app ships.
pub const MARK: &[u8] = include_bytes!("../../../hearth/ui/static/hearth.png");

/// Starts the engine and the interface (`None` when the engine could not start; a window
/// says why).
pub fn start(app: &App, l: &Launch) -> Option<H> {
    // `--quit` with no Hearth running on this profile: there is nothing to quit.
    if l.quit {
        app.quit();
        return None;
    }
    let core = match Hearth::start(&l.home, l.port) {
        Ok(c) => c,
        Err(e) => {
            let msg = e.to_string();
            crate::log::line(&l.home, &format!("Hearth could not start: {msg}"));
            app.set_locale(crate::i18n::chosen(""));
            let a = app.clone();
            app.open(
                WindowSpec::new("Hearth").size(480.0, 200.0),
                move |ui, _| {
                    let a2 = a.clone();
                    ui.root().child(
                        w::column(ui)
                            .gap(12.0)
                            .padding(Insets::all(24.0))
                            .child(
                                w::text(
                                    ui,
                                    tf(
                                        ui,
                                        "Hearth could not start: {message}",
                                        &[("message", msg.clone())],
                                    ),
                                )
                                .variant("error"),
                            )
                            .child(
                                w::button(ui, t(ui, "Quit"), move || a2.quit()).variant("danger"),
                            ),
                    );
                },
            );
            return None;
        }
    };
    let root = core.root();
    crate::log::line(
        &root,
        &format!(
            "Hearth {}  ·  {}\n  Port   {}\n  Data   {}",
            crate::update::version(),
            core.settings().device_name,
            core.port(),
            root.display()
        ),
    );
    let h = Home::new(app, core);
    *h.background.borrow_mut() = l.background;
    remember_launch(&h);
    remember_ui(&h, !l.background);
    app.keep_running(true);
    follow_theme(&h);
    // The release checks run hidden: no tray icon either.
    if !crate::ci() {
        crate::tray::install(&h);
    }
    follow_locale(&h);
    crate::update::start(&h, l.updated.clone());
    if !l.background {
        open(&h);
    }
    if l.install_update {
        crate::update::install_now(&h);
    }
    for arg in &l.open {
        opened_with(&h, arg);
    }
    let h2 = h.clone();
    app.on_second_instance(move |args| {
        // What the second launch asked for: quit, install an update, or show the window
        // (and join with an invite it was given).
        let asked = crate::launch(args.iter().cloned());
        if asked.quit {
            quit(&h2);
            return;
        }
        if asked.install_update {
            crate::update::install_now(&h2);
            return;
        }
        if !crate::ci() {
            show(&h2);
        }
        for a in &asked.open {
            opened_with(&h2, a);
        }
    });
    Some(h)
}

/// Records how this profile was started next to an installed Hearth.exe, so Setup restarts it
/// with the same profile and port after an update (as 0.4 does).
pub fn remember_launch(h: &H) {
    let Some(dir) = hearth_core::relaunch::install_dir() else {
        return;
    };
    let root = h.core.root();
    let argv = hearth_core::relaunch::argv_for_launch(&root, h.core.port(), *h.background.borrow());
    if let Err(e) = hearth_core::relaunch::write_relaunch_args(&dir, &argv, &root) {
        crate::log::line(
            &root,
            &format!("Could not record the launch for updates: {e}"),
        );
    }
}

/// Remembers whether the window is open, so an update brings it back as it was (the hidden
/// CI never does).
pub fn remember_ui(h: &H, window: bool) {
    if crate::ci() {
        return;
    }
    let mode = if window {
        hearth_core::relaunch::UiMode::Window
    } else {
        hearth_core::relaunch::UiMode::Tray
    };
    let _ = hearth_core::relaunch::write_ui_mode(&h.core.root(), mode);
}

/// Hearth started with an argument: an invite joins when this PC is in no group.
fn opened_with(h: &H, arg: &str) {
    if h.core.group().is_some() || hearth_core::invite::parse(arg).is_err() {
        return;
    }
    match h.core.join_invite(arg) {
        Ok(g) => h.toast_f("Joined {name}", &[("name", g.name)]),
        Err(e) => h.toast(e),
    }
}

/// Keeps the theme as chosen: a palette, or the system's light or dark.
pub fn follow_theme(h: &H) {
    // The theme last applied, so a settings change that is not about the theme restyles nothing.
    let applied: Rc<std::cell::RefCell<String>> = Rc::default();
    let apply = {
        let (h, applied) = (h.clone(), applied.clone());
        Rc::new(move |dark: Option<bool>, hc: bool| {
            let theme = chosen_theme(&h, dark, hc);
            *applied.borrow_mut() = look_key(&theme);
            h.app.set_theme(theme);
        })
    };
    let a2 = apply.clone();
    h.app.on_appearance(move |dark, hc| a2(dark, hc));
    // And when the setting itself changes.
    let (s, h2) = (h.settings, h.clone());
    let _ = remedy_ui::reactive::Effect::new(move || {
        let _ = s.get();
        untrack(|| {
            let (dark, hc) = h2.app.appearance().unwrap_or((None, false));
            if look_key(&chosen_theme(&h2, dark, hc)) != *applied.borrow() {
                apply(dark, hc);
            }
        });
    });
}

/// The theme and typeface chosen in Settings.
fn chosen_theme(h: &H, dark: Option<bool>, high_contrast: bool) -> remedy_ui::style::Theme {
    let s = h.core.settings();
    crate::look::with_font(theme_for(&s.theme, dark, high_contrast), &s.font)
}

/// What tells one applied look from another: the palette and the typeface.
fn look_key(t: &remedy_ui::style::Theme) -> String {
    format!("{}|{}", t.name, t.font_families.join(","))
}

/// Keeps the app's language as chosen in Settings (or `HEARTH_LANG`), or the system's.
pub fn follow_locale(h: &H) {
    // What was last handed to the app, so a settings change that is not about the language
    // changes nothing.
    let applied: Rc<std::cell::RefCell<Option<Option<String>>>> = Rc::default();
    // The first run only records what the app already starts with.
    *applied.borrow_mut() = Some(crate::i18n::chosen(&h.core.settings().language));
    h.app.set_locale(applied.borrow().clone().flatten());
    let (s, h2) = (h.settings, h.clone());
    let _ = remedy_ui::reactive::Effect::new(move || {
        let _ = s.get();
        untrack(|| {
            let want = crate::i18n::chosen(&h2.core.settings().language);
            if applied.borrow().as_ref() != Some(&want) {
                *applied.borrow_mut() = Some(want.clone());
                h2.app.set_locale(want);
            }
        });
    });
}

/// The theme for a Hearth theme id ("system" follows the OS light or dark).
pub fn theme_for(id: &str, dark: Option<bool>, high_contrast: bool) -> remedy_ui::style::Theme {
    let choice = if id == "system" || id.is_empty() {
        remedy_ui::themes::ThemeChoice::System
    } else {
        remedy_ui::themes::ThemeChoice::Palette(id.to_owned())
    };
    crate::look::with_font(crate::look::theme(choice.resolve(dark, high_contrast)), "")
}

/// Opens (or shows) the window.
pub fn open(h: &H) {
    let spec = WindowSpec::new("Hearth")
        .size(1280.0, 800.0)
        .min_size(720.0, 520.0)
        .hide_on_close()
        .persist_geometry("main");
    // The house in the title bar, the taskbar and Alt+Tab, as in the tray.
    let spec =
        match crate::tray::icon().and_then(|i| remedy_ui::host::WindowIcon::new(i.size, i.rgba)) {
            Some(icon) => spec.icon(icon),
            None => spec,
        };
    let h2 = h.clone();
    let win = h.app.open(spec, move |ui, win| build(&h2, ui, win));
    let h3 = h.clone();
    win.on_visibility(move |shown| remember_ui(&h3, shown));
    remember_ui(h, true);
    *h.window.borrow_mut() = Some(win);
    h.flush_toasts();
}

/// Shows and raises the window, opening it when it was closed.
pub fn show(h: &H) {
    let w = h.window.borrow().clone();
    match w {
        Some(w) if w.is_open() => {
            w.set_visible(true);
            w.set_minimized(false);
            w.focus();
        }
        _ => open(h),
    }
}

/// Opens Settings at `section` (in the window, which shows first).
pub fn settings(h: &H, section: &str) {
    show(h);
    let ui = h.ui.borrow().clone();
    if let Some(ui) = ui {
        crate::settings::open(h, &ui, section);
    }
}

/// The house mark, `size` wide.
pub fn mark(ui: &Ui, size: f32) -> El {
    remedy_ui::image::image(ui, Source::Bytes(Arc::from(MARK)), "", Fit::Contain)
        .width(size)
        .height(size)
}

fn chrome(h: &H, ui: &Ui) -> El {
    let menu_btn = w::icon_button(ui, w::Icon::Menu, t(ui, "Menu"), || {}).variant("quiet");
    let (h1, u1, m1) = (h.clone(), ui.clone(), menu_btn.clone());
    let menu_btn = menu_btn.on_click(move || {
        let (a, b, c, d, e) = (h1.clone(), h1.clone(), h1.clone(), h1.clone(), h1.clone());
        let _ = w::menu(
            &u1,
            &m1,
            vec![
                MenuItem::new(t(&u1, "Settings"), move || settings(&a, "general")),
                MenuItem::new(t(&u1, "Check for updates"), move || {
                    crate::update::check_now(&b)
                }),
                MenuItem::new(t(&u1, "About"), move || settings(&c, "about")),
                MenuItem::new(t(&u1, "Help"), move || settings(&d, "about")),
                MenuItem::new(t(&u1, "Quit"), move || quit(&e)).danger(),
            ],
        );
    });
    let title = w::text(ui, "Hearth").variant("chrome").nowrap();
    let (t2, core, g) = (title.clone(), h.core.clone(), h.group);
    let title = title.bind(move || {
        let _ = g.get();
        let name = core.group().map_or_else(|| "Hearth".to_owned(), |g| g.name);
        let _ = t2.clone().text(remedy_ui::view::RichText::plain(name));
    });
    let h2 = h.clone();
    ui.el("chrome")
        .row()
        .fill_width()
        .align(Align::Center)
        .gap(8.0)
        .child(menu_btn)
        .child(mark(ui, 22.0))
        .child(title.grow(1.0))
        .child(
            w::icon_button(ui, w::Icon::Settings, t(ui, "Settings"), move || {
                settings(&h2, "general")
            })
            .variant("quiet"),
        )
}

/// A dialog's head, as the Python app draws it: its title and a close button.
pub fn dialog_head(ui: &Ui, title: &str, close: impl Fn() + 'static) -> El {
    ui.el("modal_head")
        .row()
        .fill_width()
        .align(Align::Center)
        .gap(12.0)
        .child(w::heading(ui, 2, title.to_owned()).truncate().grow(1.0))
        .child(w::icon_button(ui, w::Icon::Close, t(ui, "Close"), close).variant("quiet"))
}

/// Quits Hearth (the engine stops with the process).
pub fn quit(h: &H) {
    h.app.quit();
}

fn build(h: &H, ui: &Ui, win: &Window) {
    *h.ui.borrow_mut() = Some(ui.clone());
    let content = w::column(ui)
        .grow(1.0)
        .fill_width()
        .layout(|l| l.min_height = remedy_ui::layout::Length::Px(0.0));
    let (c2, h2, u2) = (content.clone(), h.clone(), ui.clone());
    let content = content.bind(move || {
        let _ = h2.group.get();
        // A new language rebuilds every label.
        let _ = u2.locale();
        let joined = h2.core.group().is_some();
        untrack(|| {
            c2.clear();
            let _ = c2.clone().child(if joined {
                crate::files::main(&h2, &u2)
            } else {
                crate::gate::gate(&h2, &u2)
            });
        });
    });
    // The strip above the content (the menu, the brand, the update banner) is made again in
    // the new language too.
    let top = w::column(ui).fill_width();
    let (t2, h4, u4) = (top.clone(), h.clone(), ui.clone());
    let top = top.bind(move || {
        let _ = u4.locale();
        untrack(|| {
            t2.clear();
            let _ = t2
                .clone()
                .child(chrome(&h4, &u4))
                .child(crate::update::banner(&h4, &u4));
        });
    });
    // Settings, open, is rebuilt in the new language.
    let (h5, u5, first) = (h.clone(), ui.clone(), std::cell::Cell::new(true));
    let _ = remedy_ui::reactive::Effect::new(move || {
        let _ = u5.locale();
        untrack(|| {
            if !first.replace(false) {
                // Outside this effect: what Settings makes must outlive its next run.
                let (h6, u6) = (h5.clone(), u5.clone());
                h5.app.after(std::time::Duration::ZERO, move || {
                    crate::settings::reopen(&h6, &u6)
                });
            }
        });
    });
    let h3 = h.clone();
    let root = w::column(ui)
        .grow(1.0)
        .fill_width()
        .layout(|l| l.min_height = remedy_ui::layout::Length::Px(0.0))
        .child(top)
        .child(content)
        .on_key(move |e| {
            // Ctrl+, opens Settings anywhere (Command+, on Apple platforms).
            let m = e.modifiers;
            if e.pressed
                && (m.ctrl || m.meta)
                && e.key == remedy_ui::input::Key::Character(",".into())
            {
                settings(&h3, "general");
                return true;
            }
            false
        });
    ui.root().child(root);
    let _ = win;
    h.flush_toasts();
}
