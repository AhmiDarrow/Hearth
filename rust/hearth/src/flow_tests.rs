//! The whole app on the headless host, driven the way a screen reader drives it (by the
//! accessibility tree's labels and actions) and, for drops, the way the system delivers
//! them. The other PC is a second engine in the same process; every profile lives in a
//! temporary folder.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::time::Duration;

use hearth_core::Hearth;
use hearth_core::transfers::Status;
use remedy_ui::core::Point;
use remedy_ui::host::accesskit::{Action, ActionRequest, Node, NodeId, TreeId};
use remedy_ui::host::{AccessibilityEvent, DragData, DragEvent, Host, HostEvent, WindowId};
use remedy_ui::input::{Key, KeyEvent, Modifiers, PointerEvent, PointerPhase};
use remedy_ui::{App, AppConfig, El, GeometryStore, Runner};
use remedy_ui::headless::HeadlessHost;

use crate::Launch;
use crate::state::{Cursor, H, Listing};

fn free_port() -> u16 {
    std::net::TcpListener::bind("127.0.0.1:0")
        .and_then(|l| l.local_addr())
        .map(|a| a.port())
        .unwrap_or(0)
}

struct Driver {
    host: HeadlessHost,
    runner: Runner,
    h: H,
    trees: HashMap<WindowId, HashMap<NodeId, Node>>,
    roots: HashMap<WindowId, NodeId>,
    dir: tempfile::TempDir,
}

impl Driver {
    fn new() -> Self {
        let dir = tempfile::tempdir().unwrap_or_else(|e| unreachable!("temp dir: {e}"));
        let l = Launch {
            port: free_port(),
            home: dir.path().join("profile"),
            ..Launch::default()
        };
        let slot: std::rc::Rc<std::cell::RefCell<Option<H>>> = std::rc::Rc::default();
        let s2 = slot.clone();
        // The interface's own saved choices stay in the test's folder.
        let config = AppConfig::new("Hearth test")
            .cpu_only()
            .data_dir(dir.path().join("ui"))
            .theme(
                crate::shell::theme_for("forest", None, false),
                crate::look::sheet,
            );
        let mut runner =
            Runner::with_geometry(config, GeometryStore::in_memory(), move |app: &App| {
                *s2.borrow_mut() = crate::shell::start(app, &l);
            });
        let mut host = HeadlessHost::new();
        host.run_frames(&mut runner, 1);
        let h = slot
            .borrow()
            .clone()
            .unwrap_or_else(|| unreachable!("Hearth started"));
        // No update checks against the real release feed from tests.
        h.core.set_auto_check_updates(false);
        // Downloads land in the test's folder, never the user's.
        let dl = dir.path().join("downloads");
        let _ = std::fs::create_dir_all(&dl);
        let _ = h.core.set_download_folder(&dl.to_string_lossy());
        let mut d = Driver {
            host,
            runner,
            h,
            trees: HashMap::new(),
            roots: HashMap::new(),
            dir,
        };
        d.frames(3);
        d
    }

    fn frames(&mut self, n: usize) {
        self.host.run_frames(&mut self.runner, n);
        for id in self.host.window_ids() {
            let tree = self.trees.entry(id).or_default();
            if tree.is_empty() {
                self.host
                    .send(HostEvent::Accessibility(id, AccessibilityEvent::Requested));
                self.host.run_frames(&mut self.runner, 1);
            }
            for u in self.host.take_accessibility(id) {
                if let Some(t) = &u.tree {
                    self.roots.insert(id, t.root);
                }
                for (nid, node) in u.nodes {
                    tree.insert(nid, node);
                }
            }
            if let Some(root) = self.roots.get(&id).copied() {
                let mut live = std::collections::HashSet::new();
                let mut todo = vec![root];
                while let Some(n) = todo.pop() {
                    if live.insert(n)
                        && let Some(node) = tree.get(&n)
                    {
                        todo.extend(node.children().iter().copied());
                    }
                }
                tree.retain(|k, _| live.contains(k));
            }
        }
    }

