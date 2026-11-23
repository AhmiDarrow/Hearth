//! Before joining: name this PC, join a group found nearby (the password is checked with the
//! PC that offered it first), start a new group, or join with an invite.

use std::rc::Rc;

use hearth_core::Offer;
use remedy_ui::core::Insets;
use remedy_ui::layout::Length;
use remedy_ui::reactive::{Signal, untrack};
use remedy_ui::view::{Align, Justify};
use remedy_ui::{El, TextField, Ui};
use remedy_ui_widgets_core as w;

use crate::i18n::{t, tf, tr};
use crate::state::H;

/// Saves the name typed for this PC (blank keeps the current one).
fn save_name(h: &H, name: &TextField) {
    let n = name.value().trim().to_owned();
    if !n.is_empty() && n != h.core.settings().device_name {
        h.core.set_device_name(&n);
    }
}

/// A section that opens and closes (the page's `<details class="more">`).
pub fn details(ui: &Ui, title: &str, body: El) -> El {
    w::disclosure(ui, title.to_owned(), body, Signal::new(false)).fill_width()
}

fn offer_card(h: &H, ui: &Ui, o: &Offer, name: &TextField, busy: Signal<bool>) -> El {
    let unit = ui.theme().spacing.unit;
    let group = if o.group_name.trim().is_empty() {
        t(ui, "Home")
    } else {
        o.group_name.clone()
    };
    let pw = w::secret_input(
        ui,
        tf(
            ui,
            "Group password for {group}",
            &[("group", group.clone())],
        ),
    )
    .placeholder(t(ui, "Group password"));
    let err: Signal<Option<String>> = Signal::new(None);
    let join: Rc<dyn Fn()> = {
        let (h, o, pw, name, u) = (h.clone(), o.clone(), pw.clone(), name.clone(), ui.clone());
        Rc::new(move || {
            if busy.get_untracked().unwrap_or(false) {
                return;
            }
            let secret = pw.take_secret();
            let password = secret.expose().to_owned();
            if password.is_empty() {
                err.set(Some(t(&u, "Enter the group password")));
                return;
            }
            if o.salt.is_none() {
                err.set(Some(t(
                    &u,
                    "This group is too old to one-click join. Paste an invite instead.",
                )));
                return;
            }
            err.set(None);
            busy.set(true);
            save_name(&h, &name);
            let (core, offer, p2) = (h.core.clone(), o.clone(), password.clone());
            let (h2, o2, u2) = (h.clone(), o.clone(), u.clone());
            h.run(
                async move { hearth_core::client::check_password(&core, &offer, &p2).await },
                move |r| {
                    busy.set(false);
                    match r {
                        Ok(true) => match h2.core.join_offer(&o2, &password) {
                            Ok(()) => {
                                let n = if o2.group_name.trim().is_empty() {
                                    t(&u2, "the group")
                                } else {
                                    o2.group_name.clone()
                                };
                                h2.toast_f("Joined {name}", &[("name", n)]);
                            }
                            Err(e) => err.set(Some(tr(&u2, &e))),
                        },
                        Ok(false) => {
                            err.set(Some(t(&u2, "Could not join. Check the password.")));
                        }
                        Err(e) => err.set(Some(tr(&u2, &e))),
                    }
                },
            );
        })
    };
    let j2 = join.clone();
    let pw_el = pw.on_submit(move || j2()).el().grow(1.0);
    let go = w::button(ui, t(ui, "Join"), move || join()).variant("primary");
    let go2 = go.clone();
    let go = go.bind(move || {
        let _ = go2.clone().disabled(busy.get());
    });
    // `.nearby-card`
    w::column(ui)
        .gap(8.0)
        .fill_width()
        .child(
            w::text(ui, tf(ui, "Join {group}", &[("group", group.clone())]))
                .variant("strong")
                .style(remedy_ui::style::StylePatch::default().text_size(14.0)),
        )
        .child(
            w::text(
                ui,
                tf(
                    ui,
                    "Found on {name} · {address}",
                    &[("name", o.from.clone()), ("address", o.host.clone())],
                ),
            )
            .variant("hint"),
        )
        .child(
            w::row(ui)
                .gap(unit * 2.0)
                .align(Align::Center)
                .fill_width()
                .child(pw_el)
                .child(go),
        )
        .child(error_text(ui, err))
}

/// A line of error text that takes no space while there is none.
pub fn error_text(ui: &Ui, err: Signal<Option<String>>) -> El {
    let l = w::text(ui, "")
        .variant("error")
        .text(move || err.get().unwrap_or_default());
    let l2 = l.clone();
    l.bind(move || {
        let m = err.get();
        let _ = l2.clone().hidden(m.is_none()).semantics(
            remedy_ui_a11y::Semantics::new(remedy_ui_a11y::Role::Alert)
                .label(m.unwrap_or_default()),
        );
    })
}

