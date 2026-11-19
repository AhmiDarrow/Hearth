//! Updates from GitHub releases, as Hearth 0.3.x does them: prereleases included, the
//! installer trusted by the published SHA256SUMS.txt and by its Authenticode signer, checked
//! 20 s after start and every 4 h (backing off after failures), installed only on a click.

use std::time::Duration;

use remedy_ui::{El, Ui};
use remedy_ui_services_update::{self as up, Apply, Feed, Trust, UpdateConfig, UpdateState};

use crate::i18n::{t, tf, tr};
use crate::state::H;

/// What the strip says when the installer could not run (a catalog key).
const INSTALL_FAILED: &str = "The in-app updater could not finish ({detail}). The release download page is opening so you can install the update yourself.";

/// The release signing certificate's SHA-1 thumbprint.
pub const THUMBPRINT: &str = "C7B807D542B3BA7207AABED960F556B4DC85C02F";
/// Where releases are listed.
pub const RELEASES: &str = "https://github.com/AhmiDarrow/Hearth/releases";
const FIRST_CHECK: Duration = Duration::from_secs(20);
const EVERY: Duration = Duration::from_secs(4 * 3600);
const INSTALLER_ARGS: [&str; 6] = [
    "/VERYSILENT",
    "/SUPPRESSMSGBOXES",
    "/NORESTART",
    "/CLOSEAPPLICATIONS",
    "/FORCECLOSEAPPLICATIONS",
    "/CURRENTUSER",
];

/// This build's version.
pub fn version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

/// The update settings: GitHub releases of AhmiDarrow/Hearth (`HEARTH_RELEASES_API` points the
/// feed at another server, for tests).
pub fn config() -> UpdateConfig {
    let feed = Feed::GitHubAll {
        owner: "AhmiDarrow".into(),
        repo: "Hearth".into(),
        asset: "HearthSetup.exe".into(),
    };
    let mut cfg = UpdateConfig::with_trust(
        version(),
        feed,
        Trust::Checksums {
            file: "SHA256SUMS.txt".into(),
            publisher: Some(THUMBPRINT.into()),
        },
    );
    if let Ok(api) = std::env::var("HEARTH_RELEASES_API") {
        cfg.github_api = api;
    }
    cfg
}

/// The wait before the next automatic check after `failures` failed ones in a row.
pub fn next_check(failures: u32) -> Duration {
    if failures == 0 {
        return EVERY;
    }
    let step = Duration::from_secs(15 * 60).saturating_mul(1u32 << (failures - 1).min(16));
    step.min(EVERY)
}

/// Starts the automatic checks; `updated` is set when the installer restarted Hearth.
pub fn start(h: &H, updated: Option<String>) {
    if let Some(v) = updated {
        let v = if v.is_empty() {
            version().to_owned()
        } else {
            v.trim_start_matches('v').to_owned()
        };
        h.toast_f("Updated to v{version}", &[("version", v)]);
    }
    schedule(h, FIRST_CHECK);
}

fn schedule(h: &H, after: Duration) {
    let h2 = h.clone();
    h.app.after(after, move || {
        if h2.core.settings().auto_check_updates {
            check(&h2, false);
        } else {
            schedule(&h2, EVERY);
        }
    });
}

/// Checks now (from the menu, the tray or Settings): the result is always shown.
pub fn check_now(h: &H) {
    check(h, true);
}

fn check(h: &H, by_hand: bool) {
    let busy = matches!(
        h.update.get_untracked().ok(),
        Some(UpdateState::Checking | UpdateState::Downloading(_))
    );
    if busy {
        return;
    }
    // An automatic check stays quiet until it finds something.
    if by_hand {
        h.update.set(UpdateState::Checking);
    }
    let h2 = h.clone();
    h.app.background(
        || up::check(&config()),
        move |r| {
            h2.core.set_last_update_check(hearth_core::engine::now());
            let automatic = !by_hand;
            match r {
                Ok(Some(rel)) => {
                    h2.update_failures.set(0);
                    *h2.release.borrow_mut() = Some(rel.clone());
                    h2.update.set(UpdateState::Available(rel));
                }
                Ok(None) => {
                    h2.update_failures.set(0);
                    h2.release.borrow_mut().take();
                    h2.update.set(if by_hand {
                        UpdateState::UpToDate
                    } else {
                        UpdateState::Idle
                    });
                }
                Err(e) => {
                    h2.update_failures
                        .set(h2.update_failures.get().saturating_add(1));
                    h2.update.set(if by_hand {
                        UpdateState::Failed(e.to_string())
                    } else {
                        UpdateState::Idle
                    });
                }
            }
            if automatic {
                schedule(&h2, next_check(h2.update_failures.get()));
            }
        },
    );
}