    /// Runs frames until `done`, letting the engine's threads work.
    fn until(&mut self, what: &str, done: impl Fn(&Driver) -> bool) {
        for _ in 0..2000 {
            self.frames(1);
            if done(self) {
                return;
            }
            std::thread::sleep(Duration::from_millis(10));
        }
        let labels = self.labels();
        unreachable!("timed out waiting for {what}; the window shows {labels:?}");
    }

    fn win(&self) -> WindowId {
        self.host
            .window_ids()
            .first()
            .copied()
            .unwrap_or_else(|| unreachable!("a window"))
    }

    fn labels(&self) -> Vec<String> {
        self.trees
            .get(&self.win())
            .map(|t| {
                t.values()
                    .filter_map(|n| n.label().map(str::to_owned))
                    .collect()
            })
            .unwrap_or_default()
    }

    fn has(&self, label: &str) -> bool {
        self.trees
            .get(&self.win())
            .is_some_and(|t| t.values().any(|n| n.label() == Some(label)))
    }

    fn node(&self, label: &str, action: Action) -> NodeId {
        let tree = self
            .trees
            .get(&self.win())
            .unwrap_or_else(|| unreachable!("a tree"));
        match tree
            .iter()
            .find(|(_, n)| n.label() == Some(label) && n.supports_action(action))
        {
            Some((id, _)) => *id,
            None => unreachable!("no {label:?} with {action:?} in {:?}", self.labels()),
        }
    }

    fn act(&mut self, label: &str, action: Action) {
        let target = self.node(label, action);
        let req = ActionRequest {
            action,
            target_tree: TreeId::ROOT,
            target_node: target,
            data: None,
        };
        let w = self.win();
        self.host
            .send(HostEvent::Accessibility(w, AccessibilityEvent::Action(req)));
        self.frames(2);
    }

    fn click(&mut self, label: &str) {
        self.act(label, Action::Click);
    }

    fn key(&mut self, key: Key, modifiers: Modifiers) {
        let w = self.win();
        self.host.send(HostEvent::Key(
            w,
            KeyEvent::press(key)
                .with_modifiers(modifiers)
                .with_text(None),
        ));
        self.frames(2);
    }

    /// Types into a field: focus, select all, paste.
    fn type_into(&mut self, label: &str, text: &str) {
        self.act(label, Action::Focus);
        let ctrl = Modifiers {
            ctrl: true,
            ..Modifiers::NONE
        };
        self.key(Key::Character("a".into()), ctrl);
        let _ = self.host.set_clipboard_text(text, false);
        self.key(Key::Character("v".into()), ctrl);
    }

    fn ui(&self) -> remedy_ui::Ui {
        self.h
            .ui
            .borrow()
            .clone()
            .unwrap_or_else(|| unreachable!("the window is built"))
    }

    /// The first element `widget` made, depth first.
    fn find(&self, widget: &str) -> Option<El> {
        let mut todo = vec![self.ui().root()];
        while let Some(e) = todo.pop() {
            if e.info().is_some_and(|i| i.widget == widget && !i.hidden) {
                return Some(e);
            }
            let mut kids = e.child_elements();
            kids.reverse();
            todo.extend(kids);
        }
        None
    }

    /// Every control has a name, and the engine's accessibility check finds nothing.
    fn assert_accessible(&self) {
        let tree = self
            .trees
            .get(&self.win())
            .unwrap_or_else(|| unreachable!("a tree"));
        let nameless: Vec<String> = tree
            .values()
            .filter(|n| n.supports_action(Action::Click) || n.supports_action(Action::Focus))
            .filter(|n| n.label().is_none_or(|l| l.trim().is_empty()))
            .map(|n| format!("{:?}", n.role()))
            .collect();
        assert!(nameless.is_empty(), "unnamed controls: {nameless:?}");
        let ui = self.ui();
        let found: Vec<String> = remedy_ui::devtools::audit(&ui)
            .iter()
            .map(|p| {
                let what = ui
                    .element(p.element)
                    .info()
                    .map(|i| remedy_ui::devtools::describe(&i))
                    .unwrap_or_default();
                format!("{} [{what}]", p.message())
            })
            .collect();
        assert!(found.is_empty(), "{found:#?}");
    }

