//! The transfers panel under the files: the last eight downloads and uploads, with progress,
//! speed and time left, Show (in the file manager), Cancel, Remove and Clear.

use hearth_core::transfers::{Job, Status};
use remedy_ui::core::Insets;
use remedy_ui::reactive::{Signal, untrack};
use remedy_ui::view::Align;
use remedy_ui::{El, Ui};
use remedy_ui_widgets_core::{self as w, Icon};

use crate::i18n::{t, tf, tr};
use crate::state::H;

/// How many transfers the panel shows.
pub const SHOWN: usize = 8;

/// Time left as 0.3.x words it: "12s left", "3m 5s left", "1h 2m left".
pub fn eta_text(secs: f64) -> String {
    let s = secs.max(0.0).round() as u64;
    if s < 60 {
        format!("{s}s left")
    } else if s < 3600 {
        format!("{}m {}s left", s / 60, s % 60)
    } else {
        format!("{}h {}m left", s / 3600, (s % 3600) / 60)
    }
}

/// The status line of a transfer, in the window's language.
pub fn status_text(ui: &Ui, j: &Job) -> String {
    match j.status {
        Status::Queued => t(ui, "Waiting…"),
        Status::Running => {
            let pct = if j.total > 0 {
                (j.bytes as f64 / j.total as f64 * 100.0).floor() as u64
            } else {
                0
            };
            let mut parts = vec![
                format!("{pct}%"),
                tf(
                    ui,
                    "{size}/s",
                    &[("size", crate::state::size_in(ui, j.speed.max(0.0) as u64))],
                ),
            ];
            if let Some(e) = j.eta {
                parts.push(tr(ui, &eta_text(e)));
            }
            parts.join(" · ")
        }
        Status::Done => t(ui, "Done"),
        Status::Canceled => t(ui, "Canceled"),
        Status::Error => {
            if j.message.is_empty() {
                t(ui, "Failed")
            } else {
                tr(ui, &j.message)
            }
        }
    }
}

fn row(h: &H, ui: &Ui, j: &Job) -> El {
    let unit = ui.theme().spacing.unit;
    let frac = Signal::new(match j.status {
        Status::Running if j.total > 0 => Some((j.bytes as f64 / j.total as f64) as f32),
        Status::Running | Status::Queued => None,
        _ => Some(1.0),
    });
    let mut actions = w::row(ui).gap(unit).align(Align::Center);
    if j.status == Status::Done
        && j.kind == "download"
        && let Some(dest) = j.dest.clone()
    {
        let h2 = h.clone();
        actions = actions.child(
            w::button(ui, t(ui, "Show"), move || {
                if remedy_ui_services_desktop::reveal(&dest).is_err() {
                    h2.toast_f("Saved to {path}", &[("path", dest.display().to_string())]);
                }
            })
            .variant("quiet"),
        );
    }
    let (h3, id) = (h.clone(), j.id.clone());
    actions = if j.status.finished() {
        actions.child(
            w::icon_button(ui, Icon::Close, t(ui, "Remove from list"), move || {
                if !h3.core.dismiss_transfer(&id) {
                    h3.toast("That transfer is still running");
                }
            })
            .variant("quiet"),
        )
    } else {
        actions.child(
            w::icon_button(ui, Icon::Close, t(ui, "Cancel"), move || {
                let _ = h3.core.cancel_transfer(&id);
            })
            .variant("quiet"),
        )
    };
    let icon = if j.kind == "upload" {
        Icon::Upload
    } else {
        Icon::Download
    };
    let variant = if j.status == Status::Error {
        "error"
    } else {
        "muted"
    };
    w::row(ui)
        .fill_width()
        .gap(unit * 2.0)
        .align(Align::Center)
        .child(w::icon(ui, icon, 18.0))
        .child(
            w::column(ui)
                .grow(1.0)
                .gap(unit / 2.0)
                .child(w::label(ui, j.name.clone(), "").nowrap().data_text())
                .child(
                    w::progress(
                        ui,
                        tf(ui, "{name} progress", &[("name", j.name.clone())]),
                        frac,
                    )
                    .fill_width(),
                )
                .child(w::label(ui, status_text(ui, j), variant).nowrap()),
        )
        .child(actions)
}

pub fn panel(h: &H, ui: &Ui) -> El {
    let unit = ui.theme().spacing.unit;
    let list = w::column(ui).gap(unit * 2.0).fill_width();
    let clear_btn = {
        let h2 = h.clone();
        w::button(ui, t(ui, "Clear downloads"), move || {
            h2.core.clear_transfers()
        })
        .variant("quiet")
    };
    let head = w::row(ui)
        .fill_width()
        .align(Align::Center)
        .child(w::label(ui, t(ui, "Downloads"), "strong").grow(1.0))
        .child(clear_btn.clone());
    let box_ = w::column(ui)
        .fill_width()
        .gap(unit * 2.0)
        .padding(Insets::all(unit * 3.0))
        .child(w::divider(ui, false))
        .child(head)
        .child(list.clone());
    let (b2, h3, u3, t) = (box_.clone(), h.clone(), ui.clone(), h.transfers);
    box_.bind(move || {
        let _ = t.get();
        let jobs = h3.core.transfers();
        untrack(|| {
            list.clear();
            let start = jobs.len().saturating_sub(SHOWN);
            for j in jobs.iter().skip(start).rev() {
                let _ = list.clone().child(row(&h3, &u3, j));
            }
            let _ = clear_btn
                .clone()
                .hidden(!jobs.iter().any(|j| j.status.finished()));
            let _ = b2.clone().hidden(jobs.is_empty());
        });
    })
}

#[cfg(test)]
mod tests {
    #[test]
    fn time_left_reads_like_0_3() {
        assert_eq!(super::eta_text(9.4), "9s left");
        assert_eq!(super::eta_text(185.0), "3m 5s left");
        assert_eq!(super::eta_text(3725.0), "1h 2m left");
    }
}
