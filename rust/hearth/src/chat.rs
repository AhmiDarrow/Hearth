//! Chat with the house: the last 200 messages as bubbles (this PC's on the right), and a
//! composer. The view follows new messages while it is at the bottom, and stays put while
//! the user reads further up.

use std::cell::Cell;
use std::rc::Rc;

use remedy_ui::core::{Insets, Vec2};
use remedy_ui::layout::Length;
use remedy_ui::reactive::{Effect, Memo, Signal, untrack};
use remedy_ui::view::{Align, Justify};
use remedy_ui::{El, Ui};
use remedy_ui_services_i18n::{CalendarSymbols, Time};
use remedy_ui_widgets_core as w;
use serde_json::Value;

use crate::i18n::{t, tf};
use crate::state::H;

/// How many messages show.
pub const SHOWN: usize = 200;
/// Within this many pixels of the bottom, new messages scroll into view.
const FOLLOW: f32 = 40.0;

/// One message as the view shows it.
#[derive(Clone, Debug, PartialEq)]
pub struct Message {
    pub id: String,
    pub from: String,
    pub mine: bool,
    pub private: bool,
    pub text: String,
    pub ts: f64,
}

/// Messages from the engine's stored chat, oldest first, the last [`SHOWN`].
pub fn messages(raw: &[Value], me: &str) -> Vec<Message> {
    let start = raw.len().saturating_sub(SHOWN);
    raw.iter()
        .skip(start)
        .filter_map(|m| {
            let s = |k: &str| {
                m.get(k)
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_owned()
            };
            let text = s("text");
            (!text.is_empty()).then(|| Message {
                id: s("id"),
                from: s("from_name"),
                mine: s("from_id") == me,
                private: m.get("to").is_some_and(|t| !t.is_null()),
                text,
                ts: m.get("ts").and_then(Value::as_f64).unwrap_or(0.0),
            })
        })
        .collect()
}

/// A message's time on the local clock, as the locale writes it.
pub fn time_text(ts: f64, utc_offset: i32, cal: &CalendarSymbols) -> String {
    let local = (ts as i64 + i64::from(utc_offset)).rem_euclid(86_400);
    let t = Time::new((local / 3600) as u8, ((local % 3600) / 60) as u8);
    t.map(|t| cal.format_time(t)).unwrap_or_default()
}

fn bubble(ui: &Ui, m: &Message, cal: &CalendarSymbols) -> El {
    let unit = ui.theme().spacing.unit;
    let who = if m.private {
        format!("{}{}", m.from, t(ui, " → private"))
    } else {
        m.from.clone()
    };
    let when = time_text(m.ts, ui.local_time().utc_offset, cal);
    let b = w::card(ui)
        .layout(|l| l.max_width = Length::Percent(0.72))
        .child(
            w::column(ui)
                .gap(unit)
                .child(
                    w::row(ui)
                        .gap(unit * 2.0)
                        .child(w::text(ui, who).variant("strong").nowrap())
                        .child(w::text(ui, when).variant("muted").nowrap()),
                )
                .child(w::text(ui, m.text.clone()).data_text()),
        );
    let b = if m.mine { b.variant("accent") } else { b };
    w::row(ui)
        .fill_width()
        .justify(if m.mine { Justify::End } else { Justify::Start })
        .child(b)
}