    /// Rests the pointer on the control named `label` long enough for its tooltip.
    fn hover(&mut self, label: &str) {
        let el = {
            let mut todo = vec![self.ui().root()];
            let mut found = None;
            while let Some(e) = todo.pop() {
                let named = e
                    .info()
                    .and_then(|i| i.semantics)
                    .and_then(|s| s.name().map(str::to_owned))
                    .is_some_and(|n| n == label);
                if named {
                    found = Some(e);
                    break;
                }
                todo.extend(e.child_elements());
            }
            found.unwrap_or_else(|| unreachable!("no control named {label:?}"))
        };
        let at = el.bounds().unwrap_or_default().center();
        let w = self.win();
        self.host.send(HostEvent::Pointer(
            w,
            PointerEvent::new(PointerPhase::Move, at).with_pressure(0.0),
        ));
        self.frames(1);
        self.host.advance(Duration::from_millis(900));
        self.frames(3);
    }

    /// Drops files from another app at the middle of `el`.
    fn drop_files_on(&mut self, el: &El, files: Vec<PathBuf>) {
        let b = el.bounds().unwrap_or_default();
        let at = Point::new((b.min.x + b.max.x) / 2.0, (b.min.y + b.max.y) / 2.0);
        let w = self.win();
        let data = DragData {
            files,
            ..DragData::default()
        };
        self.host.send(HostEvent::Drag(
            w,
            DragEvent::Enter {
                position: Some(at),
                data: data.clone(),
            },
        ));
        self.frames(1);
        self.host.send(HostEvent::Drag(
            w,
            DragEvent::Drop {
                position: Some(at),
                data,
            },
        ));
        self.frames(2);
    }
}

/// The other PC: an engine with a writable share holding one file.
struct Peer {
    core: Hearth,
    share: PathBuf,
}

fn peer(dir: &Path) -> Peer {
    let core =
        Hearth::start(&dir.join("peer"), free_port()).unwrap_or_else(|e| unreachable!("{e}"));
    core.set_device_name("Den PC");
    let _ = core.create_group("Home", Some("kettle on the stove"));
    let share = dir.join("den-share");
    let _ = std::fs::create_dir_all(&share);
    let _ = std::fs::write(share.join("notes.txt"), b"the tea is in the left cupboard");
    let _ = core.add_share(&share, "Den files", true);
    Peer { core, share }
}

/// An invite that reaches the peer on this machine.
fn invite_for(p: &Peer) -> String {
    let g = p
        .core
        .group()
        .unwrap_or_else(|| unreachable!("the peer has a group"));
    hearth_core::invite::make(
        &g.id,
        &g.salt,
        &g.password,
        &g.name,
        &[format!("127.0.0.1:{}", p.core.port())],
    )
}

#[test]
fn first_run_names_the_pc_and_starts_a_group() {
    let mut d = Driver::new();
    assert!(d.has("Start a home group"));
    d.assert_accessible();
    d.type_into("This PC", "Kitchen PC");
    d.click("Start a home group");
    d.until("the house", |d| d.has("Copy invite"));
    assert_eq!(d.h.core.settings().device_name, "Kitchen PC");
    assert_eq!(d.h.core.group().map(|g| g.name).as_deref(), Some("Home"));
    assert!(d.h.core.invite().is_some_and(|i| i.starts_with("hearth|")));
    d.frames(3);
    d.assert_accessible();
    // Copy invite puts the invite (it holds the password) on the clipboard as sensitive.
    d.click("Copy invite");
    assert_eq!(d.host.clipboard_text().ok().flatten(), d.h.core.invite());
}

#[test]
fn many_groups_nearby_scroll_inside_the_first_run_window() {
    let mut d = Driver::new();
    // Eight houses beaconing nearby (other tests' engines may add more).
    let others: Vec<Hearth> = (0..8)
        .map(|i| {
            let h = Hearth::start(&d.dir.path().join(format!("house{i}")), free_port())
                .unwrap_or_else(|e| unreachable!("{e}"));
            let _ = h.create_group(&format!("House {i}"), Some("open sesame"));
            h
        })
        .collect();
    d.until("the offers", |d| d.h.core.offers().len() >= others.len());
    d.frames(4);
    d.assert_accessible();
}

