//! The tray icon (the house mark) and its menu: Open, Settings, Check for updates, About, Quit.

use remedy_ui::host::{TrayEvent, TrayIcon, TrayImage, TrayItem};

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

/// Shows the tray icon and wires its menu.
pub fn install(h: &H) {
    let Some(image) = icon() else { return };
    let item = |id, label: &str| TrayItem::Item {
        id,
        label: label.to_owned(),
        enabled: true,
    };
    h.app.set_tray(Some(TrayIcon {
        tooltip: "Hearth".to_owned(),
        icon: image,
        menu: vec![
            item(OPEN, "Open"),
            item(SETTINGS, "Settings"),
            item(UPDATES, "Check for updates"),
            item(ABOUT, "About"),
            TrayItem::Separator,
            item(QUIT, "Quit"),
        ],
    }));
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
        TrayEvent::Menu(_) => {}
    });
}

#[cfg(test)]
mod tests {
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