pub fn chat(h: &H, ui: &Ui) -> El {
    let unit = ui.theme().spacing.unit;
    let list = w::column(ui)
        .gap(unit * 2.0)
        .fill_width()
        .padding(Insets::all(unit * 4.0));
    let scroller = w::scroll(ui)
        .grow(1.0)
        .fill_width()
        .layout(|l| l.min_height = Length::Px(0.0))
        .child(list.clone());
    let first = Rc::new(Cell::new(true));
    // Rebuilt only when the messages change: each build owns what it makes.
    let (core, c) = (h.core.clone(), h.chat);
    let shown = Memo::new(move || {
        let _ = c.get();
        messages(&core.chat(), &core.device_id())
    });
    let (u2, sc) = (ui.clone(), scroller.clone());
    let _ = list.bind(move || {
        let msgs = shown.get();
        untrack(|| {
            // At the bottom (or the first time): follow; reading higher up: stay.
            let at_bottom = match (sc.bounds(), list_height(&sc)) {
                (Some(view), Some(content)) => {
                    let off = u2.scroll_offset(sc.id()).y;
                    off + view.height() >= content - FOLLOW
                }
                _ => true,
            };
            let cal = CalendarSymbols::for_locale(&u2.locale());
            let l = sc.child_elements().into_iter().next();
            if let Some(l) = l {
                l.clear();
                if msgs.is_empty() {
                    let _ = l.clone().child(w::empty_state(
                        &u2,
                        t(&u2, "No messages"),
                        t(&u2, "No one has spoken yet. Say hello to the other rooms."),
                    ));
                }
                for m in &msgs {
                    let _ = l.clone().child(bubble(&u2, m, &cal));
                }
            }
            if first.replace(false) || at_bottom {
                sc.scroll_to(Vec2::new(0.0, f32::MAX / 4.0));
            }
        });
    });
    // The composer: to everyone, or privately to one PC that is online.
    let to: Signal<Option<String>> = Signal::new(None);
    let field =
        w::text_input(ui, t(ui, "Message the house…")).placeholder(t(ui, "Message the house…"));
    let send = {
        let (h3, f) = (h.clone(), field.clone());
        Rc::new(move || {
            let text = f.value();
            if text.trim().is_empty() {
                return;
            }
            let target = to.get_untracked().ok().flatten();
            match h3.core.send_chat(&text, target.as_deref()) {
                Ok(()) => f.set_value(""),
                Err(e) => h3.toast(e),
            }
        })
    };
    let s2 = send.clone();
    let field_el = field.on_submit(move || s2()).el().grow(1.0);
    let button = w::button(ui, t(ui, "Send"), move || send()).variant("primary");
    let picker = w::row(ui);
    let (p2, u4, peers, core) = (picker.clone(), ui.clone(), h.peers, h.core.clone());
    // Rebuilt only when who is online changes: the select's state belongs to its build.
    let online = Memo::new(move || {
        let _ = peers.get();
        core.members()
            .into_iter()
            .filter(|m| m.online && !m.is_self)
            .map(|m| (m.device_id, m.name))
            .collect::<Vec<(String, String)>>()
    });
    let picker = picker.bind(move || {
        let online = online.get();
        untrack(|| {
            // A PC that went offline can no longer be written to privately.
            let chosen = to.get_untracked().ok().flatten();
            let at = chosen
                .as_ref()
                .and_then(|c| online.iter().position(|(id, _)| id == c));
            if at.is_none() && chosen.is_some() {
                to.set(None);
            }
            let idx = Signal::new(at.map_or(0, |i| i + 1));
            let ids: Vec<String> = online.iter().map(|(id, _)| id.clone()).collect();
            let _ = Effect::new(move || {
                let i = idx.get();
                untrack(|| to.set_if_changed(i.checked_sub(1).and_then(|i| ids.get(i).cloned())));
            });
            let mut options = vec![t(&u4, "Everyone")];
            options.extend(
                online
                    .iter()
                    .map(|(_, n)| tf(&u4, "{name} (private)", &[("name", n.clone())])),
            );
            p2.clear();
            let _ = p2.clone().child(w::select(&u4, t(&u4, "To"), options, idx));
        });
    });
    w::column(ui)
        .grow(1.0)
        .fill_width()
        .layout(|l| l.min_height = Length::Px(0.0))
        .child(scroller)
        .child(
            w::row(ui)
                .fill_width()
                .gap(unit * 2.0)
                .align(Align::Center)
                .padding(Insets::all(unit * 3.0))
                .child(picker)
                .child(field_el)
                .child(button),
        )
}

fn list_height(scroller: &El) -> Option<f32> {
    scroller
        .child_elements()
        .first()
        .and_then(El::bounds)
        .map(|b| b.height())
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    #[test]
    fn messages_keep_the_last_200_and_know_whose_they_are() {
        let raw: Vec<_> = (0..205)
            .map(|i| json!({"id": format!("m{i}"), "from_id": if i % 2 == 0 { "me" } else { "den" }, "from_name": "Den", "to": null, "text": format!("hi {i}"), "ts": 0.0}))
            .collect();
        let v = super::messages(&raw, "me");
        assert_eq!(v.len(), 200);
        assert_eq!(v.first().map(|m| m.id.as_str()), Some("m5"));
        assert!(v.iter().filter(|m| m.mine).all(|m| {
            m.id.trim_start_matches('m')
                .parse::<u32>()
                .is_ok_and(|n| n % 2 == 0)
        }));
        let p = super::messages(
            &[
                json!({"id": "x", "from_id": "a", "from_name": "A", "to": "b", "text": "psst", "ts": 0}),
            ],
            "me",
        );
        assert!(p.first().is_some_and(|m| m.private));
    }

    #[test]
    fn times_are_local_and_in_the_locale_s_style() {
        let us = remedy_ui_services_i18n::CalendarSymbols::for_locale("en-US");
        let gb = remedy_ui_services_i18n::CalendarSymbols::for_locale("en-GB");
        // 13:05 UTC, two hours east.
        let ts = 13.0 * 3600.0 + 5.0 * 60.0;
        assert_eq!(super::time_text(ts, 7200, &gb), "15:05");
        // CLDR puts a narrow no-break space before the day period.
        assert_eq!(super::time_text(ts, 7200, &us), "3:05\u{202f}PM");
        assert_eq!(
            super::time_text(ts, -14 * 3600, &gb),
            "23:05",
            "the day before"
        );
    }
}