#[test]
fn a_bad_invite_says_so_and_settings_open_before_joining() {
    let mut d = Driver::new();
    d.click("I have an invite");
    d.type_into("Paste a Hearth invite", "not an invite");
    d.click("Join with invite");
    assert!(
        d.has("Not a Hearth invite. Ask a member to copy theirs."),
        "{:?}",
        d.labels()
    );
    assert!(d.h.core.group().is_none());
    // Settings from the gear, before any group: Network says what to do first.
    d.click("Settings");
    d.frames(2);
    d.click("Network");
    assert!(d.has("Join or start a home group to get an invite."));
    d.click("General");
    assert!(d.has("PC name"));
}

#[test]
fn joined_by_invite_it_browses_downloads_uploads_and_chats_with_the_other_pc() {
    let mut d = Driver::new();
    let p = peer(d.dir.path());
    d.click("I have an invite");
    d.type_into("Paste a Hearth invite", &invite_for(&p));
    d.click("Join with invite");
    d.until("the house", |d| d.has("Copy invite"));
    // The invite named the peer's address: they meet.
    d.until("the peer online", |d| {
        d.h.core
            .members()
            .iter()
            .any(|m| m.name == "Den PC" && m.online)
    });
    d.until("the peer's row", |d| d.has("Den PC"));
    d.click("Den PC");
    d.until("the peer's files", |d| matches!(d.h.listing.get_untracked().ok(), Some(Listing::Items(ref v)) if !v.is_empty()));
    d.frames(3);
    d.assert_accessible();

    // Download: focus the files, take the first, Enter.
    d.act("Folder contents", Action::Focus);
    d.key(Key::Home, Modifiers::NONE);
    d.key(Key::Enter, Modifiers::NONE);
    d.until("the download", |d| {
        d.h.core
            .transfers()
            .iter()
            .any(|j| j.status == Status::Done)
    });
    let got = std::fs::read(d.dir.path().join("downloads").join("notes.txt")).unwrap_or_default();
    assert_eq!(got, b"the tea is in the left cupboard");
    d.frames(2);
    assert!(
        d.has("Show"),
        "a finished download can be shown in the file manager"
    );

    // Upload: dropped files land in the folder shown; a dropped folder is not sent (0.4).
    let photo = d.dir.path().join("beach.jpg");
    let _ = std::fs::write(&photo, b"sand");
    let trip = d.dir.path().join("Trip");
    let _ = std::fs::create_dir_all(&trip);
    let grid = d
        .find("grid")
        .unwrap_or_else(|| unreachable!("the grid shows"));
    d.drop_files_on(&grid, vec![photo, trip]);
    let want = p.share.join("beach.jpg");
    d.until("the upload", |_| {
        std::fs::read(&want).is_ok_and(|b| b == b"sand")
    });
    assert!(
        !p.share.join("Trip").exists(),
        "folders are not made on the other PC"
    );

    // Chat both ways.
    d.click("Chat");
    d.type_into("Message the house…", "Dinner at seven");
    d.key(Key::Enter, Modifiers::NONE);
    d.until("the peer to hear it", |_| {
        p.core
            .chat()
            .iter()
            .any(|m| m.get("text").and_then(|t| t.as_str()) == Some("Dinner at seven"))
    });
    let _ = p.core.send_chat("On my way", None);
    d.until("the reply", |d| d.has("On my way"));
    d.assert_accessible();
}

#[test]
fn this_pc_shares_a_dropped_folder_and_stops_sharing_after_asking() {
    let mut d = Driver::new();
    d.click("Start a home group");
    d.until("the house", |d| d.has("Copy invite"));
    let music = d.dir.path().join("Music");
    let _ = std::fs::create_dir_all(&music);
    d.frames(2);
    // Drop the folder on "Shared from this PC".
    let target = {
        let mut todo = vec![d.ui().root()];
        let mut found = None;
        while let Some(e) = todo.pop() {
            if e.info().and_then(|i| i.text).as_deref() == Some("Shared from this PC") {
                found = e.parent();
                break;
            }
            todo.extend(e.child_elements());
        }
        found.unwrap_or_else(|| unreachable!("the shares section"))
    };
    d.drop_files_on(&target, vec![music.clone()]);
    let shares = d.h.core.shares();
    assert_eq!(shares.len(), 1);
    assert!(
        shares.first().is_some_and(|s| !s.writable),
        "a dropped folder is shared read-only"
    );
    d.frames(2);
    // Removing asks first.
    d.click("Remove share");
    assert!(d.has("Stop sharing?"));
    d.click("Cancel");
    assert_eq!(d.h.core.shares().len(), 1);
    d.click("Remove share");
    d.click("Stop sharing");
    assert!(d.h.core.shares().is_empty());
}