pub fn gate(h: &H, ui: &Ui) -> El {
    let unit = ui.theme().spacing.unit;
    let busy = Signal::new(false);
    let name = w::text_input(ui, t(ui, "This PC")).placeholder(t(ui, "Living-room PC"));
    name.set_value(&h.core.settings().device_name);

    // Groups announced nearby.
    let nearby = ui.el("nearby").column().gap(8.0).fill_width();
    let (n2, h2, u2, name2) = (nearby.clone(), h.clone(), ui.clone(), name.clone());
    let peers = h.peers;
    let shown: Rc<std::cell::RefCell<Option<Vec<Offer>>>> = Rc::default();
    let nearby = nearby.bind(move || {
        let _ = peers.get();
        let offers = h2.core.offers();
        // Rebuilt only when the offers change, so a password being typed stays.
        if shown.borrow().as_ref() == Some(&offers) {
            return;
        }
        *shown.borrow_mut() = Some(offers.clone());
        untrack(|| {
            n2.clear();
            for o in &offers {
                let _ = n2.clone().child(offer_card(&h2, &u2, o, &name2, busy));
            }
            let _ = n2.clone().hidden(offers.is_empty());
        });
    });

    // A new group.
    let group_name = w::text_input(ui, t(ui, "Group name")).placeholder(t(ui, "Home"));
    group_name.set_value("Home");
    let group_pw = w::text_input(ui, t(ui, "Password (leave blank to generate)"))
        .placeholder(t(ui, "A short phrase the house can remember"));
    let (h3, name3, gn, gp) = (
        h.clone(),
        name.clone(),
        group_name.clone(),
        group_pw.clone(),
    );
    let start = w::button(ui, t(ui, "Start a home group"), move || {
        save_name(&h3, &name3);
        let n = gn.value().trim().to_owned();
        let p = gp.value().trim().to_owned();
        h3.core.create_group(
            if n.is_empty() { "Home" } else { &n },
            (!p.is_empty()).then_some(p.as_str()),
        );
        h3.toast("Home group is ready. Copy the invite for the other PCs.");
    })
    .variant("xl")
    .fill_width();
    let new_group = details(
        ui,
        &t(ui, "Name or password for a new group"),
        w::column(ui)
            .gap(unit * 2.0)
            .fill_width()
            .child(w::field(
                ui,
                t(ui, "Group name"),
                group_name.el().fill_width(),
            ))
            .child(w::field(
                ui,
                t(ui, "Password (leave blank to generate)"),
                group_pw.el().fill_width(),
            )),
    );

    // An invite.
    let invite = w::text_area(ui, t(ui, "Paste a Hearth invite"))
        .placeholder(t(ui, "Paste a Hearth invite"));
    let invite_err: Signal<Option<String>> = Signal::new(None);
    let (h4, name4, inv, u4) = (h.clone(), name.clone(), invite.clone(), ui.clone());
    let with_invite = w::column(ui)
        .gap(unit * 2.0)
        .fill_width()
        .child(invite.el().fill_width().height(72.0))
        .child(error_text(ui, invite_err))
        .child(w::button(ui, t(ui, "Join with invite"), move || {
            save_name(&h4, &name4);
            match h4.core.join_invite(&inv.value()) {
                Ok(_) => {
                    invite_err.set(None);
                    h4.toast("Joined. Looking for the other PCs now.");
                }
                Err(e) => invite_err.set(Some(tr(&u4, &e))),
            }
        }));
    let with_invite = details(ui, &t(ui, "I have an invite"), with_invite);

    // `.gate-brand`: the mark beside the name and what Hearth is.
    let brand = w::row(ui)
        .gap(14.0)
        .align(Align::Start)
        .fill_width()
        .padding(Insets {
            top: 0.0,
            end: 0.0,
            bottom: 6.0,
            start: 0.0,
        })
        .child(crate::shell::mark(ui, 56.0).layout(|l| l.shrink = 0.0))
        .child(
            w::column(ui)
                .gap(6.0)
                .grow(1.0)
                .layout(|l| l.min_width = Length::Px(0.0))
                .child(w::heading(ui, 1, "Hearth").variant("title"))
                .child(
                    w::text(
                        ui,
                        t(
                            ui,
                            "Share folders and chat with the other PCs in the house. No cloud, one home group.",
                        ),
                    )
                    .variant("lead"),
                ),
        );
    // `.gate-card`, in the middle of the window (it scrolls when the window is too short).
    let card = ui
        .el("gate_card")
        .column()
        .gap(12.0)
        .width(520.0)
        .layout(|l| {
            l.max_width = Length::Percent(1.0);
            l.shrink = 0.0;
        })
        .child(brand)
        .child(w::field(ui, t(ui, "This PC"), name.el().fill_width()))
        .child(nearby)
        .child(start)
        .child(with_invite)
        .child(new_group);
    w::scroll(ui).grow(1.0).fill_width().child(
        w::column(ui)
            .fill_width()
            .align(Align::Center)
            .justify(Justify::Center)
            .padding(Insets::all(32.0))
            .layout(|l| l.min_height = Length::Percent(1.0))
            .child(card),
    )
}
