//! The tray icon (the house mark) and its menu: Open, Settings, Check for updates, About, Quit.

use remedy_ui::host::{TrayEvent, TrayIcon, TrayImage, TrayItem};

use crate::i18n::t_in;
use crate::shell;
use crate::state::H;

const OPEN: u32 = 1;
const SETTINGS: u32 = 2;
const UPDATES: u32 = 3;
const ABOUT: u32 = 4;
const QUIT: u32 = 9;

/// The mark at tray size.
pub fn icon() -> Option<TrayImage> {
    let d = remedy_ui_media_image::decode(shell::MARK, Some((32, 32))).ok()?;
    (d.image.width == d.image.height).then(|| TrayImage {
        size: d.image.width,
        rgba: d.image.pixels.to_vec(),
    })
}

/// The language the menu is written in: the app's (chosen in Settings, or the system's),
/// which the tray hears without a window; before the system has said, the environment's.
fn language(h: &H) -> String {
    if let Some(l) = h.app.locale() {
        return l;
    }
    ["LC_ALL", "LC_MESSAGES", "LANG"]
        .iter()
        .filter_map(|k| std::env::var(k).ok())
        .find(|v| !v.is_empty())
        .map(|v| v.split(['.', '@']).next().unwrap_or_default().to_owned())
        .filter(|v| !v.is_empty() && v != "C" && v != "POSIX")
        .unwrap_or_else(|| "en".to_owned())
}

/// The menu in `locale`.
fn menu(locale: &str) -> Vec<TrayItem> {
    let item = |id, key: &str| TrayItem::Item {
        id,
        label: t_in(locale, key),
        enabled: true,
    };
    vec![
        item(OPEN, "Open"),
        item(SETTINGS, "Settings"),
        item(UPDATES, "Check for updates"),
        item(ABOUT, "About"),
        TrayItem::Separator,
        item(QUIT, "Quit"),
    ]
}

/// Sets the tray icon and its menu again, in the language now in use.
pub fn refresh(h: &H) {
    let Some(image) = icon() else { return };
    h.app
        .set_tray(Some(TrayIcon::new("Hearth", image, menu(&language(h)))));
}

/// Shows the tray icon and wires its menu.
pub fn install(h: &H) {
    refresh(h);
    // Written again in each language the app changes to, window or not.
    let h2 = h.clone();
    h.app.on_locale(move |_| refresh(&h2));
    let h = h.clone();
    h.app.clone().on_tray(move |e| match e {
        TrayEvent::Activate | TrayEvent::Menu(OPEN) => shell::show(&h),
        TrayEvent::Menu(SETTINGS) => shell::settings(&h, "general"),
        TrayEvent::Menu(UPDATES) => {
            shell::show(&h);
            crate::update::check_now(&h);
        }
        TrayEvent::Menu(ABOUT) => shell::settings(&h, "about"),
        TrayEvent::Menu(QUIT) => shell::quit(&h),
        _ => {}
    });
}

#[cfg(test)]
mod tests {
    use remedy_ui::host::TrayItem;

    #[test]
    fn the_menu_is_written_in_the_language_asked_for() {
        let labels = |locale: &str| -> Vec<String> {
            super::menu(locale)
                .into_iter()
                .filter_map(|i| match i {
                    TrayItem::Item { label, .. } => Some(label),
                    _ => None,
                })
                .collect()
        };
        assert_eq!(
            labels("en"),
            ["Open", "Settings", "Check for updates", "About", "Quit"]
        );
        let fr = labels("fr");
        assert_eq!(fr.get(1).map(String::as_str), Some("Réglages"));
        assert_eq!(fr.len(), 5);
        assert!(labels("ja").iter().all(|l| !l.is_empty()));
    }

    #[test]
    fn the_tray_icon_is_the_house_at_tray_size() {
        let i = super::icon().unwrap_or_else(|| unreachable!("the mark decodes"));
        assert_eq!(i.size, 32);
        assert_eq!(i.rgba.len(), 32 * 32 * 4);
        assert!(
            i.rgba.chunks(4).any(|p| p.get(3).is_some_and(|a| *a > 0)),
            "not blank"
        );
    }
}