/// Everything that opens near an edge stays inside the window: the menu, the gear's
/// tooltip in the top-right corner, in a long language and a narrow window.
#[test]
fn menus_and_tooltips_stay_inside_the_window_in_any_language_and_size() {
    let mut d = Driver::new();
    d.click("Start a home group");
    d.until("the house", |d| d.has("Copy invite"));
    for (lang, size) in [
        ("en", (1280.0, 800.0)),
        ("de", (1280.0, 800.0)),
        ("de", (720.0, 520.0)),
    ] {
        let _ = d.h.core.set_language(lang);
        let w = d.win();
        d.host
            .user_resize(w, remedy_ui::core::Size::new(size.0, size.1));
        d.frames(4);
        let settings = crate::i18n::t(&d.ui(), "Settings");
        d.hover(&settings);
        d.assert_accessible();
        let menu = crate::i18n::t(&d.ui(), "Menu");
        d.click(&menu);
        d.frames(2);
        d.assert_accessible();
        d.key(Key::Escape, Modifiers::NONE);
    }
}

#[test]
fn themes_follow_the_setting_live() {
    let mut d = Driver::new();
    d.h.core.set_theme("light");
    d.frames(3);
    let name = d.ui().theme().name.clone();
    let light = remedy_ui::themes::palette("light")
        .map(|p| p.name)
        .unwrap_or_default();
    assert_eq!(name, light);
    d.h.core.set_theme("cyan");
    d.frames(3);
    let cyan = remedy_ui::themes::palette("cyan")
        .map(|p| p.name)
        .unwrap_or_default();
    assert_eq!(d.ui().theme().name, cyan);
}

#[test]
fn the_typeface_follows_the_setting_live_and_keeps_the_palette() {
    let mut d = Driver::new();
    let first = |d: &Driver| d.ui().theme().font_families.first().cloned();
    assert_eq!(first(&d).as_deref(), Some("Remedy Sans UI"));
    d.h.core.set_theme("cyan");
    d.h.core.set_font("ahmi-print");
    d.frames(3);
    assert_eq!(first(&d).as_deref(), Some("AhmiPrint"));
    let cyan = remedy_ui::themes::palette("cyan")
        .map(|p| p.name)
        .unwrap_or_default();
    assert_eq!(d.ui().theme().name, cyan, "the palette stays");
    // Settings offers it by name, and Reset goes back to the default face.
    crate::shell::settings(&d.h, "general");
    d.frames(3);
    assert!(d.has("Font"));
    d.h.core.reset_settings();
    d.frames(3);
    assert_eq!(first(&d).as_deref(), Some("Remedy Sans UI"));
    d.assert_accessible();
}

#[test]
fn the_language_follows_the_setting_live() {
    let mut d = Driver::new();
    assert!(d.has("Start a home group"));
    d.click("Start a home group");
    d.until("the house", |d| d.has("Copy invite"));
    assert!(d.has("Share a folder"));
    // French, at once: the whole window is made again, and so are the messages.
    d.h.core
        .set_language("fr")
        .unwrap_or_else(|e| unreachable!("{e}"));
    d.until("French", |d| {
        d.has("Partager un dossier") && d.has("Copier l’invitation")
    });
    assert!(!d.has("Share a folder"));
    d.h.toast("Could not finish that transfer: File is too large");
    d.until("the message in French", |d| {
        d.has("Impossible de terminer ce transfert : Le fichier est trop volumineux")
    });
    // Settings, open at a section, stay at it across a change of language.
    d.click("Réglages");
    d.click("Réseau");
    assert!(d.has("Quitter ce Hearth"));
    d.h.core
        .set_language("de")
        .unwrap_or_else(|e| unreachable!("{e}"));
    d.until("German", |d| d.has("Diese Hearth-Gruppe verlassen"));
    // Something that is not a language is refused, and the language stays.
    assert_eq!(
        d.h.core.set_language("Klingon"),
        Err("Unknown language".to_owned())
    );
    assert_eq!(d.h.core.settings().language, "de");
    // Resetting the settings leaves the language alone.
    d.h.core.reset_settings();
    d.frames(3);
    assert_eq!(d.h.core.settings().language, "de");
    assert!(d.has("Diese Hearth-Gruppe verlassen"));
    // Back to the system's language (English here).
    d.h.core
        .set_language("")
        .unwrap_or_else(|e| unreachable!("{e}"));
    d.until("English", |d| d.has("Leave this hearth"));
    d.click("General");
    assert!(d.has("Language") && d.has("Reset settings to defaults"));
    d.assert_accessible();
}

