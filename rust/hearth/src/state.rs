//! What the interface shares: the engine, signals that follow it, where the user is browsing
//! (with back and forward), and short messages.

use std::cell::{Cell, RefCell};
use std::rc::Rc;
use std::time::Duration;

use hearth_core::client::{Entry, RemoteShare};
use hearth_core::{Event, Hearth};
use remedy_ui::reactive::{Signal, untrack};
use remedy_ui::{App, Ui, Window};
use remedy_ui::update::{Release, UpdateState};

use crate::i18n::{tf, tr};

/// Where the files view is.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Cursor {
    /// The PC.
    pub device: String,
    /// The share on it.
    pub share: String,
    /// The folder inside the share ("" for its top).
    pub path: String,
}

/// The files view's contents.
#[derive(Clone, Debug, Default, PartialEq)]
pub enum Listing {
    /// Nothing open.
    #[default]
    None,
    /// Asking the PC.
    Loading,
    /// What is there.
    Items(Vec<Entry>),
    /// Why it could not be listed.
    Failed(String),
}

/// Shared by every part of the window.
pub struct Home {
    pub app: App,
    pub core: Hearth,
    /// Bumped by the engine: PCs, group, shares, settings, chat, transfers.
    pub peers: Signal<u64>,
    pub group: Signal<u64>,
    pub shares: Signal<u64>,
    pub settings: Signal<u64>,
    pub chat: Signal<u64>,
    pub transfers: Signal<u64>,
    /// Where the files view is, the shares of that PC, and what is in the folder.
    pub cursor: Signal<Option<Cursor>>,
    pub remote_shares: Signal<Vec<RemoteShare>>,
    pub listing: Signal<Listing>,
    back: RefCell<Vec<Cursor>>,
    forward: RefCell<Vec<Cursor>>,
    pub can_back: Signal<bool>,
    pub can_forward: Signal<bool>,
    /// Typed in the search box.
    pub search: Signal<String>,
    /// Grid (true) or list.
    pub grid: Signal<bool>,
    /// Files or Chat.
    pub chat_tab: Signal<bool>,
    pub window: RefCell<Option<Window>>,
    pub ui: RefCell<Option<Ui>>,
    /// Messages raised while no window is open, shown when one opens.
    pending: RefCell<Vec<Note>>,
    /// Where an update stands, the release it is about, and failed checks in a row.
    pub update: Signal<UpdateState>,
    pub release: RefCell<Option<Release>>,
    pub update_failures: Cell<u32>,
    /// Started in the tray (`--background`, or the tray before an update).
    pub background: RefCell<bool>,
}

pub type H = Rc<Home>;

/// A message waiting to be shown in the window's language.
enum Note {
    /// English text (an engine error, a message made of English parts).
    Plain(String),
    /// A catalog key with its `{name}` values.
    Keyed(String, Vec<(String, String)>),
}

impl Note {
    /// The English it is written in.
    fn english(&self) -> &str {
        match self {
            Note::Plain(t) | Note::Keyed(t, _) => t,
        }
    }
}

impl Home {
    pub fn new(app: &App, core: Hearth) -> H {
        let h = Rc::new(Home {
            app: app.clone(),
            core,
            peers: Signal::new(0),
            group: Signal::new(0),
            shares: Signal::new(0),
            settings: Signal::new(0),
            chat: Signal::new(0),
            transfers: Signal::new(0),
            cursor: Signal::new(None),
            remote_shares: Signal::new(Vec::new()),
            listing: Signal::new(Listing::None),
            back: RefCell::new(Vec::new()),
            forward: RefCell::new(Vec::new()),
            can_back: Signal::new(false),
            can_forward: Signal::new(false),
            search: Signal::new(String::new()),
            grid: Signal::new(true),
            chat_tab: Signal::new(false),
            window: RefCell::new(None),
            ui: RefCell::new(None),
            pending: RefCell::new(Vec::new()),
            update: Signal::new(UpdateState::Idle),
            release: RefCell::new(None),
            update_failures: Cell::new(0),
            background: RefCell::new(false),
        });
        // The engine works on its own threads; what it reports is handled here.
        if let Ok(waker) = remedy_ui::reactive::waker() {
            let (peers, group, shares, settings, chat, transfers) =
                (h.peers, h.group, h.shares, h.settings, h.chat, h.transfers);
            h.core.on_event(move |e| {
                let _ = waker.post(move || {
                    let s = match e {
                        Event::Peers | Event::Nearby => peers,
                        Event::Group => group,
                        Event::Shares => shares,
                        Event::Settings | Event::Hosts => settings,
                        Event::Chat => chat,
                        Event::Transfers => transfers,
                    };
                    let _ = s.try_update(|n| *n = n.wrapping_add(1));
                });
            });
        }
        // Online state ages without events: look again every few seconds.
        let p = h.peers;
        tick(app, p);
        h
    }