/// Downloads, verifies and runs the installer; Hearth quits so it can be replaced.
pub fn install(h: &H) {
    let Some(rel) = h.release.borrow().clone() else {
        return;
    };
    h.update.set(UpdateState::Downloading(None));
    let (h2, state) = (h.clone(), h.update);
    let waker = remedy_ui::reactive::waker().ok();
    let dir = std::env::temp_dir().join("Hearth-update");
    let tag = rel.version.clone();
    h.app.background(
        move || {
            up::download(&config(), &rel, &dir, |got, total| {
                if let (Some(w), Some(t)) = (&waker, total.filter(|t| *t > 0)) {
                    let f = (got as f64 / t as f64) as f32;
                    let _ = w.post(move || {
                        let _ = state.try_set(UpdateState::Downloading(Some(f)));
                    });
                }
            })
        },
        move |r| {
            let run = r.and_then(|file| {
                h2.update.set(UpdateState::Ready);
                // What Setup needs to bring this profile back as it is now.
                let shown = h2
                    .window
                    .borrow()
                    .as_ref()
                    .is_some_and(|w| w.is_open() && w.is_visible());
                crate::shell::remember_ui(&h2, shown);
                crate::shell::remember_launch(&h2);
                crate::log::line(
                    &h2.core.root(),
                    &format!("Installing Hearth {tag} from {}", file.display()),
                );
                let mut args: Vec<String> =
                    INSTALLER_ARGS.iter().map(|a| (*a).to_owned()).collect();
                // Into the folder this copy runs from, unless that is Program Files or not
                // writable (then Setup's own default), as 0.4 decides.
                if let Some(dir) = hearth_core::relaunch::update_dir() {
                    args.push(format!("/DIR={}", dir.display()));
                }
                up::apply(&file, &Apply::RunInstaller(args))
            });
            match run {
                Ok(()) => h2.app.quit(),
                Err(e) => {
                    let detail = e.to_string();
                    crate::log::line(
                        &h2.core.root(),
                        &format!("The update could not finish: {detail}"),
                    );
                    h2.update
                        .set(UpdateState::Failed(remedy_ui_services_i18n::fill(
                            INSTALL_FAILED,
                            &[("detail", detail)],
                        )));
                    h2.app.open_url(format!("{RELEASES}/tag/{tag}"), |_| {});
                }
            }
        },
    );
}

/// The strip under the chrome: what an update is doing, with Install, in the window's
/// language. Hearth quits as its installer starts, so there is no Restart button.
pub fn banner(h: &H, ui: &Ui) -> El {
    let unit = ui.theme().spacing.unit;
    let u = ui.clone();
    let mut texts = up::BannerText::default();
    texts.checking = t(ui, "Checking for updates…");
    texts.up_to_date = t(ui, "You're up to date");
    texts.available = tf(
        ui,
        "Version {version} is available.",
        &[("version", "{version}".to_owned())],
    );
    texts.downloading = t(ui, "Downloading update…");
    texts.ready = t(ui, "Installing…");
    texts.install = t(ui, "Install update");
    texts.restart = None;
    texts.progress = t(ui, "Downloading update…");
    texts.failure = std::rc::Rc::new(move |e: &str| tr(&u, e));
    let h1 = h.clone();
    up::update_banner_with(ui, h.update, &texts, move || install(&h1), || {})
        .padding(remedy_ui::core::Insets::symmetric(unit, unit * 4.0))
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    #[test]
    fn failed_checks_back_off_like_0_3() {
        assert_eq!(super::next_check(0), Duration::from_secs(4 * 3600));
        assert_eq!(super::next_check(1), Duration::from_secs(15 * 60));
        assert_eq!(super::next_check(2), Duration::from_secs(30 * 60));
        assert_eq!(super::next_check(5), Duration::from_secs(4 * 3600));
        assert_eq!(super::next_check(40), Duration::from_secs(4 * 3600));
    }

    #[test]
    fn the_failure_text_is_a_catalog_key() {
        assert!(crate::i18n::is_key(super::INSTALL_FAILED));
    }

    #[test]
    fn releases_are_trusted_by_checksums_and_the_signer() {
        let c = super::config();
        assert!(
            matches!(c.trust, remedy_ui_services_update::Trust::Checksums { publisher: Some(ref p), .. } if p == super::THUMBPRINT)
        );
    }
}