/// A Library share in the test's folder with subfolders, opened in the files view.
fn library(d: &mut Driver) -> (PathBuf, String) {
    let lib = d.dir.path().join("Library");
    for sub in ["Photos/2026", "Music"] {
        let _ = std::fs::create_dir_all(lib.join(sub));
    }
    let _ = std::fs::write(lib.join("readme.txt"), "hello from Hearth");
    let _ = std::fs::write(lib.join("Holiday plan.pdf"), vec![b'x'; 48_000]);
    let _ = std::fs::write(lib.join("Photos/beach.jpg"), vec![b'x'; 2_400_000]);
    let s =
        d.h.core
            .add_share(&lib, "Library", true)
            .unwrap_or_else(|e| unreachable!("{e}"));
    (lib, s.id)
}

fn open_share(d: &mut Driver, id: &str) {
    let me = d.h.core.device_id();
    d.h.open(Cursor {
        device: me,
        share: id.to_owned(),
        path: String::new(),
    });
    d.until("the files", |d| {
        matches!(d.h.listing.get_untracked().ok(), Some(Listing::Items(_)))
    });
    d.frames(4);
}

#[test]
fn the_share_dialog_browses_this_pcs_folders() {
    let mut d = Driver::new();
    d.click("Start a home group");
    d.until("the house", |d| d.has("Copy invite"));
    let (lib, id) = library(&mut d);
    let ui = d.ui();
    crate::share_dialog::open(&d.h, &ui, crate::share_dialog::Mode::Edit(id.clone()));
    // It starts in the share's folder: its subfolders, by name.
    d.until("the subfolders", |d| d.has("Photos") && d.has("Music"));
    assert!(d.has("Up") && d.has("Edit share"));
    d.assert_accessible();
    // A click picks a folder; a second one opens it.
    d.click("Photos");
    d.click("Photos");
    d.until("inside Photos", |d| d.has("2026"));
    d.click("2026");
    d.click("2026");
    d.until("an empty folder", |d| d.has("No subfolders"));
    d.click("Up");
    d.until("back in Photos", |d| d.has("2026"));
    d.click("Save changes");
    d.frames(3);
    let path =
        d.h.core
            .shares()
            .into_iter()
            .find(|s| s.id == id)
            .map(|s| s.path)
            .unwrap_or_default();
    assert_eq!(
        hearth_core::paths::canonical_stored_path(&path.to_string_lossy()),
        hearth_core::paths::canonical_stored_path(&lib.join("Photos").to_string_lossy()),
        "the share now points at the folder browsed to"
    );
}

#[test]
fn settings_explain_themselves_and_the_theme_cards_choose_a_palette() {
    let mut d = Driver::new();
    d.click("Start a home group");
    d.until("the house", |d| d.has("Copy invite"));
    d.click("Settings");
    d.frames(3);
    for hint in [
        "This is how other PCs in the house see you. Changes apply as soon as you leave the field.",
        "Restores theme, startup, download folder, update checks, bridge, and speed limits. Your home group and shared folders are left alone.",
    ] {
        assert!(d.has(hint), "{hint}");
    }
    d.click("Ocean, Teal");
    d.frames(2);
    assert_eq!(d.h.core.settings().theme, "cyan");
    d.click("Light");
    d.frames(2);
    assert_eq!(d.h.core.settings().theme, "light");
    d.assert_accessible();
    d.click("Network");
    assert!(d.has("Paste this invite in Hearth on the other computer to join this home group."));
    assert!(d.has("Ports"));
    // The PC name saves when the field is left, only when it changed.
    d.click("General");
    d.type_into("PC name", "Attic PC");
    d.key(Key::Tab, Modifiers::NONE);
    d.frames(2);
    assert_eq!(d.h.core.settings().device_name, "Attic PC");
}