    /// A short message at the bottom of the window. The text is English (a key of the
    /// catalogs, or an engine error); it is shown in the window's language.
    pub fn toast(&self, text: impl Into<String>) {
        self.show(Note::Plain(text.into()));
    }

    /// [`Home::toast`] for a catalog key with `{name}` values, which are shown as they are.
    pub fn toast_f(&self, key: &str, args: &[(&str, String)]) {
        let args = args
            .iter()
            .map(|(k, v)| ((*k).to_owned(), v.clone()))
            .collect();
        self.show(Note::Keyed(key.to_owned(), args));
    }

    fn show(&self, note: Note) {
        let long = ["updated", "saved", "downloaded"]
            .iter()
            .any(|w| note.english().to_lowercase().contains(w));
        let open = self.window.borrow().as_ref().is_some_and(Window::is_open);
        let ui = self.ui.borrow().clone();
        match ui {
            Some(ui) if open => {
                // Written in the window's language as it is raised, not tracked by whatever
                // reactive scope raised it.
                let text = untrack(|| match &note {
                    Note::Plain(t) => tr(&ui, t),
                    Note::Keyed(k, a) => {
                        // A share's name made by Hearth ("Documents", "Local Disk (C:)") is
                        // shown in the window's language too.
                        let a: Vec<(&str, String)> = a
                            .iter()
                            .map(|(k, v)| {
                                let v = if k == "label" {
                                    crate::i18n::place(&ui, v)
                                } else {
                                    v.clone()
                                };
                                (k.as_str(), v)
                            })
                            .collect();
                        tf(&ui, k, &a)
                    }
                });
                // Bottom right, stacked, as the page showed them.
                let _ = remedy_ui::widgets::toast_at(
                    &ui,
                    text,
                    Duration::from_millis(if long { 5000 } else { 3200 }),
                    remedy_ui::view::Align::End,
                );
            }
            _ => self.pending.borrow_mut().push(note),
        }
    }

    /// Shows the messages raised while the window was closed.
    pub fn flush_toasts(&self) {
        let waiting = std::mem::take(&mut *self.pending.borrow_mut());
        for n in waiting {
            self.show(n);
        }
    }

    /// Runs engine work and hands its result to `done` on the UI thread. Work started inside
    /// `done` (or inside an effect) is never lost: the result travels by an app completion,
    /// which belongs to no reactive scope.
    pub fn run<T: Send + 'static>(
        &self,
        fut: impl std::future::Future<Output = T> + Send + 'static,
        done: impl FnOnce(T) + 'static,
    ) {
        let c = self.app.completion(done);
        self.core.spawn(fut, move |v| c.complete(v));
    }

    /// Opens a folder of a share on a PC, remembering where the user was.
    pub fn open(self: &Rc<Self>, to: Cursor) {
        if let Some(cur) = self.cursor.get_untracked().ok().flatten()
            && cur != to
        {
            self.back.borrow_mut().push(cur);
            self.forward.borrow_mut().clear();
        }
        self.go(to);
    }

    fn go(self: &Rc<Self>, to: Cursor) {
        self.can_back.set(!self.back.borrow().is_empty());
        self.can_forward.set(!self.forward.borrow().is_empty());
        let device_changed = self
            .cursor
            .get_untracked()
            .ok()
            .flatten()
            .is_none_or(|c| c.device != to.device);
        self.cursor.set(Some(to.clone()));
        self.listing.set(Listing::Loading);
        if device_changed {
            self.refresh_shares(&to.device);
        }
        self.reload();
    }

    /// Back to the previous folder.
    pub fn go_back(self: &Rc<Self>) {
        let Some(prev) = self.back.borrow_mut().pop() else {
            return;
        };
        if let Some(cur) = self.cursor.get_untracked().ok().flatten() {
            self.forward.borrow_mut().push(cur);
        }
        self.go(prev);
    }

    /// Forward again.
    pub fn go_forward(self: &Rc<Self>) {
        let Some(next) = self.forward.borrow_mut().pop() else {
            return;
        };
        if let Some(cur) = self.cursor.get_untracked().ok().flatten() {
            self.back.borrow_mut().push(cur);
        }
        self.go(next);
    }

    /// Up one folder.
    pub fn go_up(self: &Rc<Self>) {
        let Some(mut c) = self.cursor.get_untracked().ok().flatten() else {
            return;
        };
        if c.path.is_empty() {
            return;
        }
        c.path = c
            .path
            .rsplit_once('/')
            .map(|(a, _)| a.to_owned())
            .unwrap_or_default();
        self.open(c);
    }

    /// Reads the current folder again.
    pub fn reload(self: &Rc<Self>) {
        let Some(c) = self.cursor.get_untracked().ok().flatten() else {
            return;
        };
        let (core, h) = (self.core.clone(), self.clone());
        let want = c.clone();
        self.run(
            async move { core.files_of(&c.device, &c.share, &c.path).await },
            move |r| {
                // A newer folder may have been opened meanwhile.
                if h.cursor.get_untracked().ok().flatten().as_ref() != Some(&want) {
                    return;
                }
                h.listing.set(match r {
                    Ok(items) => Listing::Items(items),
                    Err(e) => Listing::Failed(e),
                });
            },
        );
    }

    fn refresh_shares(self: &Rc<Self>, device: &str) {
        let (core, h, d) = (self.core.clone(), self.clone(), device.to_owned());
        self.run(async move { core.shares_of(&d).await }, move |r| {
            h.remote_shares.set(r.unwrap_or_default())
        });
    }

    /// Opens a PC: its first share, or says it has none.
    pub fn open_pc(self: &Rc<Self>, device: &str, name: &str) {
        let (core, h, d, n) = (
            self.core.clone(),
            self.clone(),
            device.to_owned(),
            name.to_owned(),
        );
        let me = self.core.device_id() == device;
        self.run(
            async move { core.shares_of(&d).await.map(|s| (d, s)) },
            move |r| match r {
                Ok((d, shares)) => match shares.first() {
                    Some(s) => {
                        h.remote_shares.set(shares.clone());
                        h.chat_tab.set(false);
                        h.open(Cursor {
                            device: d,
                            share: s.id.clone(),
                            path: String::new(),
                        });
                    }
                    None if me => h.toast("Share a folder first"),
                    None => h.toast_f("{name} has not shared a folder yet", &[("name", n)]),
                },
                Err(e) => h.toast(e),
            },
        );
    }
}