/// Writes what the window shows to `HEARTH_SHOTS` (a folder), the screens the Python app's
/// `tools/port_screens_before.py` shows, for the port's before/after evidence:
/// `HEARTH_SHOTS=dir cargo test shots -- --ignored`.
#[test]
#[ignore = "writes screenshots for review"]
fn shots() {
    let Some(dir) = std::env::var_os("HEARTH_SHOTS").map(PathBuf::from) else {
        return;
    };
    let _ = std::fs::create_dir_all(&dir);
    let mut d = Driver::new();
    let shoot = |d: &mut Driver, name: &str| {
        d.frames(4);
        let Some(w) = d.host.window(d.win()) else {
            return;
        };
        let (width, height, _) = w.size();
        let rgba = w.surface().to_vec();
        let _ = image::save_buffer(
            dir.join(format!("{name}.png")),
            &rgba,
            width,
            height,
            image::ExtendedColorType::Rgba8,
        );
    };
    let theme = |d: &mut Driver, id: &str| {
        d.h.core.set_theme(id);
        d.frames(4);
    };
    d.h.core.set_device_name("Studio PC");
    d.until("the mark", |_| remedy_ui::image::cached() > 0);
    theme(&mut d, "dark");
    shoot(&mut d, "01-gate-dark");
    d.type_into("This PC", "Studio PC");
    d.click("Start a home group");
    d.until("the house", |d| d.has("Copy invite"));
    d.h.core.set_device_name("Studio PC");
    let (_, id) = library(&mut d);
    let _ = d.h.core.send_chat("Dinner at seven?", None);
    let _ =
        d.h.core
            .send_chat("I'll bring the photos from the trip.", None);
    open_share(&mut d, &id);
    shoot(&mut d, "02-files-grid-dark");
    d.h.grid.set(false);
    shoot(&mut d, "03-files-list-dark");
    d.h.grid.set(true);
    d.click("Chat");
    d.until("the messages", |d| d.has("Dinner at seven?"));
    shoot(&mut d, "04-chat-dark");
    d.click("Files");
    d.click("Settings");
    for (i, sec) in [
        "General",
        "Sharing",
        "Transfers",
        "Network",
        "Updates",
        "About",
    ]
    .iter()
    .enumerate()
    {
        d.click(sec);
        shoot(
            &mut d,
            &format!("{:02}-settings-{}-dark", i + 5, sec.to_lowercase()),
        );
    }
    d.key(Key::Escape, Modifiers::NONE);
    d.frames(2);
    let ui = d.ui();
    crate::share_dialog::open(&d.h, &ui, crate::share_dialog::Mode::Edit(id.clone()));
    d.until("the folders", |d| d.has("Photos"));
    shoot(&mut d, "11-share-dialog-dark");
    d.key(Key::Escape, Modifiers::NONE);
    theme(&mut d, "light");
    shoot(&mut d, "12-files-grid-light");
    d.click("Settings");
    d.click("General");
    shoot(&mut d, "13-settings-general-light");
    d.key(Key::Escape, Modifiers::NONE);
    let w = d.win();
    d.host
        .user_resize(w, remedy_ui::core::Size::new(760.0, 640.0));
    shoot(&mut d, "14-narrow-light");
    d.host
        .user_resize(w, remedy_ui::core::Size::new(1280.0, 800.0));
    let _ = d.h.core.set_language("de");
    theme(&mut d, "dark");
    d.until("German", |d| d.has("Ordner freigeben"));
    shoot(&mut d, "15-files-german-dark");
    let s = crate::i18n::t(&d.ui(), "Settings");
    d.click(&s);
    let n = crate::i18n::t(&d.ui(), "Network");
    d.click(&n);
    shoot(&mut d, "16-settings-network-german-dark");
}