fn tick(app: &App, peers: Signal<u64>) {
    let a = app.clone();
    app.after(Duration::from_secs(5), move || {
        let _ = peers.try_update(|n| *n = n.wrapping_add(1));
        tick(&a, peers);
    });
}

/// Human sizes: B, KB, MB, GB (one decimal past bytes).
/// A size in the window's language: its digits and decimal separator, and its unit word
/// ("2,5 Ko" in French).
pub fn size_in(ui: &Ui, n: u64) -> String {
    use remedy_ui::i18n::{Decimal, NumberSymbols};
    const KEYS: [&str; 4] = ["{n} B", "{n} KB", "{n} MB", "{n} GB"];
    let (v, u) = scaled(n);
    let syms = NumberSymbols::for_locale(&ui.locale());
    let number = if u == 0 {
        syms.format(Decimal::from_int(i64::try_from(n).unwrap_or(i64::MAX)), 0)
    } else {
        syms.format(
            Decimal::from_f64(v, 1).unwrap_or_else(|| Decimal::from_int(0)),
            1,
        )
    };
    tf(
        ui,
        KEYS.get(u).copied().unwrap_or("{n} GB"),
        &[("n", number)],
    )
}

/// `n` bytes in the largest unit that keeps it at or above 1, and that unit (0 = B … 3 = GB).
fn scaled(n: u64) -> (f64, usize) {
    let mut v = n as f64;
    let mut u = 0;
    while v >= 1024.0 && u < 3 {
        v /= 1024.0;
        u += 1;
    }
    (v, u)
}

/// A size in English (B, KB, MB, GB, one decimal past bytes): for logs and tests.
#[cfg(test)]
pub fn size_text(n: u64) -> String {
    const UNITS: [&str; 4] = ["B", "KB", "MB", "GB"];
    let mut v = n as f64;
    let mut u = 0;
    while v >= 1024.0 && u + 1 < UNITS.len() {
        v /= 1024.0;
        u += 1;
    }
    if u == 0 {
        format!("{n} B")
    } else {
        format!("{v:.1} {}", UNITS.get(u).copied().unwrap_or("GB"))
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn sizes_read_in_the_window_s_language() {
        let theme = crate::shell::theme_for("forest", None, false);
        let ui = remedy_ui::Ui::new(
            theme.clone(),
            crate::look::sheet(&theme),
            remedy_ui::text::FontCollection::new(),
        );
        ui.set_locale("en-US".to_owned());
        assert_eq!(super::size_in(&ui, 2560), "2.5 KB");
        assert_eq!(super::size_in(&ui, 512), "512 B");
        ui.set_locale("de-DE".to_owned());
        let de = super::size_in(&ui, 2560);
        assert!(de.contains("2,5"), "{de}");
        ui.set_locale("fr-FR".to_owned());
        let fr = super::size_in(&ui, 3 * 1024 * 1024 + 512 * 1024);
        assert!(fr.contains("3,5") && fr.contains("Mo"), "{fr}");
    }
}
